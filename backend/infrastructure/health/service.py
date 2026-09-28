"""健康服务适配器：实现 domain.interfaces.health_service.IHealthService。

委托 alerts/diskhealth 的既有壳函数（IO 取数与降级语义原样）。
"""
from backend.domain.interfaces.health_service import IHealthService
from backend.infrastructure.health.alerts import _evaluate_alerts
from backend.infrastructure.health.diskhealth import _enrich_disk_channels


class HealthService(IHealthService):
    def evaluate_alerts(self, system: dict, disks: list, docker: dict | None = None) -> list:
        return _evaluate_alerts(system, disks, docker)

    def enrich_disk_channels(self, disks: list, raid: dict) -> list:
        return _enrich_disk_channels(disks, raid)
