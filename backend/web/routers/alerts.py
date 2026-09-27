from backend.collectors.board import get_board
from backend.collectors.board import get_memory_modules
from backend.collectors.disks import get_disks
from backend.collectors.dockermod import get_docker
from backend.collectors.system import get_system
from backend.health.alerts import _dispatch_notifications
from backend.health.alerts import _evaluate_alerts
from backend.health.alerts import _load_alerts
from backend.health.alerts import _save_alerts
from backend.health.report import _redact_report
from backend.health.report import _render_report_html
from backend.health.report import build_diagnostics
from backend.health.report import build_health_report
from backend.health.report import render_diagnostics_text
from backend.version import APP_VERSION
import time

# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.gateway import require_admin
from backend.web.deps import _jr, json_body, json_body_as
from backend.web.schemas import AlertsConfigRequest

router = APIRouter()



@router.get("/api/alerts")
def api_alerts():
    cfg = _load_alerts()
    system = get_system()
    disks = get_disks()
    docker = get_docker()
    system_full = {**system, "board": get_board(), "memory_modules": get_memory_modules()}
    alerts = _evaluate_alerts(system_full, disks, docker)
    return _jr({"config": cfg, "alerts": alerts, "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": True})

@router.get("/api/alerts/config")
def api_alerts_config_get():
    return _jr(_load_alerts())

@router.post("/api/alerts/config")
def api_alerts_config_set(request: Request, payload: dict = Depends(json_body_as(AlertsConfigRequest)), _admin = Depends(require_admin)):
    data = payload or {}
    cfg = _load_alerts()
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    if "disk_health" in data:
        cfg["disk_health"] = bool(data["disk_health"])
    if "memory_max" in data:
        try:
            cfg["memory_max"] = float(data["memory_max"])
        except (TypeError, ValueError):
            return _jr({"ok": False, "error": "memory_max 需为数字"}, 400)
    if "temp" in data and isinstance(data["temp"], dict):
        t = cfg["temp"]; nt = data["temp"]
        for k in ("cpu_max", "mb_max", "disk_max"):
            if k in nt:
                try:
                    t[k] = float(nt[k])
                except (TypeError, ValueError):
                    return _jr({"ok": False, "error": k + " 需为数字"}, 400)
        if "enabled" in nt:
            t["enabled"] = bool(nt["enabled"])
    if "channels" in data and isinstance(data["channels"], dict):
        ch = cfg["channels"]; nc = data["channels"]
        for name in ("telegram", "bark", "email"):
            if name in nc and isinstance(nc[name], dict):
                cur = ch.get(name, {})
                for fld in ("enabled", "bot_token", "chat_id", "url", "smtp_host", "smtp_port", "user", "pass", "to"):
                    if fld in nc[name]:
                        cur[fld] = nc[name][fld]
                ch[name] = cur
        if "system" in nc:
            ch["system"] = bool(nc["system"])
    if "level_channels" in data and isinstance(data["level_channels"], dict):
        lc = cfg.get("level_channels", {})
        for lvl in ("danger", "warn", "info"):
            if lvl in data["level_channels"]:
                val = data["level_channels"][lvl]
                if isinstance(val, list):
                    lc[lvl] = [c for c in val if c in ("system", "telegram", "bark", "email")]
        cfg["level_channels"] = lc
    if _save_alerts(cfg):
        return _jr({"ok": True, "config": cfg})
    return _jr({"ok": False, "error": "写配置失败"}), 500

@router.post("/api/alerts/test")
def api_alerts_test(_admin = Depends(require_admin)):
    cfg = _load_alerts()
    text = "这是一条来自 nasdash 的测试通知（当前版本 %s）。若收到说明通知渠道配置正确。" % APP_VERSION
    res = _dispatch_notifications(text, cfg, "danger")
    return _jr({"ok": True, "results": res})

@router.get("/api/report")
def api_report(request: Request):
    fmt = request.query_params.get("format", "json")
    rep = build_health_report()
    # redact=1：贴论坛前把内网 IP / MAC / 序列号 / 主机名 / 云盘账号打码（html 与 json 都生效）
    if str(request.query_params.get("redact", "")).lower() in ("1", "true", "yes", "on"):
        rep = _redact_report(rep)
    if fmt == "html":
        return _render_report_html(rep)
    return _jr(rep)

@router.get("/api/diagnostics")
def api_diagnostics(request: Request):
    """设备诊断包：txt=可复制纯文本（贴论坛用），json=结构化。隐私安全、不联网。"""
    fmt = request.query_params.get("format", "txt")
    diag = build_diagnostics()
    if fmt == "json":
        return _jr(diag)
    return Response(render_diagnostics_text(diag), mimetype="text/plain; charset=utf-8")