"""存储卷面板用例（/api/storage 编排核，自端点原样搬迁）。"""
import time

from backend.domain.interfaces.collector_repo import IHardwareCollector


class StorageUseCase:
    def __init__(self, collector: IHardwareCollector, log=None):
        self._c = collector
        self._log = log  # 基础设施日志回调（cmd_runner.log），由组装根注入

    def panel(self) -> dict:
        """存储卷板块（mdadm/lsblk/df，均为本地快速命令）。volume_map = 卷映射树（v2.3.0 3.6）。"""
        t0 = time.time()
        try:
            storage = self._c.get_storage()
            try:
                storage["volume_map"] = self._c.get_volume_map()
            except Exception as e:
                storage["volume_map"] = {"volumes": [], "loose": [], "error": str(e)}
                if self._log:
                    self._log("volume_map failed: " + str(e))
            return {"storage": storage, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "elapsed": round(time.time() - t0, 2)}
        except Exception as e:
            return {"error": str(e), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
