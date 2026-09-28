"""Docker 面板用例（/api/docker 编排核，自端点原样搬迁）。"""
import time

from backend.domain.interfaces.collector_repo import IHardwareCollector


class DockerUseCase:
    def __init__(self, collector: IHardwareCollector):
        self._c = collector

    def panel(self) -> dict:
        """Docker 容器板块。"""
        t0 = time.time()
        try:
            return {"docker": self._c.get_docker(), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "elapsed": round(time.time() - t0, 2)}
        except Exception as e:
            return {"error": str(e), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
