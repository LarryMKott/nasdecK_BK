"""告警配置存储抽象契约。

实现方：backend/infrastructure/health/alert_repo_impl.py
（包装现 _load_alerts / _save_alerts 的 JSON 文件读写，文件位置与格式不变）。
"""
from abc import ABC, abstractmethod


class IAlertRepo(ABC):
    """告警阈值配置（alert.json）的加载与保存。"""

    @abstractmethod
    def load_alerts(self) -> dict:
        """读告警配置；无文件/损坏时返回含默认值的 dict（与现 _load_alerts 行为一致）。"""

    @abstractmethod
    def save_alerts(self, cfg: dict) -> None:
        """保存告警配置（原子写，等价现 _save_alerts）。"""
