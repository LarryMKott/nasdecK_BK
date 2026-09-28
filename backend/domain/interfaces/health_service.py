"""健康服务抽象契约（阶段 3 扩展）。

实现方：backend/infrastructure/health/service.py（委托 _evaluate_alerts / _enrich_disk_channels 壳）。
"""
from abc import ABC, abstractmethod


class IHealthService(ABC):
    """健康域应用服务：告警评估与磁盘通道标注（IO 取数在实现方完成）。"""

    @abstractmethod
    def evaluate_alerts(self, system: dict, disks: list, docker: dict | None = None) -> list:
        """扫描当前状态返回活动告警（旧 dict 形态，level/title/detail）。"""

    @abstractmethod
    def enrich_disk_channels(self, disks: list, raid: dict) -> list:
        """给每块盘标注通道来源（阵列卡 / 主板），供前端「物理硬盘信息」区分。"""
