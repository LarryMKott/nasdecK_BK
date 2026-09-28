"""健康告警领域实体。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class AlertItem:
    """一条活动告警（_evaluate_alerts 的产出单元）。

    to_dict() 键序与既有 API 一致：level / title / detail。
    level 取值域：danger（危险，红）/ warn（关注，黄）。
    """
    level: str
    title: str
    detail: str

    def to_dict(self) -> dict:
        return {"level": self.level, "title": self.title, "detail": self.detail}
