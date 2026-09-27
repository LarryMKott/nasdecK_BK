from backend.collectors.disks import get_disks
from backend.collectors.raid import get_raid_card
from backend.schedule import _load_cc_schedule
from backend.schedule import _raid_cc_vnum
from backend.schedule import _save_cc_schedule
from backend.shell import STORCLI
from backend.shell import sudo_cmd
from backend.web.routers.helpers import _panel_time
import re
import time

# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.gateway import require_admin
from backend.web.deps import _jr, json_body, json_body_as
from backend.web.schemas import RaidLocateRequest, RaidCcRequest, RaidCcScheduleRequest, RaidHotspareRequest, RaidCopybackRequest

router = APIRouter()



@router.get("/api/raid")
def api_raid():
    """阵列卡板块。#raid 渲染同时依赖 raid + disks，故一并返回（raid 带 60s 缓存，disks 带 300s 缓存）。"""
    t0 = time.time()
    try:
        return _jr({"raid": get_raid_card(), "disks": get_disks(),
                        "cc_schedule": _load_cc_schedule(),
                        "time": _panel_time(), "elapsed": round(time.time() - t0, 2)})
    except Exception as e:
        return _jr({"error": str(e), "time": _panel_time()})

@router.post("/api/raid/locate")
def api_raid_locate(request: Request, payload: dict = Depends(json_body_as(RaidLocateRequest))):
    """物理盘定位（locate LED 闪烁）。仅当控制器/背板支持时可用（locate_supported）。"""
    try:
        body = payload or {}
        slot = str(body.get("slot", "")).strip()
        action = str(body.get("action", "")).strip().lower()
        if not re.match(r"^\d+:\d+$", slot):
            return _jr({"ok": False, "error": "invalid slot"})
        if action not in ("start", "stop"):
            return _jr({"ok": False, "error": "invalid action"})
        rc = get_raid_card()
        if not rc.get("locate_supported"):
            return _jr({"ok": False, "error": "本机阵列卡/背板不支持定位闪灯"})
        valid = {d.get("slot") for d in (rc.get("drives") or [])}
        if slot not in valid:
            return _jr({"ok": False, "error": "slot 不在阵列卡接管盘列表中"})
        e, s = slot.split(":")
        verb = "start" if action == "start" else "stop"
        out = sudo_cmd([STORCLI, "/c0", "/e" + e, "/s" + s, verb, "locate"], 20)
        ok = bool(out and "Succeeded" in out)
        return _jr({"ok": ok, "action": action, "slot": slot, "out": out})
    except Exception as ex:
        return _jr({"ok": False, "error": str(ex)})

@router.get("/api/raid/cc")
@router.post("/api/raid/cc")
def api_raid_cc(request: Request, payload: dict = Depends(json_body_as(RaidCcRequest))):
    """阵列卡一致性检查（Consistency Check）。

    仅对存在的硬件 RAID 逻辑盘（VD）执行；本机为 JBOD 直通时无 VD，接口直接返回明确错误（不崩溃）。
    - GET  ?vd=0         ：查询该 VD 的 CC 进度与状态（idle/running/paused + 百分比）
    - POST {vd, action}  ：action = start|pause|resume|stop（stop 后不可恢复）
    CC 为只读扫描（不改数据），但会抬升磁盘负载，前端在开始前需二次确认。
    """
    try:
        rc = get_raid_card()
        vds = rc.get("virtual_drives") or []
        if not vds:
            return _jr({"ok": False,
                            "error": "本机无硬件 RAID 逻辑盘（VD），无法执行一致性检查（当前为 JBOD 直通模式，阵列由系统层 mdadm 管理）"})
        if request.method == "GET":
            vnum = _raid_cc_vnum(request.query_params.get("vd"), vds)
            if vnum is None:
                return _jr({"ok": False, "error": "invalid vd"})
            out = sudo_cmd([STORCLI, "/c0", "/v" + vnum, "show", "cc"], 15) or ""
            prog = re.search(r"Progress\s*=?\s*(\d+)%", out)
            pct = int(prog.group(1)) if prog else None
            state = "idle"
            if re.search(r"running|In progress|Active", out, re.I):
                state = "running"
            elif re.search(r"paused|suspended", out, re.I):
                state = "paused"
            return _jr({"ok": True, "vd": vnum, "state": state, "progress": pct, "raw": out})
        body = payload or {}
        vnum = _raid_cc_vnum(body.get("vd"), vds)
        if vnum is None:
            return _jr({"ok": False, "error": "invalid vd"})
        action = str(body.get("action", "")).strip().lower()
        if action not in ("start", "pause", "resume", "stop"):
            return _jr({"ok": False, "error": "invalid action (start|pause|resume|stop)"})
        force = bool(body.get("force", False))
        cmd = [STORCLI, "/c0", "/v" + vnum, action, "cc"]
        if action == "start" and force:
            cmd.append("force")
        out = sudo_cmd(cmd, 30) or ""
        ok = ("Succeeded" in out) or ("Status = Success" in out)
        return _jr({"ok": ok, "vd": vnum, "action": action, "out": out})
    except Exception as ex:
        return _jr({"ok": False, "error": str(ex)})

@router.get("/api/raid/cc/schedule")
@router.post("/api/raid/cc/schedule")
def api_raid_cc_schedule(request: Request, payload: dict = Depends(json_body_as(RaidCcScheduleRequest))):
    """一致性检查定时调度配置。
    GET：返回当前配置；POST {enabled, period, hour, minute, weekday, vd}：保存。
    配置本身与是否存在 VD 无关（保存合法）；真正触发由 _check_cc_schedule 按时执行，
    无 VD 时静默跳过。前端仅在存在硬件逻辑盘时展示该配置 UI。"""
    if request.method == "GET":
        return _jr(_load_cc_schedule())
    try:
        body = payload or {}
        cfg = _load_cc_schedule()
        if "enabled" in body:
            cfg["enabled"] = bool(body["enabled"])
        if "period" in body:
            cfg["period"] = "weekly" if str(body["period"]) == "weekly" else "daily"
        if "hour" in body:
            cfg["hour"] = max(0, min(23, int(body["hour"])))
        if "minute" in body:
            cfg["minute"] = max(0, min(59, int(body["minute"])))
        if "weekday" in body:
            cfg["weekday"] = max(0, min(6, int(body["weekday"])))
        if "vd" in body:
            cfg["vd"] = body["vd"]
        _save_cc_schedule(cfg)
        return _jr({"ok": True, "config": cfg})
    except Exception as ex:
        return _jr({"ok": False, "error": str(ex)})

@router.get("/api/raid/hotspare")
@router.post("/api/raid/hotspare")
def api_raid_hotspare(request: Request, payload: dict = Depends(json_body_as(RaidHotspareRequest))):
    """阵列卡热备盘分配（全局 GHS / 专用 DHS）。
    仅对存在的硬件 RAID 环境有效；本机 JBOD 直通无 VD 时接口返回明确错误（不崩溃）。
    - GET            ：返回当前热备盘列表（含类型 global/dedicated）
    - POST {slot, action}：action = add(全局) | add_dedicated(专用) | remove
    """
    try:
        rc = get_raid_card()
        if rc.get("mode") != "mega":
            return _jr({"ok": False, "error": "当前非 MegaRAID 模式，无法管理热备盘"})
        if request.method == "GET":
            return _jr({"ok": True, "hotspares": rc.get("hotspares", [])})
        body = payload or {}
        slot = str(body.get("slot", "")).strip()
        m = re.match(r"^(\d+):(\d+)$", slot)
        if not m:
            return _jr({"ok": False, "error": "invalid slot (需形如 252:0)"})
        e, s = m.group(1), m.group(2)
        action = str(body.get("action", "")).strip().lower()
        if action not in ("add", "add_dedicated", "remove"):
            return _jr({"ok": False, "error": "invalid action (add|add_dedicated|remove)"})
        if action == "remove":
            cmd = [STORCLI, "/c0", "/e" + e, "/s" + s, "delete", "hotsparedrive"]
        else:
            cmd = [STORCLI, "/c0", "/e" + e, "/s" + s, "add", "hotsparedrive"]
            if action == "add_dedicated":
                cmd.append("dedicated")
        out = sudo_cmd(cmd, 30) or ""
        ok = ("Succeeded" in out) or ("Status = Success" in out)
        return _jr({"ok": ok, "action": action, "slot": slot, "out": out})
    except Exception as ex:
        return _jr({"ok": False, "error": str(ex)})

@router.get("/api/raid/copyback")
@router.post("/api/raid/copyback")
def api_raid_copyback(request: Request, payload: dict = Depends(json_body_as(RaidCopybackRequest))):
    """阵列卡 CopyBack 自动换盘（监控 + 触发）。
    仅对硬件 RAID（存在 VD）环境有效；本机 JBOD 直通无 VD 时接口返回明确错误（不崩溃）。
    - GET                  ：返回自动 CopyBack 开关状态 + 当前正在/待换盘的盘
    - POST {action}        ：action = enable | disable（开/关控制器自动 CopyBack）
    - POST {slot, action}  ：action = start（手动对故障盘触发 CopyBack，须先配热备盘）
    """
    try:
        rc = get_raid_card()
        if rc.get("mode") != "mega":
            return _jr({"ok": False, "error": "当前非 MegaRAID 模式，无 CopyBack 能力"})
        if not rc.get("virtual_drives"):
            return _jr({"ok": False, "error": "当前无硬件 RAID 逻辑盘（VD），CopyBack 仅在已配置热备盘的阵列环境下生效"})
        if request.method == "GET":
            return _jr({
                "ok": True,
                "auto_copyback": rc.get("auto_copyback", "unknown"),
                "copyback_active": [d["slot"] for d in rc.get("drives", []) if d.get("copyback_active")],
                "copyback_needed": rc.get("copyback_needed", []),
            })
        body = payload or {}
        action = str(body.get("action", "")).strip().lower()
        if action in ("enable", "disable"):
            val = "on" if action == "enable" else "off"
            out = sudo_cmd([STORCLI, "/c0", "set", "copyback=" + val], 30) or ""
            ok = ("Succeeded" in out) or ("Status = Success" in out)
            return _jr({"ok": ok, "action": action, "auto_copyback": val, "out": out})
        if action == "start":
            slot = str(body.get("slot", "")).strip()
            m = re.match(r"^(\d+):(\d+)$", slot)
            if not m:
                return _jr({"ok": False, "error": "invalid slot (需形如 252:0)"})
            e, s = m.group(1), m.group(2)
            out = sudo_cmd([STORCLI, "/c0", "/e" + e, "/s" + s, "start", "copyback"], 30) or ""
            ok = ("Succeeded" in out) or ("Status = Success" in out)
            return _jr({"ok": ok, "action": "start", "slot": slot, "out": out})
        return _jr({"ok": False, "error": "invalid action (enable|disable|start)"})
    except Exception as ex:
        return _jr({"ok": False, "error": str(ex)})