# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.collectors.cputemp import _temp_snapshot_read
from backend.infrastructure.collectors.disks import _disk_idle
from backend.infrastructure.collectors.disks import get_disk_temps_cached
from backend.infrastructure.fans.enumerate import _enumerate_fans
from backend.infrastructure.fans.enumerate import _select_temp_fans
from backend.infrastructure.fans.fcs import _fcs_has_board_config
from backend.infrastructure.fans.fcs import _fcs_running_cached
from backend.infrastructure.fans.hwmon import _fan_read_raw
from backend.infrastructure.fans.hwmon import _fan_set_enable
from backend.infrastructure.fans.hwmon import _fan_write_raw
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
import os


# ===================== 温控曲线配置套用（两套对称，去重）=====================
def _apply_temp_curve(cfg, data, recover_max=100):
    """把 HTTP 配置体套用到 cfg（disk_temp / sys_temp 共用）。返回错误响应或 None。"""
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    for k in ("start_temp", "full_temp", "min_pwm", "max_pwm"):
        if k in data:
            try:
                v = float(data[k])
            except (TypeError, ValueError):
                return ({"ok": False, "error": k + " 需为数字"}), 400
            if k in ("min_pwm", "max_pwm") and (v < 0 or v > 100):
                return ({"ok": False, "error": k + " 需在 0~100"}), 400
            cfg[k] = v
    if "recover_temp" in data:
        try:
            rv = float(data["recover_temp"])
        except (TypeError, ValueError):
            return ({"ok": False, "error": "recover_temp 需为数字"}), 400
        if rv < 0 or rv > recover_max:
            return ({"ok": False, "error": "recover_temp 需在 0~%d" % recover_max}), 400
        cfg["recover_temp"] = rv
    if "controlled_fans" in data:
        cf = data["controlled_fans"]
        if cf == "all":
            cfg["controlled_fans"] = "all"
        elif isinstance(cf, list):
            norm = []
            for pair in cf:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    try:
                        norm.append([str(pair[0]), int(pair[1])])
                    except (TypeError, ValueError):
                        return ({"ok": False, "error": "controlled_fans 每项需为 [hwmon, idx]"}), 400
                else:
                    return ({"ok": False, "error": "controlled_fans 每项需为 [hwmon, idx]"}), 400
            cfg["controlled_fans"] = norm
        else:
            return ({"ok": False, "error": "controlled_fans 需为 'all' 或 [[hwmon,idx],...]"}), 400
    if "curve" in data:
        # 自定义温度→PWM 曲线：[[温度, 占空比], ...]，至少 2 点；缺失时回退 start/full 线性
        curve = data["curve"]
        if not isinstance(curve, list):
            return ({"ok": False, "error": "curve 需为数组"}), 400
        norm = []
        for p in curve:
            if not (isinstance(p, (list, tuple)) and len(p) == 2):
                return ({"ok": False, "error": "curve 每项需为 [温度, 占空比]"}), 400
            try:
                t = float(p[0]); pw = float(p[1])
            except (TypeError, ValueError):
                return ({"ok": False, "error": "curve 温度/占空比需为数字"}), 400
            if pw < 0 or pw > 100:
                return ({"ok": False, "error": "curve 占空比需在 0~100"}), 400
            norm.append([t, pw])
        norm.sort(key=lambda x: x[0])
        cfg["curve"] = norm
    st, ft, rt = cfg.get("start_temp"), cfg.get("full_temp"), cfg.get("recover_temp")
    if ft is not None and st is not None and ft <= st:
        return ({"ok": False, "error": "full_temp 必须大于 start_temp"}), 400
    if rt is not None and st is not None and rt >= st:
        return ({"ok": False, "error": "recover_temp 必须小于 start_temp"}), 400
    return None

def _fan_auto_pwm(cpu_temp):
    # nasdash 自带保守温控曲线（系统风扇服务不在时接管）
    pts = [(45, 90), (60, 140), (75, 204), (80, 255)]
    if cpu_temp is None:
        raw = pts[0][1]
    elif cpu_temp <= pts[0][0]:
        raw = pts[0][1]
    elif cpu_temp >= pts[-1][0]:
        raw = pts[-1][1]
    else:
        raw = pts[0][1]
        for i in range(len(pts) - 1):
            t0, p0 = pts[i]
            t1, p1 = pts[i + 1]
            if cpu_temp <= t1:
                r = (cpu_temp - t0) / (t1 - t0)
                raw = int(p0 + r * (p1 - p0))
                break
    # clamp 30%~70%（raw 76~178），防止 auto 狂转（旧机 IT87 曾因此满速）
    return max(76, min(178, raw))

def _fan_smooth_step(hwmon, idx, target, snap=False):
    _fan_set_enable(hwmon, idx, 1)   # 接管写 pwm 前确保软件控（enable=1）；否则硬件 enable=2 时写 pwm 被内核忽略→全速
    if snap:
        # 接管总开关刚打开：直接到位，一个 tick 内把风扇拉到温度目标，让面板点一下立刻看到转速变化。
        _fan_write_raw(hwmon, idx, target)
        return
    cur = _fan_read_raw(hwmon, idx)
    if cur is None:
        return
    diff = target - cur
    if abs(diff) <= 2:  # deadzone，避免抖动
        if cur != target:
            _fan_write_raw(hwmon, idx, target)
        return
    # 每 tick 最多变 30（≈ 20%/秒）：手动拉滑块/温度联动更跟手；接管开关走 snap 直接到位，不靠这里缓变。
    step = 30 if abs(diff) > 30 else abs(diff)
    _fan_write_raw(hwmon, idx, cur + (step if diff > 0 else -step))

# ===================== 风扇：硬盘温度控制（disk_temp）=====================
# 论坛需求（服务器/硬盘多/风扇多场景）：用指定硬盘温度驱动风扇——
# 如设置若干硬盘，40°C 开转、60°C 全速、硬盘休眠则停转。nasdash 增量支持，不替换现有 IT87/NCT 温控。
FAN_DISK_TEMP_FILE = os.path.join(_config_dir(), "fan_disk_temp.json")

def _load_fan_disk_temp():
    """读取硬盘温度控风扇配置（缺省关闭）。"""
    defaults = {
        "enabled": False,
        "disks": [],                 # 监控的硬盘 device，如 ["/dev/sda","/dev/sdb"]
        "start_temp": 40,            # 低于此温度 → 停转（开转阈值）；盘温重新 ≥ 此值才重新接管
        "full_temp": 60,             # 达到此温度 → 全速（max_pwm，默认 100=满转）
        "min_pwm": 30,               # 开转时最低占空比（%）
        "max_pwm": 100,              # 全速占空比（%）；full_temp 档即此值，默认 100=全速
        "recover_temp": 35,          # 盘温低于此值 → 受控风扇交还主板/内核自动控速（滞回，须 < start_temp）
        "sleep_stop": True,          # 所有监控盘休眠 → 风扇停转
        "idle_minutes": 5,           # 监控盘连续无读写满此分钟数也停转风扇（与休眠二选一满足即停，解决「设了休眠风扇却一直转」）
        "controlled_fans": "all",    # "all" 或 [[hwmon,idx],...]
    }
    d = _load_json_file(FAN_DISK_TEMP_FILE, None)
    if isinstance(d, dict):
        for k in defaults:
            if k in d:
                defaults[k] = d[k]
    return defaults

def _save_fan_disk_temp(cfg):
    return _save_json_file(FAN_DISK_TEMP_FILE, cfg)

# ===================== 风扇：主板/CPU 温度控制（sys_temp）=====================
# 与 disk_temp 对称的另一套「温度曲线控速」：温度源来自 CPU 封装温度或主板温度，
# 同样用 start/full/recover/min/max + 受控风扇 的滞回曲线。两套互不干扰，可分别接管不同风扇
# （如 CPU 风扇交给 sys_temp 按 CPU 温度控，机箱风扇交给 disk_temp 按硬盘温度控）。
FAN_SYS_TEMP_FILE = os.path.join(_config_dir(), "fan_sys_temp.json")

def _load_fan_sys_temp():
    """读取主板/CPU 温度控风扇配置（缺省关闭）。"""
    defaults = {
        "enabled": False,
        "source": "cpu",             # cpu=CPU 封装温度(coretemp Package)；mb=主板温度(it87/nct systin)
        "start_temp": 45,            # 低于此温度 → 停转（开转阈值）；温度重新 ≥ 此值才重新接管
        "full_temp": 70,             # 达到此温度 → 全速（max_pwm，默认 100=满转）
        "min_pwm": 30,               # 开转时最低占空比（%）
        "max_pwm": 100,              # 全速占空比（%）；full_temp 档即此值，默认 100=全速
        "recover_temp": 40,          # 温度低于此值 → 受控风扇交还主板/内核自动控速（滞回，须 < start_temp）
        "controlled_fans": "all",    # "all" 或 [[hwmon,idx],...]
    }
    d = _load_json_file(FAN_SYS_TEMP_FILE, None)
    if isinstance(d, dict):
        for k in defaults:
            if k in d:
                defaults[k] = d[k]
    return defaults

def _save_fan_sys_temp(cfg):
    return _save_json_file(FAN_SYS_TEMP_FILE, cfg)

def _fan_read_sys_temp(source):
    """温度源单值（统一快照读取，~2s 刷新；控速线程/状态接口不再各自跑 sensors -j）。
    source='cpu'  → CPU 封装温度（coretemp 核心 max / AMD Tdie，见 _parse_cpu_temp）；
    source='mb'   → 主板温度（SYSTIN 优先，见 _parse_mb_temp）；
    source='raid' → 阵列卡芯片温度（storcli 读；没装卡 / 卡被摘掉时返回 None）。
    读不到返回 None（调用方按「无值」处理，不会误判成 0）。"""
    src = (source or "cpu").lower()
    snap = _temp_snapshot_read()
    if src == "mb":
        return snap.get("mb_temp")
    if src == "raid":
        return snap.get("raid_temp")
    return snap.get("cpu_temp")

def _fan_curve_pwm(T, cfg, default_min, default_max):
    """自定义温度→PWM 曲线（分段线性）。cfg["curve"]=[[temp,pwm],...]（已按温度升序）。
    返回 raw(0~255) 或 None（曲线无效/缺失→交由调用方回退线性）。"""
    pts = cfg.get("curve")
    if not isinstance(pts, list) or len(pts) < 2:
        return None
    try:
        pts = sorted([(float(p[0]), float(p[1])) for p in pts if isinstance(p, (list, tuple)) and len(p) == 2], key=lambda x: x[0])
    except Exception:
        return None
    if not pts:
        return None
    if T is None:
        return None
    mn = float(cfg.get("min_pwm", default_min))
    mx = float(cfg.get("max_pwm", default_max))
    start = float(cfg.get("start_temp", pts[0][0]))
    if T < start:
        return 0
    if T >= pts[-1][0]:
        return round(min(max(pts[-1][1], mn), mx) / 100 * 255)
    for i in range(1, len(pts)):
        if T <= pts[i][0]:
            t0, p0 = pts[i-1]; t1, p1 = pts[i]
            frac = (T - t0) / (t1 - t0) if t1 > t0 else 0
            pw = p0 + frac * (p1 - p0)
            return round(min(max(pw, mn), mx) / 100 * 255)
    return round(min(max(pts[0][1], mn), mx) / 100 * 255)

def _fan_sys_temp_pwm(T, cfg):
    """按单值温度 T 算目标 raw(0~255)。优先自定义曲线；否则 start/full 线性。"""
    curve_raw = _fan_curve_pwm(T, cfg, 30, 100)
    if curve_raw is not None:
        return curve_raw
    start = float(cfg.get("start_temp", 45))
    full = float(cfg.get("full_temp", 70))
    minp = float(cfg.get("min_pwm", 30))
    maxp = float(cfg.get("max_pwm", 100))
    if T is None:
        return None
    if T < start:
        # 方案A（v2.3.2）：低于开转温度维持最低占空 min_pwm，不交还主板（避免 DIY 主板拉满 100%）
        return round(minp / 100 * 255)
    if T >= full:
        return round(maxp / 100 * 255)
    r = (T - start) / (full - start)
    raw = minp + r * (maxp - minp)
    return round(raw / 100 * 255)

# 主板/CPU 温控滞回状态：None=未初始化, True=nasdash 接管控速, False=已交还主板自动
_st_engaged = {"v": None}

def _fan_sys_temp_decision(T, cfg):
    """主板/CPU 温控决策。返回 (action, pwm)：
      "control" → 接管，pwm 为目标 raw（低于开转温度时维持最低占空 min_pwm，不交还主板）
      "release" → 读不到温度（安全兜底，交还主板/内核自动控速）
    说明同 _fan_disk_temp_decision：有效温度时始终接管，低于开转温度维持最低占空而非交还
    （避免无 FCS 主板被 BIOS 拉满 100% 全速）。"""
    global _st_engaged
    if T is None:
        _st_engaged["v"] = False
        return ("release", None)
    _st_engaged["v"] = True
    return ("control", _fan_sys_temp_pwm(T, cfg))

# NVMe 专用温度量纲。机械盘与 NVMe 的"正常/该散热"温区完全不同：
#   机械盘 40°C 已偏热、50°C 需警惕；NVMe 53°C 属完全正常，70~75°C 才触发厂商降频保护。
# 早期两者共用 start_temp(默认 40°C)，导致任何一块 NVMe 都恒高于阈值，
# 于是"硬盘全部休眠即停转"被永久判定为 False——用户勾了停转却发现风扇一直转。
# 另一面也要留安全阀：装了「主动式 M.2 散热器」（带小风扇、接主板风扇口）的机器，
# NVMe 真烧起来时风扇仍需响应，故不是直接排除，而是换算到 NVMe 自己的量纲。
NVME_START_TEMP = 65.0   # NVMe 开始需要主动散热的温度
NVME_FULL_TEMP = 80.0    # NVMe 需全力散热的温度（接近多数消费级盘的降频/告警线）

def _nvme_temp_as_hdd(t, start, full):
    """把 NVMe 温度换算成等效的机械盘温度，便于与其他盘统一比较/取最大值。
    低于 NVME_START_TEMP 返回 None（不参与，视为无需风扇介入）；
    NVME_START→start、NVME_FULL→full 线性映射，使风扇响应强度与"离降频线还有多远"对齐。"""
    if not isinstance(t, (int, float)) or t < NVME_START_TEMP:
        return None
    if t >= NVME_FULL_TEMP:
        return full
    r = (t - NVME_START_TEMP) / (NVME_FULL_TEMP - NVME_START_TEMP)
    return start + r * (full - start)

def _all_monitored_idle(valid, cfg):
    """判断"可停转风扇"：所有可休眠盘都已休眠(或连续无 I/O 满 idle_minutes)，且不可休眠盘
    (SAS 阵列企业盘/NVMe/SATA·SAS 固态盘)温度都低于启动阈值。SAS 企业盘与固态盘厂商锁死/
    无机械轴不休眠，不参与"全部休眠"判断，但它们若高温仍需散热(安全兜底)，此时不停转、
    交给温度曲线控速。
    - sleep_stop 关 → 永不停转
    - 无任何可休眠盘(如纯 SAS/NVMe/SSD 环境) → 永不因休眠停转，交给温度曲线
    - 任一可休眠盘未休眠且仍有 I/O（未满 idle_minutes）→ 不停转
    """
    if not bool(cfg.get("sleep_stop", True)):
        return False
    idle_min = float(cfg.get("idle_minutes", 5))
    sleepable = [s for s in valid if not s.get("no_sleep")]
    if not sleepable:
        return False
    for s in sleepable:
        if s.get("asleep"):
            continue
        if _disk_idle(s.get("dev"), idle_min):
            continue
        return False  # 该盘既未休眠、也未空闲够久 → 不停转
    start = float(cfg.get("start_temp", 40))
    for s in valid:
        if not s.get("no_sleep"):
            continue
        t = s.get("temp")
        if not isinstance(t, (int, float)):
            continue
        # NVMe 按自己的量纲判定（65°C 起）：机械盘的 40°C 对 NVMe 只是常温，
        # 若共用会让本函数恒返回 False，等于永久禁用「休眠停转」。
        thr = NVME_START_TEMP if s.get("is_nvme") else start
        if t >= thr:
            return False  # 有不休眠的盘确实偏热（SAS 企业盘/固态），仍需散热，不停转
    return True

def _disk_source_max_temp(valid, start, full):
    """取监控盘的「等效最热温度」，作为硬盘温度源的单值 T。
    机械盘 / SATA·SAS 固态用原始温度；NVMe 先换算到机械盘量纲（低于 65°C 直接不参与）。
    否则常年 50°C 出头的 M.2 固态会稳坐"最热盘"，让机箱风扇的转速由一块它根本吹不到的盘决定，
    真正需要散热的机械盘反而说了不算。"""
    temps = []
    for s in valid:
        t = s.get("temp")
        if not isinstance(t, (int, float)):
            continue
        if s.get("is_nvme"):
            t = _nvme_temp_as_hdd(t, start, full)
            if t is None:
                continue
        temps.append(t)
    return max(temps) if temps else None

def _fan_disk_temp_pwm(states, cfg):
    """按硬盘温度算目标 raw(0~255)。
    - 所有监控盘休眠且 sleep_stop → 0（停转）
    - 优先自定义温度→PWM 曲线；否则取最热盘温度 T：T<start → 0；start≤T<full → min~max 线性；T≥full → max
    """
    valid = [s for s in (states or {}).values() if isinstance(s, dict)]
    if not valid:
        return None
    if _all_monitored_idle(valid, cfg):
        return 0
    start = float(cfg.get("start_temp", 40))
    full = float(cfg.get("full_temp", 60))
    minp = float(cfg.get("min_pwm", 30))
    maxp = float(cfg.get("max_pwm", 70))
    T = _disk_source_max_temp(valid, start, full)
    if T is None:
        # 区分两种「没有可用温度」：
        #   完全读不到 → 保守兜底转起来（怕真热却不知道）；
        #   读得到但都低于各自开转阈值（例如只监控了一块 55°C 的 NVMe）→ 无需风扇，给 0。
        if not any(isinstance(s.get("temp"), (int, float)) for s in valid):
            if cfg.get("curve"):
                return _fan_curve_pwm(None, cfg, 30, 70)
            return round(float(cfg.get("min_pwm", 30)) / 100 * 255)
        return 0
    curve_raw = _fan_curve_pwm(T, cfg, 30, 70)
    if curve_raw is not None:
        return curve_raw
    if T < start:
        # 方案A（v2.3.2）：低于开转温度维持最低占空 min_pwm，不交还主板（避免 DIY 主板拉满 100%）
        return round(minp / 100 * 255)
    if T >= full:
        return round(maxp / 100 * 255)
    r = (T - start) / (full - start)
    raw = minp + r * (maxp - minp)
    return round(raw / 100 * 255)

# 硬盘温控滞回状态：None=未初始化, True=nasdash 接管控速, False=已交还主板自动
_dt_engaged = {"v": None}


def _fan_disk_temp_decision(states, cfg):
    """硬盘温控决策。返回 (action, pwm)：
      "control" → 接管，pwm 为目标 raw（低于开转温度时维持最低占空 min_pwm，不交还主板）
      "release" → 完全读不到温度（安全兜底，交还主板/内核自动控速）
    说明：只要能读到有效盘温，nasdash 始终接管——避免无 FCS 的主板（如 DIY B360M）在
    交还后被 BIOS Thermal Cruise 拉满 100% 全速。低于开转温度不再停转/交还，而是维持最低
    占空（用户设的 min_pwm，默认 30%），既安静又保留基础气流。
    休眠停转（sleep_stop + 所有盘 idle 且无不可休眠盘偏热）仍走 _all_monitored_idle → 0（停转）。"""
    global _dt_engaged
    valid = [s for s in (states or {}).values() if isinstance(s, dict)]
    if not valid:
        _dt_engaged["v"] = False
        return ("release", None)
    if _all_monitored_idle(valid, cfg):
        # 所有可休眠盘休眠且无不可休眠盘偏热 → 停转（显式休眠停转功能，nasdash 接管写 0）
        _dt_engaged["v"] = False
        return ("control", 0)
    T = _disk_source_max_temp(valid, float(cfg.get("start_temp", 40)), float(cfg.get("full_temp", 60)))
    if T is None:
        # 完全读不到温度 → 安全兜底交还主板/内核自动（怕真热却不知道）
        _dt_engaged["v"] = False
        return ("release", None)
    _dt_engaged["v"] = True
    return ("control", _fan_disk_temp_pwm(states, cfg))

# ===================== 风扇：逐风扇温度联动规则（fan_rules）=====================
# 论坛需求（huhaibo820）：两台硬盘风扇跟同一组硬盘温度走，但各用不同曲线
# （一路 30–45°C、一路 45–60°C）。即「在风扇处设置温度源」——每台风扇可单独选温度源
# （硬盘聚合 / CPU / 主板）并配自己的曲线。
# 设计（向后兼容，叠加式）：
#   - 默认规则由旧的 disk_temp / sys_temp 两个全局面板派生（谁 enabled + 接管哪些风扇）。
#   - fan_rules.json 存「逐风扇覆盖」：某台风扇一旦单独设置，即以它为准，压过全局默认。
#   - 硬盘温度源共享 disk_temp 的「监控硬盘 + 休眠/空闲停转」设置（同阵列同温源）。
FAN_RULES_FILE = os.path.join(_config_dir(), "fan_rules.json")
_FAN_RULE_SOURCES = ("disk", "cpu", "mb", "raid")

def _load_fan_rules_raw():
    """读取用户保存的逐风扇覆盖规则；无有效文件返回 None（表示未自定义、沿用全局默认）。"""
    d = _load_json_file(FAN_RULES_FILE, None)
    if isinstance(d, dict) and isinstance(d.get("rules"), dict):
        return d
    return None

def _save_fan_rules(d):
    return _save_json_file(FAN_RULES_FILE, d)

def _rule_from_cfg(source, cfg):
    """把全局 disk_temp / sys_temp 配置转成一条逐风扇规则。"""
    r = {
        "enabled": True,
        "source": source,
        "start_temp": cfg.get("start_temp", 40),
        "full_temp": cfg.get("full_temp", 60),
        "min_pwm": cfg.get("min_pwm", 30),
        "max_pwm": cfg.get("max_pwm", 100),
        "recover_temp": cfg.get("recover_temp", 35),
    }
    if cfg.get("curve"):
        r["curve"] = cfg["curve"]
    return r

def _derive_rules_from_legacy():
    """从旧的 disk_temp / sys_temp 全局面板派生逐风扇默认规则（sys 优先于 disk）。"""
    st = _load_fan_sys_temp()
    dt = _load_fan_disk_temp()
    all_fans = _enumerate_fans()
    sys_claimed, disk_claimed = _select_temp_fans(all_fans, st, dt)
    rules = {}
    for (h, i) in sys_claimed:
        rules["%s::%d" % (h, i)] = _rule_from_cfg(st.get("source", "cpu"), st)
    for (h, i) in disk_claimed:
        rules["%s::%d" % (h, i)] = _rule_from_cfg("disk", dt)
    return rules

def _effective_fan_rules():
    """最终生效的逐风扇规则：先由旧全局面板派生默认，再叠加用户逐风扇覆盖。
    覆盖项 value=None 表示「显式清除该风扇的规则」（回到手动/BIOS）。"""
    rules = _derive_rules_from_legacy()
    saved = _load_fan_rules_raw()
    if saved:
        for k, r in (saved.get("rules") or {}).items():
            if r is None:
                rules.pop(k, None)
            elif isinstance(r, dict) and r.get("enabled", True):
                rules[k] = r
            else:
                rules.pop(k, None)  # enabled=False → 该风扇不温控
    # hwmon 路径漂移兜底：飞牛重启后 /sys/class/hwmon/hwmonN 编号会变化
    # (如 hwmon4<->hwmon3)，已保存规则的 key 用的是旧路径，精确匹配会整组失效、
    # 表现为「部分风扇温控丢失 / 只控一组」。按通道序号(idx)兜底：当前枚举到的
    # (hwmon,idx) 若精确 key 缺失、但存在同 idx 的旧 key，则映射到当前精确 key。
    # 与 fan_labels 的 idx 兜底策略一致。
    try:
        _enum = _enumerate_fans()
        _fixed = {}
        for (_hw, _idx) in _enum:
            _ek = "%s::%d" % (_hw, _idx)
            if _ek in rules:
                continue
            _suf = "::%d" % _idx
            for _k, _r in rules.items():
                if _k.endswith(_suf):
                    _fixed[_ek] = _r
                    break
        rules.update(_fixed)
    except Exception:
        pass
    return rules

def _disk_source_state(dt_cfg):
    """硬盘温度源当前状态（供所有 source=disk 的风扇共享，每轮只算一次）。
    返回 (all_idle, T, has_disks)：
      all_idle=True → 监控盘全部休眠/空闲 → 交还自动
      T            → 最热监控盘温度（°C），无读数为 None
      has_disks    → 是否配置了监控盘（无则 disk 源风扇不参与温控）"""
    devs = dt_cfg.get("disks") or []
    if not devs:
        return (False, None, False)
    states = get_disk_temps_cached(devs)
    valid = [s for s in states.values() if isinstance(s, dict)]
    # v2.3.3：阵列卡 SAS/阵列物理盘温度经 storcli 采集进 _TEMP_SNAP["raid_drives"]，
    # 必须并入风扇的硬盘温度源——RAID5/RAID0/RAID10 等真实阵列场景下物理盘不暴露 /dev 节点、
    # smartctl 读不到，否则「风扇按硬盘温度」只控到主板 SATA 盘、SAS 阵列盘说了不算；
    # 读不到时还会被交还 BIOS，在无 FCS 的 DIY 主板（如 B360M）上被 Thermal Cruise 拉满 100% 狂转
    #（论坛反馈 + 158 实机一夜风扇满速）。按序列号已去重，OS 可见盘不会重复计入。
    try:
        _snap = _temp_snapshot_read()
        for _rd in (_snap.get("raid_drives") or []):
            _t = _rd.get("temp")
            if not isinstance(_t, (int, float)):
                continue
            _st = {
                "dev": "raid:" + str(_rd.get("slot")),
                "temp": _t,
                "asleep": False,
                "no_sleep": True,   # SAS 企业盘永不休眠，不参与"全部休眠→停转"
                "is_nvme": False,
                "serial": _rd.get("serial") or "",
                "intf": (str(_rd.get("intf") or "").upper()),
            }
            valid.append(_st)
            states[_st["dev"]] = _st
    except Exception:
        pass
    if not valid:
        return (False, None, True)
    if _all_monitored_idle(valid, dt_cfg):
        # 全部监控盘休眠/空闲 → 交还自动控速（停转）。但仍返回真实最热盘温供预览展示，
        # 停转决策由 all_idle 标志单独控制（get_fan_status 中 _raw=0 if _ridle），
        # 故此处返回真实 T 不会让风扇在空闲时反而转起来。
        T = _disk_source_max_temp(valid,
                                  float(dt_cfg.get("start_temp", 40)),
                                  float(dt_cfg.get("full_temp", 60)))
        return (True, T, True)
    # NVMe 先换算到机械盘量纲再参与取最热（低于 65°C 不参与），
    # 否则常年 50°C 出头的 M.2 固态会恒定当选"最热盘"，把机箱风扇一直顶在中高速。
    T = _disk_source_max_temp(valid,
                              float(dt_cfg.get("start_temp", 40)),
                              float(dt_cfg.get("full_temp", 60)))
    return (False, T, True)

def _fan_rule_pwm(T, rule):
    """按单值温度 T + 该风扇自身规则算目标 raw(0~255)。
    active_mode 控制走哪套（3按钮显式选择，根治「不知道当前跑的是哪套」）：
      'curve'  → 自定义曲线；曲线无效/缺失时回退到线性（不报错）
      'linear' → 开转/全速 线性；忽略自定义曲线（即便设了也走线性）
      未设/None → 旧行为：曲线优先，线性兜底（兼容老配置）"""
    minp = float(rule.get("min_pwm", 30))
    maxp = float(rule.get("max_pwm", 100))
    active_mode = rule.get("active_mode")
    if active_mode == "curve":
        curve_raw = _fan_curve_pwm(T, rule, minp, maxp)
        if curve_raw is not None:
            return curve_raw
        # 曲线无效/缺失：回退线性（不写错，避免显式选曲线但无曲线时 0 输出）
    elif active_mode == "linear":
        pass  # 直接走线性
    else:
        # 未设/旧配置：曲线优先（保持向后兼容）
        curve_raw = _fan_curve_pwm(T, rule, minp, maxp)
        if curve_raw is not None:
            return curve_raw
    if T is None:
        return None
    start = float(rule.get("start_temp", 40))
    full = float(rule.get("full_temp", 60))
    if T < start:
        # 方案A（v2.3.2）：低于开转温度不再停转为 0，而是维持最低占空 min_pwm，
        # 避免「交还主板/内核自动」在无 FCS 的 DIY 主板上被 BIOS Thermal Cruise 拉满 100% 全速。
        return round(minp / 100 * 255)
    if T >= full:
        return round(maxp / 100 * 255)
    r = (T - start) / (full - start) if full > start else 0
    return round((minp + r * (maxp - minp)) / 100 * 255)

# 逐风扇温控滞回状态：{(hwmon, idx): True/False/None}
_FAN_ENGAGED = {}

def _fan_rule_decision(key, rule, T, all_idle=False, source=None):
    """逐风扇温控滞回状态机，语义与旧的两套全局状态机一致但按风扇独立记忆。
    返回 (action, raw)：control=按曲线接管 / release=交还自动 / hold=已释放且在滞回区不写。
    stop_below_start=True 时：温度低于开转温度（或硬盘空闲=冷态）直接强制 0%（保持在 nasdash
    软件接管、不交还自动），实现「低于开转温度即停转」。读不到温度仍保守交还自动（避免未知
    高温时风扇熄火，比强制停转更安全）。"""
    start = float(rule.get("start_temp", 40))
    recover = float(rule.get("recover_temp", start - 5))
    if recover >= start:
        recover = start - 5
    stop_below = bool(rule.get("stop_below_start", False))
    # 接管总开关开着、且本机无可用 FCS（fnOS 原生控温）时，绝不把任何风扇交还 BIOS：
    # 无 FCS 的 DIY 主板（如 B360M）交还后会被 Thermal Cruise 拉满 100% 狂转。
    # 因此无论温度源（硬盘/CPU/主板/阵列卡/混合）读不到温度还是全空闲，都维持最低占空保底气流。
    # 仅「开关关闭」或「有可用 FCS 兜底」（FCS 接管控温，安全）时才交还自动。
    _no_fcs = not (_fcs_running_cached() and _fcs_has_board_config())
    if all_idle:
        _FAN_ENGAGED[key] = False
        if stop_below:
            # 勾选「低温停转」：空闲=冷态，强制 0%（保持在 nasdash 软件接管、不交还自动）
            return ("control", 0)
        if _no_fcs:
            # 无 FCS 主板（B360M）：全盘空闲维持最低占空，绝不交还 BIOS 狂转
            minp = float(rule.get("min_pwm", 30))
            return ("control", round(minp / 100 * 255))
        return ("release", None)
    if T is None:
        _FAN_ENGAGED[key] = False
        if _no_fcs:
            # 无 FCS 主板（B360M）：任何温度源读不到都维持最低占空，绝不交还 BIOS 拉满 100%
            minp = float(rule.get("min_pwm", 30))
            return ("control", round(minp / 100 * 255))
        # 有可用 FCS：读不到温度交还自动，由 FCS 接管控温（安全兜底）
        return ("release", None)
    if T < start:
        if stop_below:
            # 低温强制停转：直接写 0%（保持在 nasdash 软件接管，不交还自动）
            _FAN_ENGAGED[key] = False
            return ("control", 0)
        # 方案A（v2.3.2）：低于开转温度且不勾「低温停转」→ nasdash 仍接管，维持最低占空 min_pwm，
        # 不再交还主板/内核自动。无 FCS 的 DIY 主板（如 B360M）交还后会被 BIOS Thermal Cruise
        # 拉满 100% 全速，与「低温应安静」的直觉相反；常接管既安静又保留基础气流。
        _FAN_ENGAGED[key] = True
        return ("control", _fan_rule_pwm(T, rule))
    # 已达开转温度：接管并按曲线控速
    _FAN_ENGAGED[key] = True
    return ("control", _fan_rule_pwm(T, rule))

def _resolve_rule_temp(src, cpu_T, mb_T, disk_T, disk_all_idle, disk_has, raid_T=None):
    """按温度源解析出 (T, all_idle)，供 _fan_rule_decision 使用。
    src 支持：
      disk                → 硬盘温度（disk_has 为假时返回 (None, False) 表示本轮跳过）
      cpu / mb            → CPU / 主板温度
      raid                → 阵列卡芯片温度（v2.3.0 新增；读不到返回 (None, False)）
      combo_max:cpu,mb    → 取 CPU 与主板温度的【较大值】
      combo_avg:cpu,mb    → 取 CPU 与主板温度的【平均值】
    子项可递归（combo 里还能套 sensor: 等）。任一子源不可用时忽略该子源；
    全部不可用时返回 (None, False)。移植自 guan-ry/FanControlServerApp 的 resolveSourceTemp。
    彻底解决「选了主板 CPU 就没了」的单选互斥痛点（论坛 huhaibo820 反馈）。
    raid_T 用默认参数（而非并进前 6 个位置参数）是为了不动既有调用点签名。"""
    src = (src or "disk").strip()
    if src == "disk":
        return (disk_T if disk_has else None, disk_all_idle)
    if src == "cpu":
        return (cpu_T, False)
    if src == "mb":
        return (mb_T, False)
    if src == "raid":
        return (raid_T, False)
    if src.startswith("combo_avg:") or src.startswith("combo_max:"):
        parts = [s.strip() for s in src.split(":", 1)[1].split(",") if s.strip()]
        vals = []
        for p in parts:
            if p == "cpu":
                vals.append(cpu_T)
            elif p == "mb":
                vals.append(mb_T)
            elif p == "disk":
                vals.append(disk_T if disk_has else None)
            elif p == "raid":
                vals.append(raid_T)
            else:
                t, _ = _resolve_rule_temp(p, cpu_T, mb_T, disk_T, disk_all_idle, disk_has, raid_T)
                vals.append(t)
        vals = [v for v in vals if isinstance(v, (int, float))]
        if not vals:
            return (None, False)
        if src.startswith("combo_max"):
            return (max(vals), False)
        return (sum(vals) / len(vals), False)
    return (None, False)


def _validate_fan_rule(r):
    """校验单条逐风扇规则，返回 (clean_rule|None, error|None)。value=None 表示删除该风扇规则。"""
    if r is None:
        return (None, None)
    if not isinstance(r, dict):
        return (None, "规则需为对象")
    src = r.get("source", "disk")
    if src not in _FAN_RULE_SOURCES and not (src.startswith("combo_max:") or src.startswith("combo_avg:")):
        return (None, "source 需为 disk / cpu / mb / raid 或 combo_max:/combo_avg: 组合（如 combo_max:cpu,mb）")
    clean = {"enabled": bool(r.get("enabled", True)), "source": src}
    # active_mode：3按钮显式控速方案（'linear' 或 'curve'）。
    # 未提供时根据「是否有曲线」推断：有曲线→'curve'，否则→'linear'（兼容老配置）。
    if "active_mode" in r and r["active_mode"] in ("linear", "curve"):
        clean["active_mode"] = r["active_mode"]
    else:
        clean["active_mode"] = "curve" if r.get("curve") else "linear"
    for k, lo, hi, dv in (("start_temp", 0, 110, 40), ("full_temp", 0, 120, 60),
                          ("min_pwm", 0, 100, 30), ("max_pwm", 0, 100, 100),
                          ("recover_temp", 0, 120, 35)):
        v = r.get(k, dv)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return (None, k + " 需为数字")
        if v < lo or v > hi:
            return (None, "%s 需在 %d~%d" % (k, lo, hi))
        clean[k] = v
    if clean["full_temp"] <= clean["start_temp"]:
        return (None, "全速温度必须大于开转温度")
    if clean["recover_temp"] >= clean["start_temp"]:
        return (None, "恢复自动温度必须小于开转温度")
    # 可选：低于开转温度即强制停转（默认关）
    if "stop_below_start" in r:
        clean["stop_below_start"] = bool(r["stop_below_start"])
    if "curve" in r and r["curve"]:
        curve = r["curve"]
        if not isinstance(curve, list):
            return (None, "curve 需为数组")
        norm = []
        for p in curve:
            if not (isinstance(p, (list, tuple)) and len(p) == 2):
                return (None, "curve 每项需为 [温度, 占空比]")
            try:
                t = float(p[0]); pw = float(p[1])
            except (TypeError, ValueError):
                return (None, "curve 温度/占空比需为数字")
            if pw < 0 or pw > 100:
                return (None, "curve 占空比需在 0~100")
            norm.append([t, pw])
        norm.sort(key=lambda x: x[0])
        clean["curve"] = norm
    return (clean, None)
