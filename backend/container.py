"""过渡期 DI 组装根（backend 顶层，legacy 层级，G4 不检查）。

整洁架构组装根的最终归宿是 main.py（阶段 5）；在此先行集中装配，
让表现层只依赖本模块的访问器而不直接导入 infrastructure。
阶段 5 时本文件内容并入 main.py 后删除。

注意：仅构造对象、零 IO（采集器的命令执行都发生在方法调用时）。
"""
from backend.application.usecases.alerts_usecase import AlertsUseCase
from backend.application.usecases.dashboard_usecase import DashboardUseCase
from backend.application.usecases.disk_selftest_usecase import DiskSelftestUseCase
from backend.application.usecases.disks_usecase import DisksUseCase
from backend.application.usecases.docker_usecase import DockerUseCase
from backend.application.usecases.health_report_usecase import HealthReportUseCase
from backend.application.usecases.history_query_usecase import HistoryQueryUseCase
from backend.application.usecases.metrics_usecase import MetricsUseCase
from backend.application.usecases.network_usecase import NetworkUseCase
from backend.application.usecases.ports_usecase import PortsUseCase
from backend.application.usecases.storage_usecase import StorageUseCase
from backend.infrastructure.collectors.disktest_service import DiskTestService
from backend.infrastructure.collectors.network_service import NetworkService
from backend.infrastructure.collectors.repo import HardwareCollectorRepo
from backend.infrastructure.command import cmd_runner
from backend.infrastructure.fans.driver import HwmonFanDriver
from backend.infrastructure.health.alert_repo_impl import AlertRepoImpl
from backend.infrastructure.health.disknames_repo_impl import DiskNamesRepoImpl
from backend.infrastructure.health.service import HealthService
from backend.infrastructure.persistence.history_repo_impl import HistoryRepoImpl
from backend.infrastructure.version import AppInfo


class Container:
    """手工组装的依赖容器（无框架，见开发计划前置决策 #4）。"""

    def __init__(self):
        self.collector = HardwareCollectorRepo()
        self.network = NetworkService()
        self.health = HealthService()
        self.app_info = AppInfo()
        self.history = HistoryRepoImpl()
        self.alert_repo = AlertRepoImpl()
        self.fan_driver = HwmonFanDriver()
        self.disk_names = DiskNamesRepoImpl()
        self.disktest = DiskTestService()
        # ---- UseCase ----
        self.dashboard = DashboardUseCase(self.collector, self.health, self.app_info)
        self.storage = StorageUseCase(self.collector, log=cmd_runner.log)
        self.docker = DockerUseCase(self.collector)
        self.ports = PortsUseCase(self.collector, self.network)
        self.network_uc = NetworkUseCase(self.network)
        self.alerts = AlertsUseCase(self.collector, self.health, self.alert_repo, self.app_info)
        self.report = HealthReportUseCase(self.health)
        self.metrics = MetricsUseCase(self.collector)
        self.history_uc = HistoryQueryUseCase(self.history)
        self.disks = DisksUseCase(self.collector, self.health, self.disk_names)
        self.selftest = DiskSelftestUseCase(self.collector, self.disktest)


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container()
    return _container
