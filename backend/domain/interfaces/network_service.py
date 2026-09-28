"""网络服务抽象契约（阶段 3 扩展）：网卡查询 / WOL 设置 / 进程终止 / 应用图标。

实现方：backend/infrastructure/collectors/network_service.py。
"""
from abc import ABC, abstractmethod


class INetworkService(ABC):
    @abstractmethod
    def nics(self) -> list:
        """网卡列表（IP/MAC/协商速率/驱动/状态等，60s TTL 缓存）。"""

    @abstractmethod
    def set_wol(self, name, enable: bool) -> dict:
        """设置网卡 WOL（网络唤醒）开关（v2.3.0 3.2）。

        流程：物理口名解析 → ethtool 应用 → 配置落盘（进程启动时回放）→ 让「读」缓存失效。
        返回 {"ok": bool, "phy": str|None, "enabled": bool|None, "err": str|None}；
        phy=None 表示网卡名非法（err="缺少网卡名"）。
        """

    @abstractmethod
    def proc_comm(self, pid: int) -> str:
        """读 /proc/<pid>/comm 进程短名。"""

    @abstractmethod
    def kill_term(self, pid: int, timeout: int = 5) -> str:
        """sudo kill -TERM 优雅终止进程；失败抛异常，成功返回命令输出。"""

    @abstractmethod
    def port_app_icon(self, appid: str) -> tuple | None:
        """应用中心已安装 app 的图标 (path, mime)；appid 不在白名单 / 无图标源返回 None。"""
