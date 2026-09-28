"""健康服务抽象契约（阶段 3 扩展）。

实现方：backend/infrastructure/health/service.py（委托 alerts/diskhealth/report 的既有壳函数）。
"""
from abc import ABC, abstractmethod


class IHealthService(ABC):
    """健康域应用服务：告警评估 / 通知分发 / 磁盘通道标注 / 健康报告与诊断包（IO 在实现方完成）。"""

    @abstractmethod
    def evaluate_alerts(self, system: dict, disks: list, docker: dict | None = None) -> list:
        """扫描当前状态返回活动告警（旧 dict 形态，level/title/detail）。"""

    @abstractmethod
    def enrich_disk_channels(self, disks: list, raid: dict) -> list:
        """给每块盘标注通道来源（阵列卡 / 主板），供前端「物理硬盘信息」区分。"""

    @abstractmethod
    def dispatch_notifications(self, text: str, cfg: dict, level: str = "danger") -> dict:
        """按「严重级别」把告警文本推送到该级别启用的渠道，返回 {channel: ok|error}。"""

    @abstractmethod
    def build_health_report(self) -> dict:
        """汇总当前全部硬件状态 + 活动告警，生成健康报告快照。"""

    @abstractmethod
    def redact_report(self, rep: dict) -> dict:
        """报告脱敏：内网 IP / MAC / 序列号 / 主机名 / 云盘账号打码（贴论坛前用）。"""

    @abstractmethod
    def build_diagnostics(self) -> dict:
        """生成设备诊断包（结构化 dict，隐私安全、不联网）。"""

    @abstractmethod
    def render_diagnostics_text(self, diag: dict) -> str:
        """把诊断包渲染成可复制的纯文本。"""

    @abstractmethod
    def render_report_html(self, rep: dict):
        """报告 HTML 渲染（整函数平移不拆）；返回现成的 HTML 响应对象（含 Content-Type 头），端点直接透传。"""
