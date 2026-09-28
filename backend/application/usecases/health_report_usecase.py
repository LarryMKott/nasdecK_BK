"""健康报告 / 设备诊断包用例：/api/report 与 /api/diagnostics 的编排核。

自 presentation/api/endpoints/alerts.py 原样搬迁（v2.4 阶段 3 批 A 第三单，计划批 C 项前置）。
报告构建与脱敏在基础设施（health/report.py，渲染大函数整函数平移不拆）；
HTML 响应对象由基础设施渲染好直接透传端点（application 源码不接触 fastapi）。
"""
from backend.domain.interfaces.health_service import IHealthService


class HealthReportUseCase:
    def __init__(self, health: IHealthService):
        self._health = health

    def report(self, redact: bool = False) -> dict:
        """健康报告快照；redact=True 时打码内网 IP / MAC / 序列号 / 主机名 / 云盘账号（贴论坛前用）。"""
        rep = self._health.build_health_report()
        # redact=1：贴论坛前把内网 IP / MAC / 序列号 / 主机名 / 云盘账号打码（html 与 json 都生效）
        if redact:
            rep = self._health.redact_report(rep)
        return rep

    def report_html(self, redact: bool = False):
        """/api/report?format=html：现成 HTML 响应对象（含 Content-Type 头），端点直接返回。"""
        return self._health.render_report_html(self.report(redact))

    def diagnostics(self) -> dict:
        """设备诊断包（结构化）。隐私安全、不联网。"""
        return self._health.build_diagnostics()

    def diagnostics_text(self, diag: dict) -> str:
        """诊断包 → 可复制纯文本（贴论坛用）。"""
        return self._health.render_diagnostics_text(diag)
