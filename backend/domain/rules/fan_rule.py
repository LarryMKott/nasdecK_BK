"""风扇温控纯规则：温度→PWM 曲线、NVMe 量纲换算、接管决策核。

忠实副本来源：backend/fans/rules.py（v2.3.3）的纯计算函数。与存量实现的差异（仅限两种，见 rules/__init__.py）：
  1. 滞回/接管标志不再写模块级全局（_st_engaged/_dt_engaged），改为 FanDecision.engaged 随值返回；
  2. 「全部监控盘休眠/空闲」判定（_all_monitored_idle，内部走磁盘 I/O）以 all_idle 参数传入，
     infrastructure 包装方负责实算后传入（判定逻辑本体仍留基础设施侧）。
"""
from backend.domain.entities.fan import FanDecision

# NVMe 专用温度量纲。机械盘与 NVMe 的"正常/该散热"温区完全不同：
#   机械盘 40°C 已偏热、50°C 需警惕；NVMe 53°C 属完全正常，70~75°C 才触发厂商降频保护。
# 早期两者共用 start_temp(默认 40°C)，导致任何一块 NVMe 都恒高于阈值，
# 于是"硬盘全部休眠即停转"被永久判定为 False——用户勾了停转却发现风扇一直转。
# 另一面也要留安全阀：装了「主动式 M.2 散热器」（带小风扇、接主板风扇口）的机器，
# NVMe 真烧起来时风扇仍需响应，故不是直接排除，而是换算到 NVMe 自己的量纲。
NVME_START_TEMP = 65.0   # NVMe 开始需要主动散热的温度
NVME_FULL_TEMP = 80.0    # NVMe 需全力散热的温度（接近多数消费级盘的降频/告警线）


def nvme_temp_as_hdd(t, start, full):
    """把 NVMe 温度换算成等效的机械盘温度，便于与其他盘统一比较/取最大值。
    低于 NVME_START_TEMP 返回 None（不参与，视为无需风扇介入）；
    NVME_START→start、NVME_FULL→full 线性映射，使风扇响应强度与"离降频线还有多远"对齐。"""
    if not isinstance(t, (int, float)) or t < NVME_START_TEMP:
        return None
    if t >= NVME_FULL_TEMP:
        return full
    r = (t - NVME_START_TEMP) / (NVME_FULL_TEMP - NVME_START_TEMP)
    return start + r * (full - start)


def fan_curve_pwm(T, cfg, default_min, default_max):
    """自定义温度→PWM 曲线（分段线性）。cfg["curve"]=[[temp,pwm],...]（已按温度升序）。
    返回 raw(0~255) 或 None（曲线无效/缺失→交由调用方回退线性）。"""
    pts = cfg.get("curve")
    if not isinstance(pts, list) or len(pts) < 2:
        return None
    try:
        pts = sorted([(float(p[0]), float(p[1])) for p in pts
                      if isinstance(p, (list, tuple)) and len(p) == 2], key=lambda x: x[0])
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
            t0, p0 = pts[i - 1]
            t1, p1 = pts[i]
            frac = (T - t0) / (t1 - t0) if t1 > t0 else 0
            pw = p0 + frac * (p1 - p0)
            return round(min(max(pw, mn), mx) / 100 * 255)
    return round(min(max(pts[0][1], mn), mx) / 100 * 255)


def sys_temp_pwm(T, cfg):
    """按单值温度 T 算目标 raw(0~255)。优先自定义曲线；否则 start/full 线性。"""
    curve_raw = fan_curve_pwm(T, cfg, 30, 100)
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


def sys_temp_decision(T, cfg) -> FanDecision:
    """主板/CPU 温控决策（原 _fan_sys_temp_decision，接管标志改随值返回）。
      "control" → 接管，pwm 为目标 raw（低于开转温度时维持最低占空 min_pwm，不交还主板）
      "release" → 读不到温度（安全兜底，交还主板/内核自动控速）
    说明同硬盘温控决策：有效温度时始终接管，低于开转温度维持最低占空而非交还
    （避免无 FCS 主板被 BIOS 拉满 100% 全速）。"""
    if T is None:
        return FanDecision(action="release", pwm=None, engaged=False)
    return FanDecision(action="control", pwm=sys_temp_pwm(T, cfg), engaged=True)


def disk_source_max_temp(valid, start, full):
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
            t = nvme_temp_as_hdd(t, start, full)
            if t is None:
                continue
        temps.append(t)
    return max(temps) if temps else None


def disk_temp_pwm(states, cfg, all_idle: bool):
    """按硬盘温度算目标 raw(0~255)（原 _fan_disk_temp_pwm；全闲置判定改由参数传入）。
    - 所有监控盘休眠且 sleep_stop（all_idle=True）→ 0（停转）
    - 优先自定义温度→PWM 曲线；否则取最热盘温度 T：T<start → minp；start≤T<full → 线性；T≥full → max
    """
    valid = [s for s in (states or {}).values() if isinstance(s, dict)]
    if not valid:
        return None
    if all_idle:
        return 0
    start = float(cfg.get("start_temp", 40))
    full = float(cfg.get("full_temp", 60))
    minp = float(cfg.get("min_pwm", 30))
    maxp = float(cfg.get("max_pwm", 70))
    T = disk_source_max_temp(valid, start, full)
    if T is None:
        # 区分两种「没有可用温度」：
        #   完全读不到 → 保守兜底转起来（怕真热却不知道）；
        #   读得到但都低于各自开转阈值（例如只监控了一块 55°C 的 NVMe）→ 无需风扇，给 0。
        if not any(isinstance(s.get("temp"), (int, float)) for s in valid):
            if cfg.get("curve"):
                return fan_curve_pwm(None, cfg, 30, 70)
            return round(float(cfg.get("min_pwm", 30)) / 100 * 255)
        return 0
    curve_raw = fan_curve_pwm(T, cfg, 30, 70)
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


def disk_temp_decision(states, cfg, all_idle: bool) -> FanDecision:
    """硬盘温控决策（原 _fan_disk_temp_decision；全闲置判定参数传入、接管标志随值返回）。
      "control" → 接管，pwm 为目标 raw（低于开转温度时维持最低占空 min_pwm，不交还主板）
      "release" → 完全读不到温度（安全兜底，交还主板/内核自动控速）
    说明：只要能读到有效盘温，nasdeck 始终接管——避免无 FCS 的主板（如 DIY B360M）在
    交还后被 BIOS Thermal Cruise 拉满 100% 全速。低于开转温度不再停转/交还，而是维持最低
    占空（用户设的 min_pwm，默认 30%），既安静又保留基础气流。
    休眠停转（sleep_stop + 所有盘 idle 且无不可休眠盘偏热）仍可停转 → control/0。"""
    valid = [s for s in (states or {}).values() if isinstance(s, dict)]
    if not valid:
        return FanDecision(action="release", pwm=None, engaged=False)
    if all_idle:
        # 所有可休眠盘休眠且无不可休眠盘偏热 → 停转（显式休眠停转功能，nasdeck 接管写 0）
        return FanDecision(action="control", pwm=0, engaged=False)
    T = disk_source_max_temp(valid, float(cfg.get("start_temp", 40)), float(cfg.get("full_temp", 60)))
    if T is None:
        # 完全读不到温度 → 安全兜底交还主板/内核自动（怕真热却不知道）
        return FanDecision(action="release", pwm=None, engaged=False)
    return FanDecision(action="control", pwm=disk_temp_pwm(states, cfg, all_idle), engaged=True)
