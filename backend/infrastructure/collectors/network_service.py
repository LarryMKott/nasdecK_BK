"""网络服务适配器：实现 domain.interfaces.network_service.INetworkService。

委托 wol / ports / system 既有函数（IO 与缓存语义原样）。
"""
from backend.domain.interfaces.network_service import INetworkService
from backend.infrastructure.collectors.ports import (_ICON_MIME, _app_icon_file, _app_ids,
                                                     _proc_comm)
from backend.infrastructure.collectors.system import _SYSTEM_CACHE, get_network_nics
from backend.infrastructure.collectors.wol import (_wol_apply, _wol_load_cfg, _wol_phy_name,
                                                   _wol_save_cfg)
from backend.infrastructure.command.cmd_runner import sudo_cmd


class NetworkService(INetworkService):
    def nics(self) -> list:
        return get_network_nics()

    def set_wol(self, name, enable: bool) -> dict:
        phy = _wol_phy_name(name)
        if not phy:
            return {"ok": False, "phy": None, "enabled": None, "err": "缺少网卡名"}
        ok, err = _wol_apply(phy, enable)
        if not ok:
            return {"ok": False, "phy": phy, "enabled": None, "err": err or "设置失败"}
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
        return {"ok": True, "phy": phy, "enabled": enable, "err": None}

    def proc_comm(self, pid: int) -> str:
        return _proc_comm(pid)

    def kill_term(self, pid: int, timeout: int = 5) -> str:
        return sudo_cmd(["kill", "-TERM", str(pid)], timeout)

    def port_app_icon(self, appid: str) -> tuple | None:
        # 只接受已安装的 appid（_app_ids 白名单）+ 固定子路径，杜绝目录穿越；
        # Docker 容器行也用这个接口——商店装的 docker 应用会生成同名 @appcenter 目录，
        # 容器名即 appid，同样命中白名单；纯 docker run 的容器没有图标源。
        if not appid or appid not in _app_ids():
            return None
        path = _app_icon_file(appid)
        if not path:
            return None
        return path, _ICON_MIME.get(_ext(path), "image/png")


def _ext(path: str) -> str:
    import os
    return os.path.splitext(path)[1].lower()
