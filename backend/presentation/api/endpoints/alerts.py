# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3：编排收敛至 application.usecases（告警面板 + 健康报告/诊断包用例），
# 端点只做 解析→用例→透传；纯文本诊断响应属表现层本职。
# 【契约回归修复】/api/diagnostics 文本分支：迁移期丢失 Response 导入且 mimetype= 为
# Flask 专参，默认 format=txt 在 FastAPI 下必 NameError→500；恢复 Flask 原语义
# （200 + text/plain; charset=utf-8）。
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from backend.container import get_container
from backend.infrastructure.gateway import require_admin
from backend.presentation.api.dependencies import _jr, json_body_as
from backend.presentation.schemas import AlertsConfigRequest

router = APIRouter()



@router.get("/api/alerts")
def api_alerts():
    return _jr(get_container().alerts.panel())

@router.get("/api/alerts/config")
def api_alerts_config_get():
    return _jr(get_container().alerts.get_config())

@router.post("/api/alerts/config")
def api_alerts_config_set(request: Request, payload: dict = Depends(json_body_as(AlertsConfigRequest)), _admin = Depends(require_admin)):
    data = payload or {}
    status, body = get_container().alerts.set_config(data)
    return _jr(body, status)

@router.post("/api/alerts/test")
def api_alerts_test(_admin = Depends(require_admin)):
    return _jr(get_container().alerts.test_notify())

@router.get("/api/report")
def api_report(request: Request):
    uc = get_container().report
    # redact=1：贴论坛前把内网 IP / MAC / 序列号 / 主机名 / 云盘账号打码（html 与 json 都生效）
    redact = str(request.query_params.get("redact", "")).lower() in ("1", "true", "yes", "on")
    if request.query_params.get("format", "json") == "html":
        return uc.report_html(redact)
    return _jr(uc.report(redact))

@router.get("/api/diagnostics")
def api_diagnostics(request: Request):
    """设备诊断包：txt=可复制纯文本（贴论坛用），json=结构化。隐私安全、不联网。"""
    uc = get_container().report
    diag = uc.diagnostics()
    if request.query_params.get("format", "txt") == "json":
        return _jr(diag)
    return Response(uc.diagnostics_text(diag), media_type="text/plain; charset=utf-8")
