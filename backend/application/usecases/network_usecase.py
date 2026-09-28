"""网络面板用例（/api/network 系列编排核，自端点原样搬迁）。"""
import time

from backend.domain.interfaces.network_service import INetworkService


class NetworkUseCase:
    def __init__(self, network: INetworkService):
        self._net = network

    def nics(self) -> dict:
        """独立网卡接口载荷：不进 get_system 的 TTL 缓存，供前端「网络」卡片轻量(5s)刷新，
        避免被 RAID/SMART/Docker 等慢采集拖慢。异常向上传播（端点映射 500）。"""
        return {"nics": self._net.nics(), "time": time.strftime("%Y-%m-%d %H:%M:%S")}

    def set_wol(self, data: dict) -> tuple:
        """设置网卡 WOL 开关（v2.3.0 3.2）。仅对硬件支持该能力的物理口开放；
        成功后写进配置，进程启动时回放一次以跨重启保留。
        作用面严格限于用户点的那张网卡，不做其它系统改动。返回 (http_status, body)。"""
        r = self._net.set_wol((data or {}).get("name"), bool((data or {}).get("enable")))
        if not r["ok"]:
            return 400, {"ok": False, "error": r["err"] or "设置失败"}
        return 200, {"ok": True, "name": r["phy"], "enabled": r["enabled"]}
