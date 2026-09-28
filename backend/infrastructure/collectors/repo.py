"""硬件采集适配器：实现 domain.interfaces.collector_repo.IHardwareCollector。

委托既有采集函数（逻辑零改动）；阶段 3 UseCase 经构造注入使用本类，
单元测试以内存假实现替换（无需真实硬件）。
"""
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.infrastructure.collectors.board import get_board, get_memory_modules
from backend.infrastructure.collectors.cpuusage import get_cpu_usage
from backend.infrastructure.collectors.dockermod import get_docker
from backend.infrastructure.collectors.gpu import _get_gpu_live
from backend.infrastructure.collectors.metrics import get_realtime_metrics
from backend.infrastructure.collectors.ports import get_listening_ports
from backend.infrastructure.collectors.raid import get_raid_card
from backend.infrastructure.collectors.storage import get_storage, get_volume_map
from backend.infrastructure.collectors.system import get_network_nics, get_rapl_power, get_system
from backend.infrastructure.collectors.disks import get_disks


class HardwareCollectorRepo(IHardwareCollector):
    """采集门面（只读）：方法与既有函数一一对应。"""

    def get_system(self, force: bool = False) -> dict:
        return get_system(force)

    def get_disks(self, force: bool = False) -> list:
        return get_disks(force)

    def get_raid_card(self) -> dict:
        return get_raid_card()

    def get_storage(self) -> dict:
        return get_storage()

    def get_volume_map(self) -> dict:
        return get_volume_map()

    def get_docker(self) -> dict:
        return get_docker()

    def get_listening_ports(self) -> list:
        return get_listening_ports()

    def get_cpu_usage(self) -> dict:
        return get_cpu_usage()

    def get_board(self) -> dict:
        return get_board()

    def get_memory_modules(self) -> list:
        return get_memory_modules()

    def get_network_nics(self) -> list:
        return get_network_nics()

    def get_rapl_power(self) -> dict:
        return get_rapl_power()

    def get_gpu_live(self) -> list:
        return _get_gpu_live()

    def get_realtime_metrics(self) -> dict:
        return get_realtime_metrics()
