"""健康服务适配器：实现 domain.interfaces.health_service.IHealthService。

委托 alerts/diskhealth/report 的既有壳函数（IO 取数与降级语义原样）。
"""
from backend.domain.interfaces.health_service import IHealthService
from backend.infrastructure.logging.errring import _ERR_RING
from backend.infrastructure.health.alerts import _dispatch_notifications, _evaluate_alerts
from backend.infrastructure.health.diskhealth import _enrich_disk_channels
from backend.infrastructure.health.report import (_redact_report, _render_report_html, build_diagnostics,
                                  build_health_report, render_diagnostics_text)


class HealthService(IHealthService):
    def evaluate_alerts(self, system: dict, disks: list, docker: dict | None = None) -> list:
        return _evaluate_alerts(system, disks, docker)

    def enrich_disk_channels(self, disks: list, raid: dict) -> list:
        return _enrich_disk_channels(disks, raid)

    def dispatch_notifications(self, text: str, cfg: dict, level: str = "danger") -> dict:
        return _dispatch_notifications(text, cfg, level)

    def build_health_report(self) -> dict:
        return build_health_report()

    def redact_report(self, rep: dict) -> dict:
        return _redact_report(rep)

    def build_diagnostics(self) -> dict:
        return build_diagnostics()

    def render_diagnostics_text(self, diag: dict) -> str:
        return render_diagnostics_text(diag)

    def render_report_html(self, rep: dict):
        """整函数平移不拆（计划阶段 3 批 C）：返回现成的 HTMLResponse（含 Content-Type 头），端点直接透传。"""
        return _render_report_html(rep)

    def clear_error_ring(self) -> None:
        _ERR_RING.clear()
