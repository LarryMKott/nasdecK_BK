"""硬件采集抽象契约。

实现方：backend/infrastructure/collectors/（现有采集函数逐个委托，逻辑零改动）。
出参暂为既有 dict/list 载荷（API 契约冻结期不做实体化改造，见开发计划前置决策 #8）；
后续按用例需要逐步引入领域实体。
"""
from abc import ABC, abstractmethod


class IHardwareCollector(ABC):
    """硬件状态采集门面（只读）。"""

    @abstractmethod
    def get_system(self, force: bool = False) -> dict:
        """系统概览（主机名/系统/内核/运行时间/内存/CPU 温度等，30s 缓存）。"""

    @abstractmethod
    def get_disks(self, force: bool = False) -> list:
        """全部物理盘（SATA/SAS/NVMe + 阵列卡背后的物理盘）。"""

    @abstractmethod
    def get_raid_card(self) -> dict:
        """阵列卡信息（storcli；无卡返回空 dict）。"""

    @abstractmethod
    def get_storage(self) -> dict:
        """存储卷容量（df 聚合）。"""

    @abstractmethod
    def get_volume_map(self) -> dict:
        """卷→盘映射（volumes / loose 两段）。"""

    @abstractmethod
    def get_docker(self) -> dict:
        """Docker 容器状态（5s TTL 缓存）。"""

    @abstractmethod
    def get_listening_ports(self) -> list:
        """监听端口占用（含进程/应用归属）。"""

    @abstractmethod
    def get_cpu_usage(self) -> dict:
        """CPU 使用率（1s 采样缓存）。"""

    @abstractmethod
    def get_board(self) -> dict:
        """主板信息（DMI 直读）。"""

    @abstractmethod
    def get_memory_modules(self) -> list:
        """内存模组（SPD 直读品牌/颗粒厂，含空槽）。"""

    @abstractmethod
    def get_network_nics(self) -> list:
        """网卡列表（IP/MAC/协商速率/驱动等）。"""

    @abstractmethod
    def get_rapl_power(self) -> dict:
        """RAPL 实时功耗（Intel 平台；无则空 dict）。"""

    # ---- 阶段 3 扩展（UseCase 落地需要）----

    @abstractmethod
    def get_gpu_live(self) -> list:
        """显卡实时状态（显存占用/频率等；独显驱动直读，核显走 i915 GEM）。"""

    @abstractmethod
    def get_realtime_metrics(self) -> dict:
        """实时指标（{"net": [...], "disk": [...], ...}，1s 粒度差分）。"""
