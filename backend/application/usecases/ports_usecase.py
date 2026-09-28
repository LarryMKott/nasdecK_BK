"""端口占用面板用例（/api/ports 系列编排核，自端点原样搬迁）。

释放端口的安全策略：领域规则 domain.rules.port_rule 持有关键进程名单；
编排顺序（PID 校验 → 自身进程 → 关键进程 → 优雅终止）与旧实现逐行一致。
"""
import os
import time

from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.network_service import INetworkService
from backend.domain.rules.port_rule import is_protected


class PortsUseCase:
    def __init__(self, collector: IHardwareCollector, network: INetworkService):
        self._c = collector
        self._net = network

    def list_panel(self) -> dict:
        """宿主机端口占用：监听端口 + 占用进程 + 是否 Docker 容器端口（v2.3.0 端口扫描）。
        异常向上传播（端点映射 500）。"""
        return {"ports": self._c.get_listening_ports(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S")}

    def app_icon(self, appid: str) -> tuple | None:
        """应用中心 app 图标 (path, mime)；白名单外 / 无图标源返回 None（端点回退 404）。"""
        return self._net.port_app_icon(appid)

    def release_port(self, pid_raw) -> tuple:
        """释放端口占用：终止占用进程（SIGTERM 优雅终止）。返回 (http_status, body)。

        安全边界：禁止 kill 系统关键进程（nasdeck 自身 / PID1 / systemd / dockerd /
        sshd 等）与 fnOS 核心服务；其余进程由用户在前端二次确认后承担后果。"""
        try:
            pid = int(pid_raw)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "PID 非法"}
        if pid <= 1:
            return 400, {"ok": False, "error": "PID 非法"}
        if pid == os.getpid():
            return 400, {"ok": False, "blocked": True, "error": "不能释放 nasdeck 自身进程"}
        comm = self._net.proc_comm(pid)
        if is_protected(comm):
            return 400, {"ok": False, "blocked": True, "error": "该进程为系统关键进程，禁止释放"}
        try:
            out = self._net.kill_term(pid, 5)
        except Exception as e:
            return 500, {"ok": False, "error": "释放失败：" + str(e)}
        return 200, {"ok": True, "pid": pid, "process": comm, "output": (out or "")[:200]}
