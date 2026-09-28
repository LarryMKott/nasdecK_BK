# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.collectors.cputemp import _temp_snapshot_read
from backend.infrastructure.collectors.disks import _list_all_disk_devs
from backend.infrastructure.fans.enumerate import _FAN_SNAP
from backend.infrastructure.fans.enumerate import _enumerate_fans
from backend.infrastructure.fans.fcs import _FCS_TAKEN
from backend.infrastructure.fans.fcs import _fan_start_ext_service
from backend.infrastructure.fans.fcs import _fan_stop_ext_service
from backend.infrastructure.fans.fcs import _fcs_disabled
from backend.infrastructure.fans.fcs import _fcs_has_board_config
from backend.infrastructure.fans.fcs import _fcs_installed_cached
from backend.infrastructure.fans.fcs import _fcs_running_cached
from backend.infrastructure.fans.hwmon import _fan_read_pwm_mode
from backend.infrastructure.fans.hwmon import _fan_release_auto
from backend.infrastructure.fans.labels import _apply_fan_aliases
from backend.infrastructure.fans.labels import _dedup_fan_names
from backend.infrastructure.fans.labels import _fan_label_for
from backend.infrastructure.fans.labels import _load_fan_labels
from backend.infrastructure.fans.modes import _FAN_CTRL_ENABLED
from backend.infrastructure.fans.rules import _disk_source_state
from backend.infrastructure.fans.rules import _effective_fan_rules
from backend.infrastructure.fans.rules import _fan_auto_pwm
from backend.infrastructure.fans.rules import _fan_read_sys_temp
from backend.infrastructure.fans.rules import _fan_rule_decision
from backend.infrastructure.fans.rules import _fan_rule_pwm
from backend.infrastructure.fans.rules import _fan_smooth_step
from backend.infrastructure.fans.rules import _load_fan_disk_temp
from backend.infrastructure.fans.rules import _resolve_rule_temp
from backend.infrastructure.config.paths import _config_dir
from backend.infrastructure.command.cmd_runner import read_file
from backend.infrastructure.state import FAN_LOCK
from backend.infrastructure.state import FAN_TARGETS
import json
import os
import time


def _fan_cpu_temp_cached():
    """CPU 封装温度（统一快照，~2s 刷新；已修正 coretemp temp1_input 虚高问题）。"""
    return _temp_snapshot_read().get("cpu_temp")

def _fan_ensure_all_claimed():
    """每轮兜底：把枚举到的、尚未被接管的可控风扇默认设为 auto 接管。
    解决硬重启时序（hwmon 晚于 nasdeck 自启注册、启动期枚举不全）与 fan_mode.json 不完整
    导致的「部分风扇失控、开机狂转」。已显式设为 manual 的风扇在 FAN_TARGETS 中会被跳过，不被覆盖。"""
    try:
        if not _FAN_CTRL_ENABLED:
            return
        # 同上：FCS 没真配置时不视为「系统在控」，nasdeck 该接管就接管，否则风扇会全速。
        if _fcs_running_cached() and _fcs_has_board_config():
            return
        enum = _enumerate_fans()
        with FAN_LOCK:
            for (hwmon, idx) in enum:
                if (hwmon, idx) not in FAN_TARGETS:
                    FAN_TARGETS[(hwmon, idx)] = {"mode": "auto", "target": None}
    except Exception:
        pass

def fan_smooth_loop():
    # daemon 线程：每 ~0.6s 把风扇当前 pwm 朝目标平滑过渡（常驻线程 tick + 缓变）
    while True:
        try:
            if not _FAN_CTRL_ENABLED:
                # 接管关闭：优先把控制权交还 fnOS 原生 FanControlServer（FCS）。
                # FCS 使用 enable=1 + pwm 手动控速；若我们再写 enable=2 会覆盖 FCS，
                # 使风扇被卡在最后的高位。因此 FCS 真的配置了风扇参数时只启动/保持它，
                # 不再写 enable=2；否则 nasdeck 直接停手，保持风扇当前手动值，
                # 避免某些主板 enable=2 后转速反而被拉得更高。
                if not _fcs_disabled() and _fcs_installed_cached() and _fcs_has_board_config():
                    if _FCS_TAKEN["v"]:
                        try: _fan_start_ext_service(); _FCS_TAKEN["v"] = False
                        except Exception: pass
                    if not _fcs_running_cached():
                        _fan_start_ext_service()
                time.sleep(0.6)
                continue
            # 自愈：每轮确保本机枚举到的每个可控风扇都被接管为 auto（除非用户显式设为 manual）。
            # 解决硬重启时序（hwmon 晚于 nasdeck 自启注册、启动期枚举不全）与 fan_mode.json 不完整
            # 导致的「部分风扇失控、开机狂转」。FCS 在控时不抢（交还原生控温）。
            _fan_ensure_all_claimed()
            # 接管总开关刚切到 ON：本 tick 一次性把风扇直接写到目标（见 _FAN_SNAP 标志），即时反馈给用户。
            _snap = False
            with _FAN_SNAP["lock"]:
                if _FAN_SNAP["v"]:
                    _snap = True
                    _FAN_SNAP["v"] = False
            with FAN_LOCK:
                overrides = dict(FAN_TARGETS)   # 每风扇手动/自动覆盖（仅用户经 UI 调过的风扇）
            all_fans = _enumerate_fans()          # 本机真实风扇全集（it87/nct）
            dt = _load_fan_disk_temp()            # 硬盘监控设置（监控盘 + 休眠/空闲停转，供 disk 源共享）
            rules = _effective_fan_rules()        # 逐风扇温控规则（旧全局面板派生 + 用户逐风扇覆盖）
            controlled = set()
            controlling_any = False   # 本周期是否真正在写 PWM（接管 FCS 的依据）
            # 逐风扇温控：每台风扇按各自「温度源 + 曲线」独立决策（论坛需求：同温源不同曲线）。
            # 温度源每种只算一次，多台同源风扇复用（硬盘温度采集较重，避免重复扫盘）。
            need_disk = any((r.get("source", "disk") == "disk") for r in rules.values())
            _need_combo = any((r.get("source") or "").startswith("combo") for r in rules.values())
            need_cpu = any((r.get("source") == "cpu") for r in rules.values()) or _need_combo
            need_mb = any((r.get("source") == "mb") for r in rules.values()) or _need_combo
            need_raid = any((r.get("source") == "raid") for r in rules.values()) or _need_combo
            disk_all_idle, disk_T, disk_has = (False, None, False)
            if need_disk:
                _dt_eff = dt
                if not dt.get("disks"):
                    # 逐风扇 disk 规则不依赖全局监控盘白名单：自动用本机全部盘作温度源
                    _all_devs = _list_all_disk_devs()
                    if _all_devs:
                        _dt_eff = dict(dt, disks=_all_devs)
                disk_all_idle, disk_T, disk_has = _disk_source_state(_dt_eff)
            cpu_T = _fan_read_sys_temp("cpu") if need_cpu else None
            mb_T = _fan_read_sys_temp("mb") if need_mb else None
            # 阵列卡芯片温度（v2.3.0）：读快照里的 raid_temp（由采集线程经 get_raid_card 填）。
            # 没装卡 / 卡被摘掉 / 采集失败时为 None —— 该风扇本轮交还自动，不会误判成 0。
            raid_T = _fan_read_sys_temp("raid") if need_raid else None
            for (hwmon, idx) in all_fans:
                rule = rules.get("%s::%d" % (hwmon, idx))
                if not rule:
                    continue
                src = rule.get("source", "disk")
                if src == "disk":
                    if not disk_has:
                        continue  # 未配置监控盘 → 该风扇本轮不温控（保持原样）
                    T, all_idle = disk_T, disk_all_idle
                else:
                    # cpu / mb / combo_max:cpu,mb / combo_avg:cpu,mb 统一经 _resolve_rule_temp 解析。
                    # 任一子源读数缺失则 T=None，由 _fan_rule_decision 保守交还自动（与旧逻辑一致）。
                    T, all_idle = _resolve_rule_temp(src, cpu_T, mb_T, disk_T, disk_all_idle, disk_has, raid_T)
                key = (hwmon, idx)
                # #4 修复（huhaibo820）：用户显式手动调速（FAN_TARGETS 该风扇 mode=manual）
                # 时，手动优先于温度联动——否则一旦设了温度联控速，手动滑块就完全失效。
                # 点「恢复自动」会把 mode 改回 auto/清空，届时温度联动重新生效。
                ov = overrides.get(key)
                if ov and ov.get("mode") == "manual":
                    continue
                controlled.add(key)
                action, target = _fan_rule_decision(key, rule, T, all_idle=all_idle, source=src)
                if action == "control" and target is not None:
                    _fan_smooth_step(hwmon, idx, target, _snap); controlling_any = True
                elif action == "release":
                    _fan_release_auto(hwmon, idx)
            # "hold" → 已交还自动，不再写入（避免与主板/内核抢控）
            # 剩余风扇：仅处理用户在 UI 中手动/自动设过的（overrides）；未触碰的风扇保持原样（交还 BIOS/主板）
            for (hwmon, idx), cfg in overrides.items():
                if (hwmon, idx) in controlled:
                    continue  # 已被温控接管
                if cfg.get("mode") == "auto":
                    ct = _fan_cpu_temp_cached()
                    _fan_smooth_step(hwmon, idx, _fan_auto_pwm(ct), _snap); controlling_any = True
                else:
                    tgt = cfg.get("target")
                    if tgt is None:
                        continue
                    _fan_smooth_step(hwmon, idx, tgt, _snap); controlling_any = True
            # 接管/交还系统风扇服务 FanControlServer：本应用真正控速任意风扇时停 FCS（避免抢控冲突），
            # 全部交还自动后重启 FCS 恢复 fnOS 原生控温。状态机保证只在边界切换时执行一次。
            if controlling_any and not _FCS_TAKEN["v"]:
                _fan_stop_ext_service(); _FCS_TAKEN["v"] = True
            elif not controlling_any and _FCS_TAKEN["v"]:
                _fan_start_ext_service(); _FCS_TAKEN["v"] = False
        except Exception:
            pass
        time.sleep(0.6)

def _fan_smooth_runner():
    """风扇控速常驻线程的『守护外壳』：fan_smooth_loop 内部虽已捕获 Exception，
    但为防止任何意外（含非 Exception 的 BaseException、SIGINT 等）导致控速线程静默死亡、
    进而使所有 PWM 变更（手动调速/温度联动）完全失效，此处再包一层自重启 + 异常落盘，
    确保控速线程永不永久退出，并把罕见崩溃原因写进 fan_thread.log 供排查。"""
    import traceback as _tb
    _log = os.path.join(_config_dir(), "fan_thread.log")
    while True:
        try:
            fan_smooth_loop()
        except BaseException as _e:   # 含 Exception 与 KeyboardInterrupt/SystemExit 等
            try:
                with open(_log, "a") as _fh:
                    _fh.write("%s [fan-smooth] 意外退出，2s 后自愈重启：%r\n%s\n"
                              % (time.strftime("%Y-%m-%d %H:%M:%S"), _e, _tb.format_exc()))
            except Exception:
                pass
            time.sleep(2)   # 退避后重启，避免空转打满 CPU


def get_fan_status():
    """风扇实时状态列表（供前端轮询与硬件健康报告复用）。"""
    fans = []
    labels = _load_fan_labels()
    _dt = _load_fan_disk_temp()
    _rules = _effective_fan_rules()   # 逐风扇温控规则（判定每台风扇是否被温控接管 + 算目标）
    # 逐风扇温控目标：温度源每种只算一次，供状态展示（与调速线程口径一致）
    _need_disk = any((r.get("source", "disk") == "disk") for r in _rules.values())
    _need_combo = any((r.get("source") or "").startswith("combo") for r in _rules.values())
    _need_cpu = any((r.get("source") == "cpu") for r in _rules.values()) or _need_combo
    _need_mb = any((r.get("source") == "mb") for r in _rules.values()) or _need_combo
    _need_raid = any((r.get("source") == "raid") for r in _rules.values()) or _need_combo
    _disk_idle_s, _disk_T, _disk_has = (False, None, False)
    if _need_disk:
        _dt_eff = _dt
        if not _dt.get("disks"):
            # 逐风扇 disk 规则不依赖全局监控盘白名单：自动用本机全部盘作温度源
            _all_devs = _list_all_disk_devs()
            if _all_devs:
                _dt_eff = dict(_dt, disks=_all_devs)
        _disk_idle_s, _disk_T, _disk_has = _disk_source_state(_dt_eff)
    _cpu_T = _fan_read_sys_temp("cpu") if _need_cpu else None
    _mb_T = _fan_read_sys_temp("mb") if _need_mb else None
    _raid_T = _fan_read_sys_temp("raid") if _need_raid else None
    fc_raw = read_file("/vol2/@appconf/FanControlServer/config.json")
    names = {}
    if fc_raw:
        try:
            fc = json.loads(fc_raw)
            for fan in fc.get("fans", []):
                fi = fan.get("pwm_index")
                if fi:
                    names[fi] = fan.get("name", f"风扇{fi}")
        except Exception:
            pass
    # 复用 _enumerate_fans：不依赖芯片型号、遍历所有 hwmon 风扇通道（fan1-10）、多芯片不漏
    for (hwmon, idx) in _enumerate_fans():
        _pe = read_file(f"{hwmon}/pwm{idx}_enable").strip()
        _pv = read_file(f"{hwmon}/pwm{idx}").strip()
        _fv = read_file(f"{hwmon}/fan{idx}_input").strip()
        try:
            rpm = int(_fv)
        except Exception:
            rpm = 0
        try:
            pwm_raw = int(_pv)
        except Exception:
            pwm_raw = None
        pwm_pct = round(pwm_raw / 255 * 100) if pwm_raw is not None else None
        cur_mode = "manual" if _pe == "1" else "auto" if _pe == "2" else "off"
        key = (hwmon, idx)
        # 逐风扇温控：该风扇若命中规则，模式按温度源分类（沿用旧徽标 disk_temp/sys_temp），
        # 并附带该风扇自己的规则与实时计算目标，供前端逐扇展示。
        _rule = _rules.get("%s::%d" % (hwmon, idx))
        rule_out = _rule
        # 3按钮显式控速方案：'linear'（开转/全速）/'curve'（自定义曲线）；手动时为 None
        active_mode = (_rule.get("active_mode") if _rule else None)
        computed_pwm = None
        rule_source = None
        if _rule:
            _src = _rule.get("source", "disk")
            rule_source = _src
            _rt, _ridle = _resolve_rule_temp(_src, _cpu_T, _mb_T, _disk_T, _disk_idle_s, _disk_has, _raid_T)
            if _rt is not None or _ridle:
                _raw = 0 if _ridle else _fan_rule_pwm(_rt, _rule)
                if _raw is not None:
                    computed_pwm = round(_raw / 255 * 100)
            # 显式分两类：手动覆盖时不算温控模式；规则存在但未接管（如曲线无效/温度低）
            # 时维持 3按钮的高亮态（active_mode 优先于 mode 标签）。
            if not active_mode:
                # 旧配置无 active_mode：回退到「linear/curve」按曲线有无判断
                active_mode = "curve" if _rule.get("curve") else "linear"
            if _src in ("cpu", "mb") or _src.startswith("combo"):
                mode = "sys_temp" if active_mode == "linear" else "curve"
            else:
                mode = "disk_temp" if active_mode == "linear" else "curve"
        else:
            # 无温控规则：以用户设的逻辑模式为准。nasdeck 接管控速时硬件 pwm_enable 恒为 1（manual），
            # 不能据此判断，故显示 auto（下面若 tcfg 为 manual 会再覆盖为 manual）。
            mode = "auto"
            active_mode = None
        target_pct = None
        with FAN_LOCK:
            tcfg = FAN_TARGETS.get(key)
        if tcfg and tcfg.get("mode") == "manual":
            target_pct = round(tcfg["target"] / 255 * 100)
        # #4 修复：用户显式手动调速时手动优先于温度联动，状态标签显示「手动控制」（与控速行为一致）
        if tcfg and tcfg.get("mode") == "manual":
            mode = "manual"
            active_mode = None
        # 用 _fan_label_for：精确 key 缺失时按 idx 跨 hwmon 兜底（hwmon 编号随重启漂移，
        # 如 hwmon4→hwmon5，否则标签/隐藏/自定义名全部错位成 风扇N）。
        _lbl = _fan_label_for(hwmon, idx)
        fans.append({
            "name": _lbl.get("name") or names.get(idx, f"风扇{idx}"),
            "label": _lbl.get("name", ""),
            "voltage": _lbl.get("voltage", ""),
            "idx": idx, "hwmon": hwmon,
            "rpm": rpm, "pwm": pwm_pct,
            "pwm_enable": _pe,   # 原始寄存器值：0=关闭 1=软件控(手动) 2=交还主板/内核自动
            # 调速信号类型：'pwm'=4pin 脉宽调速 / 'dc'=3pin 直流电压调速 / None=该通道无此寄存器。
            # DC 模式下 nasdeck 写的占空比按电压比例输出，低档更易停转，前端会给出提示。
            "pwm_mode": _fan_read_pwm_mode(hwmon, idx),
            "mode": mode,
            "rule": rule_out,
            "rule_source": rule_source,
            "computed_pwm": computed_pwm,
            "target_pct": target_pct,
            "controllable": True,
            # has_tach=False：该通道读不到转速（分线器副扇/未接转速线/主板未布线该通道）
            "has_tach": rpm > 0,
            # no_tach=True：用户已标注「此口风扇无转速反馈线（2/3针）」，读不到转速属正常。
            # 与 pwm_mode=='pwm' 同时成立时，前端提示占空比很可能不起作用（无信号线→恒满速）。
            "no_tach": bool(_lbl.get("no_tach")),
            # 用户可把「无风扇的幽灵通道」隐藏（持久化到 fan_labels.json）
            "hidden": bool(_lbl.get("hidden")),
            # 手动/曲线共存模型：曲线(rule)与手动覆盖相互独立，互不销毁（论坛 #3 反馈）。
            # has_curve=是否存了曲线；manual_active=当前是否处于手动态（曲线被临时盖住）。
            "has_curve": _rule is not None,
            "manual_active": bool(tcfg and tcfg.get("mode") == "manual"),
            # 3按钮显式方案：'linear'/'curve'/None(手动)
            "active_mode": active_mode,
        })
    fans = _apply_fan_aliases(fans)
    return _dedup_fan_names(fans)
