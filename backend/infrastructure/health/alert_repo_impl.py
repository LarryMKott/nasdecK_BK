"""告警配置仓储适配器：实现 domain.interfaces.alert_repo.IAlertRepo。"""
from backend.domain.interfaces.alert_repo import IAlertRepo
from backend.infrastructure.health.alerts import _load_alerts, _save_alerts


class AlertRepoImpl(IAlertRepo):
    def load_alerts(self) -> dict:
        return _load_alerts()

    def save_alerts(self, cfg: dict) -> bool:
        return bool(_save_alerts(cfg))
