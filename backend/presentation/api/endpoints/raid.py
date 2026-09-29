# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3 批 B：编排收敛至 application.usecases.raid_usecase，
# 端点只做 解析→用例→透传。
from fastapi import APIRouter, Depends, Request

from backend.container import get_container
from backend.presentation.api.dependencies import _jr, json_body_as
from backend.presentation.schemas import RaidLocateRequest, RaidCcRequest, RaidCcScheduleRequest, RaidHotspareRequest, RaidCopybackRequest

router = APIRouter()



@router.get("/api/raid")
def api_raid():
    """阵列卡板块。#raid 渲染同时依赖 raid + disks，故一并返回（raid 带 60s 缓存，disks 带 300s 缓存）。"""
    return _jr(get_container().raid_uc.panel())

@router.post("/api/raid/locate")
def api_raid_locate(request: Request, payload: dict = Depends(json_body_as(RaidLocateRequest))):
    """物理盘定位（locate LED 闪烁）。仅当控制器/背板支持时可用（locate_supported）。"""
    try:
        body = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    return _jr(get_container().raid_uc.locate(body))

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
        body = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    return _jr(get_container().raid_uc.cc(request.method, request.query_params.get("vd"), body))

@router.get("/api/raid/cc/schedule")
@router.post("/api/raid/cc/schedule")
def api_raid_cc_schedule(request: Request, payload: dict = Depends(json_body_as(RaidCcScheduleRequest))):
    """一致性检查定时调度配置。
    GET：返回当前配置；POST {enabled, period, hour, minute, weekday, vd}：保存。
    配置本身与是否存在 VD 无关（保存合法）；真正触发由 _check_cc_schedule 按时执行，
    无 VD 时静默跳过。前端仅在存在硬件逻辑盘时展示该配置 UI。"""
    try:
        body = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    return _jr(get_container().raid_uc.cc_schedule(request.method, body))

@router.get("/api/raid/hotspare")
@router.post("/api/raid/hotspare")
def api_raid_hotspare(request: Request, payload: dict = Depends(json_body_as(RaidHotspareRequest))):
    """阵列卡热备盘分配（全局 GHS / 专用 DHS）。
    仅对存在的硬件 RAID 环境有效；本机 JBOD 直通无 VD 时接口返回明确错误（不崩溃）。
    - GET            ：返回当前热备盘列表（含类型 global/dedicated）
    - POST {slot, action}：action = add(全局) | add_dedicated(专用) | remove
    """
    try:
        body = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    return _jr(get_container().raid_uc.hotspare(request.method, body))

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
        body = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    return _jr(get_container().raid_uc.copyback(request.method, body))
