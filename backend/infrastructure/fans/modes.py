# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.fans.enumerate import _FAN_SNAP
from backend.infrastructure.fans.enumerate import _enumerate_fans
from backend.infrastructure.fans.fcs import _FCS_TAKEN
from backend.infrastructure.fans.fcs import _fan_ext_service_running
from backend.infrastructure.fans.fcs import _fan_start_ext_service
from backend.infrastructure.fans.fcs import _fan_stop_ext_service
from backend.infrastructure.fans.fcs import _fcs_disabled
from backend.infrastructure.fans.fcs import _fcs_has_board_config
from backend.infrastructure.fans.fcs import _fcs_installed_state
from backend.infrastructure.fans.fcs import _fcs_status_cached
from backend.infrastructure.fans.hwmon import _FAN_ENABLE_CACHE
from backend.infrastructure.fans.hwmon import _fan_release_to_idle
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
from backend.infrastructure.state import FAN_LOCK
from backend.infrastructure.state import FAN_TARGETS
import os
import threading


# ===================== 风扇模式持久化（启动自动恢复，避免重启后全速）=====================
# 按 idx（非 hwmon 路径）持久化，抗 hwmon 跨重启漂移。结构：{str(idx): {"mode":"auto"|"manual", "target":0-255}}
FAN_MODE_FILE = os.path.join(_config_dir(), "fan_mode.json")

def _load_fan_modes():
    return _load_json_file(FAN_MODE_FILE, {})

def _save_fan_modes(modes):
    return _save_json_file(FAN_MODE_FILE, modes)

def _save_fan_mode(idx, mode, target):
    """用户经 UI 设过某风扇模式后调用，持久化以便重启自动恢复。"""
    try:
        modes = _load_fan_modes()
        modes[str(int(idx))] = {"mode": mode, "target": (int(target) if target is not None else None)}
        _save_fan_modes(modes)
    except Exception:
        pass

# ===================== 风扇接管总开关 =====================
# 关闭后 nasdeck 不再根据温度自动调速。若飞牛自带风扇服务已配置，会交还它接管；
# 否则保持当前转速、不再写 PWM。
FAN_CONTROL_CFG = os.path.join(_config_dir(), "fan_control.json")
_FAN_CTRL_ENABLED = True

def _load_fan_control_enabled():
    global _FAN_CTRL_ENABLED
    try:
        d = _load_json_file(FAN_CONTROL_CFG, None)
        if isinstance(d, dict) and "enabled" in d:
            _FAN_CTRL_ENABLED = bool(d["enabled"])
    except Exception:
        pass
    return _FAN_CTRL_ENABLED

def set_control_enabled(enabled):
    """切换接管总开关（阶段 3 批 B 自 api_fan_control_set 整体收敛于此；
    原 app.py 单文件时代 global 赋值即本文件全局，拆包后必须活写本模块全局，
    控速循环 fan_smooth_loop 亦以模块属性活读本值——严禁 from-import 快照）。
    写配置 → 活写全局 → 清 FCS 状态缓存；开：后台停 FCS + 清 enable 缓存 + 置 snap；
    关：后台释放风扇到安全待机。返回 False = 配置写失败。"""
    global _FAN_CTRL_ENABLED
    if not _save_json_file(FAN_CONTROL_CFG, {"enabled": enabled}):
        return False
    _FAN_CTRL_ENABLED = enabled
    # 状态已切换，立即清掉 FCS 状态缓存，让前端下一次拉取拿到最新状态。
    _fcs_status_cached(clear=True)
    if enabled:
        # 重新接管前必须停掉系统风扇服务，否则 nasdeck 会因 FCS 在跑而拒绝写 PWM。
        # 只有 FCS 真的配置了参数时才需要停；没配置时它本来就不会控速。
        # systemctl stop 交给后台线程，避免阻塞本 API 响应（开关才能真正秒回）。
        try:
            if _fcs_has_board_config():
                def _stop_fcs():
                    try:
                        _fan_stop_ext_service()
                        _FCS_TAKEN["v"] = True
                    except Exception:
                        pass
                threading.Thread(target=_stop_fcs, daemon=True).start()
        except Exception:
            pass
        _FAN_ENABLE_CACHE.clear()
        # 标记下一 tick 把接管中的风扇直接到位（见 fan_smooth_loop 的 _snap 处理），让接管开关点一下立刻见效。
        with _FAN_SNAP["lock"]:
            _FAN_SNAP["v"] = True
    else:
        # 接管关闭：后台线程把风扇降到安全待机转速并停手（详见 _fan_release_to_idle 注释），
        # 避免同步写 sysfs 阻塞 API，造成前端 toggle 卡住、用户感觉「关不掉/打不开」。
        def _release():
            try:
                _fan_release_to_idle()
            except Exception:
                pass
        threading.Thread(target=_release, daemon=True).start()
    return True

def _restore_fan_modes():
    """nasdeck 启动后自动恢复风扇模式，避免重启后 FAN_TARGETS 空导致风扇保持硬件全速：
    - 有持久化配置：按用户上次选择恢复（auto 接管；若 FCS 在控则交还 enable=2；manual 恢复固定值）。
    - 无配置（首次/清配置）：若系统风扇服务 FCS 未在控，默认将所有可控风扇设为 auto 接管，
      消除开机全速；若 FCS 在控则交还、不强行接管（尊重 fnOS 原生控温）。"""
    try:
        if not _FAN_CTRL_ENABLED:
            # 接管关闭：优先把控制权交还 fnOS 原生 FanControlServer（FCS）。
            # FCS 使用 enable=1 + pwm 手动控速；若我们再写 enable=2 会覆盖它，导致风扇被卡在高速。
            # 若 FCS 真的配置了风扇参数，启动它接管；否则 nasdeck 不再写 PWM，
            # 保持风扇当前手动值（避免部分主板 enable=2 反而把转速拉得更高）。
            if not _fcs_disabled() and _fcs_installed_state() == "enabled" and _fcs_has_board_config():
                _fan_start_ext_service()
            else:
                # 无 FCS 可接管：落到安全待机占空比，避免开机即高位冻结/狂转。
                try:
                    _fan_release_to_idle()
                except Exception:
                    pass
            return
        enum = _enumerate_fans()
        idx2hw = {i: h for (h, i) in enum}
        if not idx2hw:
            return
        # FCS 服务 active(exited) 不代表它真的能控速：/boot/board.json 为空时它只是空跑。
        # 只有服务在跑且配置了风扇参数时，才视为「系统在控」，nasdeck 不抢。
        fcs = _fan_ext_service_running() and _fcs_has_board_config()
        modes = _load_fan_modes()
        if not modes:
            # 首次/无配置：FCS 未控则默认接管 auto；FCS 在控则交还、不抢
            if not fcs:
                for idx, hwmon in idx2hw.items():
                    with FAN_LOCK:
                        FAN_TARGETS[(hwmon, idx)] = {"mode": "auto", "target": None}
            else:
                for idx, hwmon in idx2hw.items():
                    try:
                        with open(f"{hwmon}/pwm{idx}_enable", "w") as f:
                            f.write("2")
                    except Exception:
                        pass
            return
        # 已有配置：按用户上次选择恢复
        for sidx, m in modes.items():
            try:
                idx = int(sidx)
            except Exception:
                continue
            if idx not in idx2hw:
                continue
            hwmon = idx2hw[idx]
            mode = m.get("mode")
            if mode == "auto":
                if fcs:
                    try:
                        with open(f"{hwmon}/pwm{idx}_enable", "w") as f:
                            f.write("2")
                    except Exception:
                        pass
                else:
                    with FAN_LOCK:
                        FAN_TARGETS[(hwmon, idx)] = {"mode": "auto", "target": None}
            elif mode == "manual":
                tgt = m.get("target")
                if tgt is None:
                    continue
                with FAN_LOCK:
                    FAN_TARGETS[(hwmon, idx)] = {"mode": "manual", "target": int(tgt)}
        # 配置未列出、但本机枚举到的可控风扇（启动期 hwmon 晚注册 / 新装风扇），
        # 默认接管为 auto（FCS 未控时），避免留下「失控 / 开机狂转」的风扇。
        if not fcs:
            for idx, hwmon in idx2hw.items():
                with FAN_LOCK:
                    if (hwmon, idx) not in FAN_TARGETS:
                        FAN_TARGETS[(hwmon, idx)] = {"mode": "auto", "target": None}
    except Exception:
        pass
