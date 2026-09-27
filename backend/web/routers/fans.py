from backend.collectors.cputemp import _temp_snapshot_read
from backend.collectors.disks import _disk_friendly_info
from backend.collectors.disks import _list_all_disk_devs
from backend.collectors.disks import get_disk_temps_cached
from backend.collectors.gpu import _get_gpu_live
from backend.fans.control import get_fan_status
from backend.fans.enumerate import _FAN_SNAP
from backend.fans.enumerate import _enumerate_fans
from backend.fans.fcs import _FCS_TAKEN
from backend.fans.fcs import _fan_ext_service_running
from backend.fans.fcs import _fan_stop_ext_service
from backend.fans.fcs import _fcs_board_cfg_cached
from backend.fans.fcs import _fcs_disable
from backend.fans.fcs import _fcs_enable
from backend.fans.fcs import _fcs_has_board_config
from backend.fans.fcs import _fcs_status_cached
from backend.fans.hwmon import _FAN_ENABLE_CACHE
from backend.fans.hwmon import _fan_read_pwm_mode
from backend.fans.hwmon import _fan_release_to_idle
from backend.fans.hwmon import _fan_set_pwm_mode
from backend.fans.labels import _FAN_VOLT_ALLOWED
from backend.fans.labels import _canon_label_key
from backend.fans.labels import _consolidate_fan_labels
from backend.fans.labels import _dedup_fan_names
from backend.fans.labels import _fan_alias_members_of
from backend.fans.labels import _fan_label_for
from backend.fans.labels import _load_fan_labels
from backend.fans.labels import _read_fan_drivers
from backend.fans.labels import _replicate_aliases
from backend.fans.labels import _save_fan_labels
from backend.fans.modes import FAN_CONTROL_CFG
from backend.fans.modes import _save_fan_mode
from backend.fans.rules import NVME_START_TEMP
from backend.fans.rules import _FAN_RULE_SOURCES
from backend.fans.rules import _apply_temp_curve
from backend.fans.rules import _disk_source_state
from backend.fans.rules import _effective_fan_rules
from backend.fans.rules import _fan_disk_temp_pwm
from backend.fans.rules import _fan_read_sys_temp
from backend.fans.rules import _fan_rule_pwm
from backend.fans.rules import _fan_sys_temp_pwm
from backend.fans.rules import _load_fan_disk_temp
from backend.fans.rules import _load_fan_rules_raw
from backend.fans.rules import _load_fan_sys_temp
from backend.fans.rules import _save_fan_disk_temp
from backend.fans.rules import _save_fan_rules
from backend.fans.rules import _save_fan_sys_temp
from backend.fans.rules import _validate_fan_rule
from backend.history.db import _DB_PATH
from backend.history.db import _db_lock
from backend.history.db import _rename_history_fan
from backend.jsonstore import _save_json_file
from backend.state import FAN_LOCK
from backend.state import FAN_TARGETS
import json
import os
import sqlite3 as _sqlite3
import threading as _threading
import time

# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.gateway import require_admin
from backend.web.deps import _jr, json_body, json_body_as
from backend.web.schemas import FanSetRequest, FanPwmModeRequest, FanControlRequest, FanDiskTempConfigRequest, FanSysTempConfigRequest, FanRulesRequest, FanFcsRequest, FanLabelsRequest

router = APIRouter()



@router.post("/api/fan/set")
def api_fan_set(request: Request, payload: dict = Depends(json_body_as(FanSetRequest)), _admin = Depends(require_admin)):
    """设置风扇转速：设目标 PWM（后台缓变线程平滑过渡）或恢复自动控温"""
    try:
        if not _FAN_CTRL_ENABLED:
            return _jr({"ok": False, "error": "风扇接管已关闭，nasdash 不控速（仅监控）"}, 400)
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    hwmon = data.get("hwmon")
    idx = data.get("idx")
    mode = data.get("mode")
    pwm = data.get("pwm")
    # 安全：仅允许本机 hwmon 路径，防止路径注入
    if not isinstance(hwmon, str) or not hwmon.startswith("/sys/class/hwmon/hwmon"):
        return _jr({"ok": False, "error": "invalid hwmon"}, 400)
    try:
        idx = int(idx)
        if idx < 1 or idx > 10:
            raise ValueError()
    except (TypeError, ValueError):
        return _jr({"ok": False, "error": "invalid idx"}, 400)
    key = (hwmon, idx)
    # 防御：hwmon 路径可能跨重启/进程漂移(如 nct6797 在 hwmon3↔hwmon4 间变化)，
    # 而 FAN_TARGETS 与平滑线程都以「实时枚举」为权威 key。若 GUI 发来的 (hwmon,idx)
    # 不在实时枚举中、但该 idx 存在，则按 idx 校正 hwmon，确保目标命中真正的可控通道。
    _enum = _enumerate_fans()
    if (hwmon, idx) not in _enum:
        _idx2hw = {i: h for (h, i) in _enum}
        if idx in _idx2hw:
            hwmon = _idx2hw[idx]
            key = (hwmon, idx)
    # 别名组：同一物理风扇多通道（用户为这些通道设了相同名称）→ 控速同步到全部成员
    _keys = _fan_alias_members_of(hwmon, idx)
    if mode == "auto":
        ext = _fan_ext_service_running()
        for (h, i) in _keys:
            if ext:
                # 系统风扇服务在跑：交还它接管（写 enable=2）
                try:
                    with open(f"{h}/pwm{i}_enable", "w") as f:
                        f.write("2")
                except Exception:
                    pass
                with FAN_LOCK:
                    FAN_TARGETS.pop((h, i), None)
            else:
                # 系统风扇服务未运行：nasdash 自带保守温控曲线接管
                with FAN_LOCK:
                    FAN_TARGETS[(h, i)] = {"mode": "auto", "target": None}
            _save_fan_mode(i, "auto", None)
        owner = "ext_service" if ext else "nasdash"
        return _jr({"ok": True, "mode": "auto", "owner": owner})
    try:
        pct = int(pwm)
    except (TypeError, ValueError):
        return _jr({"ok": False, "error": "invalid pwm"}, 400)
    FLOOR = 0  # 允许命令到 0%（真正停转）。有用户反馈其硬件在 10% 已停转，需要能设到 0%
    pct = max(FLOOR, min(100, pct))
    raw = round(pct / 100 * 255)
    # 仅设为目标值，由缓变线程平滑过渡（不再瞬间写 255，避免突然全速）
    for (h, i) in _keys:
        with FAN_LOCK:
            FAN_TARGETS[(h, i)] = {"mode": "manual", "target": raw}
        _save_fan_mode(i, "manual", raw)
    return _jr({"ok": True, "mode": "manual", "pwm": pct, "raw": raw, "aliased": len(_keys) > 1})

@router.post("/api/fan/pwm_mode")
def api_fan_pwm_mode(request: Request, payload: dict = Depends(json_body_as(FanPwmModeRequest)), _admin = Depends(require_admin)):
    """切换风扇接口的调速信号类型：PWM(4pin 脉宽) ↔ DC(3pin 直流电压)。

    背景：3pin 风扇插在被设成 PWM 的接口上会一直全速——因为它没有 PWM 线，只认电压。
    多数主板 BIOS 里能给每个接口单独选 PWM/DC，Linux 下对应 pwmN_mode 寄存器。
    但**能不能改由主板硬件决定**：部分接口只焊了 PWM 电路，写 DC 会被内核直接拒绝，
    这种情况只能进 BIOS 或换 4pin 风扇，属主板限制而非软件问题。
    """
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    hwmon = data.get("hwmon")
    idx = data.get("idx")
    mode = (data.get("pwm_mode") or "").strip().lower()
    if not isinstance(hwmon, str) or not hwmon.startswith("/sys/class/hwmon/hwmon"):
        return _jr({"ok": False, "error": "invalid hwmon"}, 400)
    try:
        idx = int(idx)
        if idx < 1 or idx > 10:
            raise ValueError()
    except (TypeError, ValueError):
        return _jr({"ok": False, "error": "invalid idx"}, 400)
    if mode not in ("pwm", "dc"):
        return _jr({"ok": False, "error": "invalid pwm_mode"}, 400)
    # 与 /api/fan/set 一致：hwmon 编号可能跨重启漂移，按实时枚举校正
    _enum = _enumerate_fans()
    if (hwmon, idx) not in _enum:
        _idx2hw = {i: h for (h, i) in _enum}
        if idx in _idx2hw:
            hwmon = _idx2hw[idx]
    cur = _fan_read_pwm_mode(hwmon, idx)
    if cur is None:
        return _jr({"ok": False, "error": "该接口不支持切换调速方式（主板未提供此寄存器）"}, 400)
    if cur == mode:
        return _jr({"ok": True, "pwm_mode": mode, "changed": False})
    ok, err = _fan_set_pwm_mode(hwmon, idx, mode)
    if not ok:
        return _jr({"ok": False, "error": err, "pwm_mode": cur}, 400)
    return _jr({"ok": True, "pwm_mode": mode, "changed": True})

@router.get("/api/fan/status")
def api_fan_status():
    """轻量风扇状态：供前端高频轮询，实时显示转速/当前占空比/目标（常驻线程 2s tick）"""
    # fcs 只读 15s TTL 缓存（_fcs_status_cached 绝不在此跑 systemctl），供前端在「接管关闭」时
    # 说清现在由谁在管风扇（v2.3.0 3.1）。
    _fs = _fcs_status_cached()
    return _jr({
        "fans": get_fan_status(),
        "control_enabled": _FAN_CTRL_ENABLED,
        "fan_drivers": _read_fan_drivers(),
        "fcs": {
            "running": bool(_fs.get("running")),
            "installed": bool(_fs.get("installed") or _fs.get("enabled")),
            "configured": _fcs_board_cfg_cached(),
            "disabled_by_user": bool(_fs.get("disabled_by_user")),
        },
    })

@router.get("/api/fan/control")
def api_fan_control_get():
    """风扇接管总开关状态。"""
    return _jr({"enabled": _FAN_CTRL_ENABLED})

@router.post("/api/fan/control")
def api_fan_control_set(request: Request, payload: dict = Depends(json_body_as(FanControlRequest)), _admin = Depends(require_admin)):
    """开关风扇接管：关闭后 nasdash 不再根据温度自动调速。若飞牛自带风扇服务已配置，
    会交还它接管；否则保持当前转速、不再写 PWM。"""
    global _FAN_CTRL_ENABLED
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    enabled = bool(data.get("enabled", True))
    if not _save_json_file(FAN_CONTROL_CFG, {"enabled": enabled}):
        return _jr({"ok": False, "error": "写入配置失败"}, 500)
    _FAN_CTRL_ENABLED = enabled
    # 状态已切换，立即清掉 FCS 状态缓存，让前端下一次拉取拿到最新状态。
    _fcs_status_cached(clear=True)
    if enabled:
        # 重新接管前必须停掉系统风扇服务，否则 nasdash 会因 FCS 在跑而拒绝写 PWM。
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
                _threading.Thread(target=_stop_fcs, daemon=True).start()
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
        _threading.Thread(target=_release, daemon=True).start()
    return _jr({"ok": True, "enabled": enabled})

@router.get("/api/fan/temps")
def api_fan_temps():
    """温度统一快照：CPU/主板/各硬盘/全量测点/阵列卡，供前端「当前温度」卡与温度墙高频展示。
    全部数据来自后台采集循环的 _TEMP_SNAP（~2s 刷新），不再各自采样。"""
    _snap = _temp_snapshot_read()
    cpu_T = _snap.get("cpu_temp")
    mb_T = _snap.get("mb_temp")
    states = _snap.get("disks") or {}
    devs = list(states.keys())
    disks = []
    for d in devs:
        st = states.get(d, {})
        fi = _disk_friendly_info(d)
        disks.append({
            "dev": d,
            "name": fi["name"],
            "model": fi["model"],
            "category": fi["category"],
            "is_system": fi["is_system"],
            "temp": st.get("temp"),
            "asleep": st.get("asleep"),
            "no_sleep": st.get("no_sleep", False),
            # 供前端标注「被动散热、不参与风扇温控」，并说明其判定阈值与机械盘不同
            "is_nvme": st.get("is_nvme", False),
            "nvme_start_temp": NVME_START_TEMP,
            # 接口类型（SAS/空）：供温度页给 SAS 盘加身份小徽标
            "intf": (st.get("intf") or "").upper(),
        })
    # 同型号同容量的盘（典型：双磁臂 SAS 盘拆成两个 LUN、或买了两块一样的盘）友好名会撞车，
    # 撞车时补上内核名后缀，保证「一眼能认出是哪块」这个目标不被重名破坏。
    _name_count = {}
    for it in disks:
        _name_count[it["name"]] = _name_count.get(it["name"], 0) + 1
    for it in disks:
        if _name_count.get(it["name"], 0) > 1:
            it["name"] = "%s·%s" % (it["name"], str(it["dev"]).replace("/dev/", ""))
    return _jr({
        "cpu_temp": round(cpu_T, 1) if isinstance(cpu_T, (int, float)) else None,
        "mb_temp": round(mb_T, 1) if isinstance(mb_T, (int, float)) else None,
        "disks": disks,
        # 阵列卡物理盘温度（RAID5 等虚拟盘场景下物理盘不暴露 /dev，OS 盘列表看不到，
        # 只能从 storcli 拿；已按序列号与上方 OS 可见盘去重）：
        "raid_drives": _snap.get("raid_drives") or [],
        # 温度墙测点全量 + 阵列卡芯片温度：前端温度页 5s 实时刷新用（不再等 30s 快照）
        "sensors": _snap.get("temps") or [],
        "raid_temp": _snap.get("raid_temp"),
        "raid_controller_temp": _snap.get("raid_controller_temp"),
        # 显卡温度：复用 GPU 实时采样缓存（2s 刷新），仅取温度相关字段，供温度墙展示。
        # iGPU（Intel 核显）temp 取 CPU 封装温度（同 die）；dGPU 走各自驱动。
        # 附带显存占用（供系统资源页顶部 hero「显存占用」指标格）：
        # 独显走驱动真实显存；核显取 i915 图形缓冲实际占用（共享内存口径，见 mem_basis）。
        "gpus": [
            {"name": gg.get("name"), "vendor": gg.get("vendor"),
             "type": gg.get("type"), "temp": gg.get("temp"),
             "mem_used": gg.get("mem_used"), "mem_total": gg.get("mem_total"),
             "mem_pct": gg.get("mem_pct"), "mem_basis": gg.get("mem_basis")}
            for gg in _get_gpu_live()
        ],
    })

@router.get("/api/fan/disk_temp")
def api_fan_disk_temp_get():
    """读取硬盘温度控风扇配置 + 实时监控盘温度/休眠 + 计算所得目标PWM"""
    cfg = _load_fan_disk_temp()
    devs = cfg.get("disks", [])
    # 未设监控盘白名单时，与控速循环保持一致：自动用本机全部盘作温度源，
    # 否则 preview 永远空、与实际控制态（已用全部盘）不一致。
    if not devs:
        devs = _list_all_disk_devs()
    states = get_disk_temps_cached(devs) if devs else {}
    disks_out = [{
        "dev": dev,
        "temp": states.get(dev, {}).get("temp"),
        "asleep": states.get(dev, {}).get("asleep"),
        "no_sleep": states.get(dev, {}).get("no_sleep", False),
    } for dev in devs]
    target = _fan_disk_temp_pwm(states, cfg)
    return _jr({
        "config": cfg,
        "disks": disks_out,
        "computed_pwm": round(target / 255 * 100) if target is not None else None,
        "computed_raw": target,
    })

@router.post("/api/fan/disk_temp")
def api_fan_disk_temp_set(request: Request, payload: dict = Depends(json_body_as(FanDiskTempConfigRequest)), _admin = Depends(require_admin)):
    data = payload or {}
    cfg = _load_fan_disk_temp()
    if "disks" in data:
        if not isinstance(data["disks"], list):
            return _jr({"ok": False, "error": "disks 需为数组"}, 400)
        norm = []
        for d in data["disks"]:
            # 前端勾选值可能是短名（sda / nvme0n1），也可能是已带 /dev/ 的全路径
            devname = str(d["dev"] if isinstance(d, dict) and "dev" in d else d)
            dd = devname if devname.startswith("/dev/") else "/dev/" + devname
            if not os.path.exists(dd):
                return _jr({"ok": False, "error": "设备不存在: " + dd}, 400)
            norm.append(dd)
        cfg["disks"] = norm
    if "sleep_stop" in data:
        cfg["sleep_stop"] = bool(data["sleep_stop"])
    if "idle_minutes" in data:
        try:
            iv = float(data["idle_minutes"])
        except (TypeError, ValueError):
            return _jr({"ok": False, "error": "idle_minutes 需为数字"}, 400)
        if iv < 1 or iv > 120:
            return _jr({"ok": False, "error": "idle_minutes 需在 1~120 分钟"}, 400)
        cfg["idle_minutes"] = iv
    err = _apply_temp_curve(cfg, data, recover_max=100)
    if err:
        return err
    if _save_fan_disk_temp(cfg):
        return _jr({"ok": True, "config": cfg})
    return _jr({"ok": False, "error": "写配置失败"}), 500

@router.get("/api/fan/sys_temp")
def api_fan_sys_temp_get():
    """读取主板/CPU 温度控风扇配置 + 当前温度源读数 + 计算所得目标PWM"""
    cfg = _load_fan_sys_temp()
    T = _fan_read_sys_temp(cfg.get("source", "cpu"))
    target = _fan_sys_temp_pwm(T, cfg) if T is not None else None
    return _jr({
        "config": cfg,
        "source_temp": round(T, 1) if T is not None else None,
        "computed_pwm": round(target / 255 * 100) if target is not None else None,
        "computed_raw": target,
    })

@router.post("/api/fan/sys_temp")
def api_fan_sys_temp_set(request: Request, payload: dict = Depends(json_body_as(FanSysTempConfigRequest)), _admin = Depends(require_admin)):
    data = payload or {}
    cfg = _load_fan_sys_temp()
    if "source" in data:
        if data["source"] not in ("cpu", "mb"):
            return _jr({"ok": False, "error": "source 需为 cpu 或 mb"}, 400)
        cfg["source"] = data["source"]
    err = _apply_temp_curve(cfg, data, recover_max=120)
    if err:
        return err
    if _save_fan_sys_temp(cfg):
        return _jr({"ok": True, "config": cfg})
    return _jr({"ok": False, "error": "写配置失败"}), 500

@router.get("/api/fan/rules")
def api_fan_rules_get():
    """读取逐风扇温控规则（含旧全局面板派生的默认）+ 每台风扇的温度源、实时温度、计算目标。
    is_custom：是否已存在用户逐风扇覆盖文件。"""
    rules = _effective_fan_rules()
    dt = _load_fan_disk_temp()
    _dt_eff = dt
    if not dt.get("disks"):
        _all_devs = _list_all_disk_devs()
        if _all_devs:
            _dt_eff = dict(dt, disks=_all_devs)
    _need_disk_state = any(
        (r.get("source", "disk") == "disk") or "disk" in (r.get("source") or "") for r in rules.values())
    disk_all_idle, disk_T, disk_has = _disk_source_state(_dt_eff) if _need_disk_state else (False, None, bool(_dt_eff.get("disks")))
    cpu_T = _fan_read_sys_temp("cpu")
    mb_T = _fan_read_sys_temp("mb")
    fans_out = []
    for (hwmon, idx) in _enumerate_fans():
        rk = "%s::%d" % (hwmon, idx)
        rule = rules.get(rk)
        lbl = _fan_label_for(hwmon, idx)
        src = (rule or {}).get("source")
        if src == "cpu":
            T = cpu_T
        elif src == "mb":
            T = mb_T
        else:
            T = disk_T if rule else None
        computed = None
        if rule:
            raw = 0 if (src == "disk" and disk_all_idle) else _fan_rule_pwm(T, rule)
            if raw is not None:
                computed = round(raw / 255 * 100)
        fans_out.append({
            "hwmon": hwmon, "idx": idx, "key": rk,
            "name": lbl.get("name") or ("风扇%d" % idx),
            "hidden": bool(lbl.get("hidden")),
            "rule": rule,
            "source_temp": round(T, 1) if isinstance(T, (int, float)) else None,
            "computed_pwm": computed,
        })
    fans_out = _dedup_fan_names(fans_out)
    return _jr({
        "fans": fans_out,
        "is_custom": _load_fan_rules_raw() is not None,
        "disk": {"disks": dt.get("disks", []), "has_disks": disk_has,
                 "all_idle": disk_all_idle, "temp": round(disk_T, 1) if isinstance(disk_T, (int, float)) else None},
        "cpu_temp": round(cpu_T, 1) if isinstance(cpu_T, (int, float)) else None,
        "mb_temp": round(mb_T, 1) if isinstance(mb_T, (int, float)) else None,
        "sources": list(_FAN_RULE_SOURCES) + ["combo_max:cpu,mb", "combo_avg:cpu,mb"],
    })

@router.post("/api/fan/rules")
def api_fan_rules_set(request: Request, payload: dict = Depends(json_body_as(FanRulesRequest)), _admin = Depends(require_admin)):
    """保存逐风扇温控规则覆盖。两种 body：
      整体覆盖：{"rules": {"<hwmon>::<idx>": {...}|null, ...}}
      单条设置：{"key": "<hwmon>::<idx>", "rule": {...}|null}
    rule=null 表示清除该风扇的自定义（回到全局默认/手动）。"""
    data = payload or {}
    saved = _load_fan_rules_raw() or {"rules": {}}
    cur = dict(saved.get("rules") or {})
    # 注意：保存温度联动规则【不再】清掉该风扇的手动覆盖（移除旧 _clear_manual_for_rule）。
    # 手动与曲线是两套独立状态：手动只是临时钉转速，曲线(fan_rules)始终保留；
    # 用户点「回到我的曲线」即恢复（见 api_fan_set mode=auto）。两者不再互斥。

    if "key" in data:
        k = data.get("key")
        if not isinstance(k, str) or "::" not in k:
            return _jr({"ok": False, "error": "key 需为 '<hwmon>::<idx>'"}, 400)
        clean, err = _validate_fan_rule(data.get("rule"))
        if err:
            return _jr({"ok": False, "error": err}, 400)
        if clean is None:
            # 显式写入 null 占位：saved 里这条 key 不存在时 _derive_rules_from_legacy()
            # 仍会派生默认规则给该风扇，必须靠占位让 _effective_fan_rules() 的
            # `if r is None: rules.pop(k, None)` 把 legacy 默认也摘掉，关闭才真正生效。
            cur[k] = None
        else:
            cur[k] = clean
    elif "rules" in data:
        incoming = data.get("rules")
        if not isinstance(incoming, dict):
            return _jr({"ok": False, "error": "rules 需为对象"}, 400)
        for k, r in incoming.items():
            if not isinstance(k, str) or "::" not in k:
                return _jr({"ok": False, "error": "规则 key 需为 '<hwmon>::<idx>'"}, 400)
            clean, err = _validate_fan_rule(r)
            if err:
                return _jr({"ok": False, "error": "%s: %s" % (k, err)}, 400)
            if clean is None:
                # 同单条入口：必须写 null 占位，否则 legacy 派生默认会重新覆盖。
                cur[k] = None
            else:
                cur[k] = clean
    else:
        return _jr({"ok": False, "error": "缺少 key 或 rules"}, 400)
    if _save_fan_rules({"rules": cur}):
        return _jr({"ok": True, "rules": cur})
    return _jr({"ok": False, "error": "写配置失败"}), 500

@router.post("/api/fan/fcs")
def api_fan_fcs_post(request: Request, payload: dict = Depends(json_body_as(FanFcsRequest)), _admin = Depends(require_admin)):
    """永久禁用 / 恢复 FanControlServer。body: {"action": "disable"|"enable"}。
    disable = systemctl stop + disable（重启不复活）+ 持久化标志；enable = enable + start。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    action = data.get("action")
    if action == "disable":
        _fcs_disable()
    elif action == "enable":
        _fcs_enable()
    else:
        return _jr({"ok": False, "error": "action 需为 disable 或 enable"}, 400)
    # 操作完成后立即刷新缓存，让前端拿到最新状态
    return _jr({"ok": True, "status": _fcs_status_cached(clear=True)})

@router.get("/api/fan/labels")
def api_fan_labels_get():
    """返回用户标注的风扇名称/电压：key="hwmon::idx" -> {"name","voltage"}"""
    return _jr(_load_fan_labels())

@router.get("/api/fan/history")
def api_fan_history(request: Request):
    """返回每台风扇的转速/占空比/接管状态历史（每 30s 一点，保留 30 天）。供风扇问题排查复盘。"""
    try:
        rng = request.query_params.get("range", "24h")
        secs = {"24h": 86400, "7d": 7*86400, "30d": 30*86400}.get(rng, 86400)
        end = int(time.time()); start = end - secs
        with _db_lock:
            con = _sqlite3.connect(_DB_PATH)
            rows = con.execute(
                "SELECT ts, fan_detail FROM samples WHERE ts>=? AND fan_detail IS NOT NULL ORDER BY ts",
                (start,)).fetchall()
            con.close()
        points = []
        for ts, detail in rows:
            try:
                fl = json.loads(detail) if detail else []
            except Exception:
                fl = []
            points.append({"ts": ts * 1000, "fans": fl})
        return _jr({"range": rng, "points": points})
    except Exception as e:
        return _jr({"error": str(e), "points": []})

@router.post("/api/fan/labels")
def api_fan_labels_post(request: Request, payload: dict = Depends(json_body_as(FanLabelsRequest)), _admin = Depends(require_admin)):
    """保存风扇标注（合并模式：仅覆盖传入的通道，保留其余，防止单条保存清空全部）。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    # 先读出现有标注,用于把改名/隐藏归并到同一 idx 的既有 key(跨 hwmon 漂移兜底,
    # 否则每次重启 hwmon 重排都新建孤立 key,改名在下次开机后像『没保存』)
    existing = _load_fan_labels()
    # 改动前名字快照：用于检测哪些 idx 被改名,以便连带重写历史库
    _old_names: dict = {}
    for _k, _v in existing.items():
        if "::" not in _k:
            continue
        try:
            _ii = int(_k.split("::", 1)[1])
        except (TypeError, ValueError):
            continue
        _nm = (_v or {}).get("name")
        if _nm:
            _old_names[_ii] = _nm
    incoming = {}
    for k, v in data.items():
        if not isinstance(k, str) or "::" not in k:
            continue
        hwmon, idx = k.split("::", 1)
        # 安全：仅允许本机 hwmon 路径 + 合法 idx，防止注入
        if not hwmon.startswith("/sys/class/hwmon/hwmon"):
            continue
        try:
            idx_i = int(idx)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        name = str(v.get("name", ""))[:40]
        volt = v.get("voltage", "")
        if volt not in _FAN_VOLT_ALLOWED:
            volt = "未知"
        entry = {}
        if name:
            entry["name"] = name
        if volt != "未知":
            entry["voltage"] = volt
        if v.get("hidden"):
            entry["hidden"] = True   # 隐藏无风扇的幽灵通道（可恢复）
        if v.get("no_tach"):
            # 用户标注「这把风扇没有转速反馈线」（2 针风扇 / 分线器副扇 / 转速线未接）：
            # 风扇本身在转，只是主板永远读不到 rpm。标了之后转速栏不再显示刺眼的 0，
            # 也不会被误判成「停转/空通道」。
            entry["no_tach"] = True
        # 空标注（name 空且无 hidden/no_tach）→ 视为取消该 idx 的标注，删除既有 key
        if not entry:
            canon = _canon_label_key(existing, idx_i)
            if canon:
                incoming[canon] = None
            continue
        # 同一 idx 的既有标注可能落在别的 hwmon 路径下(hwmon 漂移),写入时归并到那条 key,
        # 避免重复建 key;没有既有 key 才用本次传入的(当前 hwmon 路径)。
        canon = _canon_label_key(existing, idx_i) or k
        incoming[canon] = entry
    # 合并现有标注，而非整体覆盖：防御前端只传单条导致全量清空
    for k, v in incoming.items():
        if v is None:
            existing.pop(k, None)
        else:
            existing[k] = v
    _replicate_aliases(existing)   # 别名同步：同名标注通道改名/隐藏联动（huhaibo820 #1）
    _consolidate_fan_labels(existing)  # 保存前再归并一次：清除漂移产生的重复 idx key
    if _save_fan_labels(existing):
        # 保存成功后立即让系统总览缓存失效，风扇卡片无需强制刷新即可显示新名字
        global _SYSTEM_CACHE
        _SYSTEM_CACHE["v"] = None
        _SYSTEM_CACHE["t"] = 0.0
        # 检测改名：把历史库里该 idx 的所有旧样本名字也改成新名(连带改名,无残留,不丢历史)
        _renames = []
        for _k, _v in existing.items():
            if "::" not in _k:
                continue
            try:
                _ii = int(_k.split("::", 1)[1])
            except (TypeError, ValueError):
                continue
            _nm = (_v or {}).get("name")
            if _nm and _old_names.get(_ii) != _nm:
                _renames.append((_ii, _nm, _old_names.get(_ii)))
        if _renames:
            def _rename_history_bg():
                for (_i, _nn, _on) in _renames:
                    _rename_history_fan(_i, _nn, _on)
            _threading.Thread(target=_rename_history_bg, daemon=True).start()
        return _jr({"ok": True})
    return _jr({"ok": False, "error": "save failed"}), 500