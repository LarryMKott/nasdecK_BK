"""过渡期 DI 组装根（backend 顶层，legacy 层级，G4 不检查）。

整洁架构组装根的最终归宿是 main.py（阶段 5）；在此先行集中装配，
让表现层只依赖本模块的访问器而不直接导入 infrastructure。
阶段 5 时本文件内容并入 main.py 后删除。

注意：仅构造对象、零 IO（采集器的命令执行都发生在方法调用时）。
"""
from backend.application.usecases.dashboard_usecase import DashboardUseCase
from backend.infrastructure.collectors.repo import HardwareCollectorRepo
from backend.infrastructure.fans.driver import HwmonFanDriver
from backend.infrastructure.health.alert_repo_impl import AlertRepoImpl
from backend.infrastructure.health.service import HealthService
from backend.infrastructure.persistence.history_repo_impl import HistoryRepoImpl
from backend.infrastructure.version import AppInfo


class Container:
    """手工组装的依赖容器（无框架，见开发计划前置决策 #4）。"""

    def __init__(self):
        self.collector = HardwareCollectorRepo()
        self.health = HealthService()
        self.app_info = AppInfo()
        self.history = HistoryRepoImpl()
        self.alert_repo = AlertRepoImpl()
        self.fan_driver = HwmonFanDriver()
        # ---- UseCase ----
        self.dashboard = DashboardUseCase(self.collector, self.health, self.app_info)


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container()
    return _container
