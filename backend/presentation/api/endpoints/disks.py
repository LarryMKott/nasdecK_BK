# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3：编排收敛至 application.usecases（硬盘面板 + 自检任务用例），
# 端点只做 解析→用例→透传。
from fastapi import APIRouter, Depends, Request

from backend.container import get_container
from backend.infrastructure.gateway import require_admin
from backend.presentation.api.dependencies import _jr, json_body_as
from backend.presentation.schemas import DiskRenameRequest, DiskSelftestAbortRequest, DiskSelftestRequest

router = APIRouter()



@router.get("/api/disks")
def api_disks(request: Request):
    """硬盘 SMART 板块。

    standalone（是否独立盘，决定能否跑破坏性 badblocks）必须实时计算，
    不能跟着 get_disks 的 300s 缓存走——否则用户刚从阵列移除硬盘，
    B 档按钮要等 5 分钟缓存过期才变绿。SMART 等重数据仍走缓存。
    """
    return _jr(get_container().disks.panel(request.query_params.get("force") == "1"))

@router.post("/api/disks/rename")
def api_disks_rename(request: Request, payload: dict = Depends(json_body_as(DiskRenameRequest))):
    """设置/清除运维自定义盘名（v2.3.0 3.8）。body: {key, name}；name 空串 = 清除。

    key 用 /api/disks 返回的 name_key（序列号优先，与缺陷趋势同源），
    前端不自己拼 key，避免口径漂移。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "请求格式错误"}, 400)
    status, body = get_container().disks.rename(data)
    return _jr(body, status)

@router.get("/api/disks/selftest")
def api_disks_selftest_get():
    """返回当前硬盘自检任务状态（只读）。"""
    return _jr(get_container().selftest.status())

@router.get("/api/disks/selftest/history")
def api_disks_selftest_history(request: Request):
    """返回硬盘自检历史记录（新→旧，最多 50 条）。"""
    dev = (request.query_params.get("dev") or "").strip()
    return _jr(get_container().selftest.history(dev))

@router.post("/api/disks/selftest")
def api_disks_selftest_post(request: Request, payload: dict = Depends(json_body_as(DiskSelftestRequest)), _admin = Depends(require_admin)):
    """启动硬盘自检：type=long（SMART 长自检）/ badblocks（破坏性，仅独立盘）/ surface（只读表面扫描，所有盘）。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().selftest.start(data)
    return _jr(body, status)

@router.post("/api/disks/selftest/abort")
def api_disks_selftest_abort(request: Request, payload: dict = Depends(json_body_as(DiskSelftestAbortRequest)), _admin = Depends(require_admin)):
    """中止指定硬盘的自检任务。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "bad json"}, 400)
    status, body = get_container().selftest.abort(data)
    return _jr(body, status)
