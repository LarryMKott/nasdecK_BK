from backend import shell

from backend.collectors.dockermod import get_docker
from backend.collectors.ports import _ICON_MIME
from backend.collectors.ports import _PORT_PROTECTED
from backend.collectors.ports import _app_icon_file
from backend.collectors.ports import _app_ids
from backend.collectors.ports import _proc_comm
from backend.collectors.ports import get_listening_ports
from backend.collectors.storage import get_storage
from backend.collectors.storage import get_volume_map
from backend.collectors.system import _SYSTEM_CACHE
from backend.collectors.system import get_network_nics
from backend.collectors.wol import _wol_apply
from backend.collectors.wol import _wol_load_cfg
from backend.collectors.wol import _wol_phy_name
from backend.collectors.wol import _wol_save_cfg
from backend.shell import sudo_cmd
from backend.web.routers.helpers import _panel_time
import os
import time

# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from backend.gateway import require_admin
from backend.web.deps import _jr, json_body

router = APIRouter()



@router.get("/api/storage")
def api_storage():
    """存储卷板块（mdadm/lsblk/df，均为本地快速命令）。volume_map = 卷映射树（v2.3.0 3.6）。"""
    t0 = time.time()
    try:
        storage = get_storage()
        try:
            storage["volume_map"] = get_volume_map()
        except Exception as e:
            storage["volume_map"] = {"volumes": [], "loose": [], "error": str(e)}
            shell.log("volume_map failed: " + str(e))
        return _jr({"storage": storage, "time": _panel_time(), "elapsed": round(time.time() - t0, 2)})
    except Exception as e:
        return _jr({"error": str(e), "time": _panel_time()})

@router.get("/api/docker")
def api_docker():
    """Docker 容器板块。"""
    t0 = time.time()
    try:
        return _jr({"docker": get_docker(), "time": _panel_time(), "elapsed": round(time.time() - t0, 2)})
    except Exception as e:
        return _jr({"error": str(e), "time": _panel_time()})

@router.post("/api/network/wol")
def api_network_wol(request: Request, payload: dict = Depends(json_body)):
    """设置网卡 WOL（网络唤醒）开关（v2.3.0 3.2）。
    仅对硬件支持该能力的物理口开放；成功后写进配置，进程启动时回放一次以跨重启保留。
    作用面严格限于用户点的那张网卡，不做其它系统改动。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "请求格式错误"}, 400)
    phy = _wol_phy_name(data.get("name"))
    enable = bool(data.get("enable"))
    if not phy:
        return _jr({"ok": False, "error": "缺少网卡名"}, 400)
    ok, err = _wol_apply(phy, enable)
    if not ok:
        return _jr({"ok": False, "error": err or "设置失败"}, 400)
    cfg = _wol_load_cfg()
    cfg[phy] = enable
    _wol_save_cfg(cfg)
    # 写成功后必须让「读」的缓存失效：get_network_nics 有 60s TTL 缓存、
    # get_system 有 30s TTL 缓存（检测页走 /api/all → system.nics），
    # 否则用户刷新页面会看到 60 秒内的旧状态（"刚关了又显示开着"）。
    try:
        get_network_nics.cache_clear()
    except Exception:
        pass
    try:
        _SYSTEM_CACHE["v"] = None
        _SYSTEM_CACHE["t"] = 0.0
    except Exception:
        pass
    return _jr({"ok": True, "name": phy, "enabled": enable})

@router.get("/api/network")
def api_network():
    """独立网卡接口：返回 get_network_nics() 结果（IP/MAC/速率/驱动/状态等）。
    不进 get_system 的 60s TTL 缓存，供前端「网络」卡片轻量(5s)刷新，
    避免被 RAID/SMART/Docker 等慢采集拖慢。"""
    try:
        nics = get_network_nics()
        resp = _jr({"nics": nics, "time": _panel_time()})
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        return _jr({"nics": [], "error": str(e)}), 500

@router.get("/api/ports")
def api_ports():
    """宿主机端口占用：列出所有监听端口 + 占用进程 + 是否 Docker 容器端口（v2.3.0 端口扫描）。"""
    try:
        return _jr({"ports": get_listening_ports(), "time": _panel_time()})
    except Exception as e:
        return _jr({"ports": [], "error": str(e)}), 500

@router.get("/api/ports/appicon")
def api_ports_appicon(request: Request):
    """返回应用中心某个已安装 app 的图标，供「端口占用」页标出是哪个应用在用端口。

    只接受已安装的 appid（_app_ids 白名单）+ 固定子路径，杜绝目录穿越；
    Docker 容器行也用这个接口——商店装的 docker 应用（homebox / cf-dns-select）会生成同名
    @appcenter 目录，容器名即 appid，同样命中白名单；纯 docker run 的容器没有图标源。
    找不到图标就 404，前端自动回退成纯文字。图片强缓存一天（图标基本不变）。
    """
    appid = (request.query_params.get("app") or "").strip()
    if not appid or appid not in _app_ids():
        return Response(status_code=404)
    path = _app_icon_file(appid)
    if not path:
        return Response(status_code=404)
    try:
        if not os.path.exists(path):
            return Response(status_code=404)
        mt = _ICON_MIME.get(os.path.splitext(path)[1].lower(), "image/png")
        resp = FileResponse(path, media_type=mt)
    except Exception:
        return Response(status_code=404)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@router.post("/api/ports/release")
def api_ports_release(request: Request, payload: dict = Depends(json_body)):
    """释放端口占用：终止占用该端口的进程（SIGTERM 优雅终止）。
    安全边界：禁止 kill 系统关键进程（nasdash 自身 / PID1 / systemd / dockerd / sshd 等）与 fnOS 核心服务；
    其余进程由用户在前端二次确认后承担后果。"""
    try:
        data = payload or {}
    except Exception:
        return _jr({"ok": False, "error": "请求格式错误"}, 400)
    pid = data.get("pid")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return _jr({"ok": False, "error": "PID 非法"}, 400)
    if pid <= 1:
        return _jr({"ok": False, "error": "PID 非法"}, 400)
    self_pid = os.getpid()
    if pid == self_pid:
        return _jr({"ok": False, "blocked": True, "error": "不能释放 nasdash 自身进程"}, 400)
    if pid == 1:
        return _jr({"ok": False, "blocked": True, "error": "该进程为系统关键进程，禁止释放"}, 400)
    comm = _proc_comm(pid)
    if comm in _PORT_PROTECTED:
        return _jr({"ok": False, "blocked": True, "error": "该进程为系统关键进程，禁止释放"}, 400)
    try:
        out = sudo_cmd(["kill", "-TERM", str(pid)], 5)
    except Exception as e:
        return _jr({"ok": False, "error": "释放失败：" + str(e)}, 500)
    return _jr({"ok": True, "pid": pid, "process": comm, "output": (out or "")[:200]})