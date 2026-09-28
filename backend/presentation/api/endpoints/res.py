# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3：编排收敛至 application.usecases（存储/Docker/网络/端口四个面板用例），
# 端点只做 解析→用例→透传；文件响应（图标）仍属表现层本职。
import os

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from backend.container import get_container
from backend.presentation.api.dependencies import _jr, json_body_as
from backend.presentation.schemas import NetworkWolRequest, PortsReleaseRequest

router = APIRouter()


@router.get("/api/storage")
def api_storage():
    """存储卷板块（mdadm/lsblk/df，均为本地快速命令）。volume_map = 卷映射树（v2.3.0 3.6）。"""
    return _jr(get_container().storage.panel())

@router.get("/api/docker")
def api_docker():
    """Docker 容器板块。"""
    return _jr(get_container().docker.panel())

@router.post("/api/network/wol")
def api_network_wol(request: Request, payload: dict = Depends(json_body_as(NetworkWolRequest))):
    """设置网卡 WOL（网络唤醒）开关（v2.3.0 3.2）。
    仅对硬件支持该能力的物理口开放；成功后写进配置，进程启动时回放一次以跨重启保留。
    作用面严格限于用户点的那张网卡，不做其它系统改动。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "请求格式错误"}, 400)
    status, body = get_container().network_uc.set_wol(data)
    return _jr(body, status)

@router.get("/api/network")
def api_network():
    """独立网卡接口：返回 get_network_nics() 结果（IP/MAC/速率/驱动/状态等）。
    不进 get_system 的 60s TTL 缓存，供前端「网络」卡片轻量(5s)刷新，
    避免被 RAID/SMART/Docker 等慢采集拖慢。"""
    try:
        resp = _jr(get_container().network_uc.nics())
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        return _jr({"nics": [], "error": str(e)}, 500)

@router.get("/api/ports")
def api_ports():
    """宿主机端口占用：列出所有监听端口 + 占用进程 + 是否 Docker 容器端口（v2.3.0 端口扫描）。"""
    try:
        return _jr(get_container().ports.list_panel())
    except Exception as e:
        return _jr({"ports": [], "error": str(e)}, 500)

@router.get("/api/ports/appicon")
def api_ports_appicon(request: Request):
    """返回应用中心某个已安装 app 的图标，供「端口占用」页标出是哪个应用在用端口。

    只接受已安装的 appid（_app_ids 白名单）+ 固定子路径，杜绝目录穿越；
    Docker 容器行也用这个接口——商店装的 docker 应用（homebox / cf-dns-select）会生成同名
    @appcenter 目录，容器名即 appid，同样命中白名单；纯 docker run 的容器没有图标源。
    找不到图标就 404，前端自动回退成纯文字。图片强缓存一天（图标基本不变）。
    """
    appid = (request.query_params.get("app") or "").strip()
    icon = get_container().ports.app_icon(appid)
    if not icon:
        return Response(status_code=404)
    path, mt = icon
    try:
        if not os.path.exists(path):
            return Response(status_code=404)
        resp = FileResponse(path, media_type=mt)
    except Exception:
        return Response(status_code=404)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@router.post("/api/ports/release")
def api_ports_release(request: Request, payload: dict = Depends(json_body_as(PortsReleaseRequest))):
    """释放端口占用：终止占用该端口的进程（SIGTERM 优雅终止）。
    安全边界：禁止 kill 系统关键进程（nasdeck 自身 / PID1 / systemd / dockerd / sshd 等）与 fnOS 核心服务；
    其余进程由用户在前端二次确认后承担后果。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "请求格式错误"}, 400)
    status, body = get_container().ports.release_port(data.get("pid"))
    return _jr(body, status)
