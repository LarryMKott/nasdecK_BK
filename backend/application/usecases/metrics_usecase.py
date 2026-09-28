"""系统资源实时指标用例：/api/metrics 的编排核（轻量实时指标，供前端 1s 高频轮询）。

自 presentation/api/endpoints/metrics.py 原样搬迁（v2.4 阶段 3 批 A 第三单）：
OVS 桥归并 / diskio 元数据补全 / 各读数独立容错逐行等价；
数据获取全部经 IHardwareCollector（重型采集不触发，元数据走 300s TTL 缓存）。
"""
import time

from backend.domain.interfaces.collector_repo import IHardwareCollector


class MetricsUseCase:
    def __init__(self, collector: IHardwareCollector):
        self._c = collector

    def panel(self) -> dict:
        """轻量实时指标：网络吞吐 + 磁盘 I/O + CPU/内存/负载。供前端高频(1s)轮询，不触发重型 /api/all(阵列卡/SMART等)。
        网络速率会把 OVS 桥（如 eno1-ovs）归并到物理口名（eno1），与 /api/system 的合并显示名对齐，
        这样前端按 data-nic 直接覆盖即可。"""
        try:
            rt = self._c.get_realtime_metrics()
            net = rt.get("net", [])
            merged = {}
            for e in net:
                name = e.get("name", "")
                if name.endswith("-ovs"):
                    base = name[:-4]
                    merged[base] = {"name": base,
                                    "rx_rate": e.get("rx_rate", 0.0),
                                    "tx_rate": e.get("tx_rate", 0.0)}
                elif name not in merged:
                    merged[name] = {"name": name,
                                    "rx_rate": e.get("rx_rate", 0.0),
                                    "tx_rate": e.get("tx_rate", 0.0)}
            diskio = rt["disk"]
            try:
                disk_map = self._c.get_disks_meta_map()
            except Exception:
                disk_map = {}
            for d in diskio:
                info = disk_map.get(d["device"], {})
                d["model"] = info.get("model", "")
                d["size"] = info.get("size", "")
                d["brand"] = info.get("brand", "")
                d["type"] = info.get("type", "")
            # 实时 CPU / 内存 / 负载（无缓存，供前端 1s 增量刷新）。
            # CPU 走 get_cpu_usage 的 1s 窗口缓存（daemon 每 1s 刷新；异常时就地 1s 采样兜底），
            # 采样窗口恒为 ~1s，与 fnOS 口径一致，不再有毫秒级窗口导致的瞬时 100%/50% 尖刺。
            try:
                cpu_usage = self._c.get_cpu_usage()
            except Exception:
                cpu_usage = None
            try:
                mem_percent = self._c.get_live_mem_percent()
            except Exception:
                mem_percent = None
            try:
                load = self._c.get_load_avg()
            except Exception:
                load = None
            # CPU 当前频率（全部核心瞬时值的平均，口径同 /api/system 的 cpu_info.current_freq_mhz）。
            try:
                cpu_freq_mhz = self._c.get_cpu_freq_mhz()
            except Exception:
                cpu_freq_mhz = None
            return {"net": list(merged.values()), "diskio": diskio,
                    "cpu_usage": cpu_usage, "mem_percent": mem_percent, "load": load,
                    "cpu_freq_mhz": cpu_freq_mhz,
                    "gpu": self._c.get_gpu_live(),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        except Exception as e:
            return {"error": str(e), "net": [], "diskio": []}
