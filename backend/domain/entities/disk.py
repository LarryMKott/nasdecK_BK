"""磁盘健康分级实体（_grade_disk_health 的领域形态）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DiskHealthGrade:
    """单块盘的健康分级结果。

    level: red / yellow / green / unknown（与既有 API 完全一致的取值域）
    reasons: 触发判级的理由列表（红在前黄在后，与旧实现顺序一致）
    counters: 供前端弹窗完整展示的计数器快照（含 0 值项）
    source: 分级依据来源标注（如「阵列卡 + 系统 SMART」）
    """
    level: str
    label: str
    reasons: tuple
    counters: dict
    source: str

    def to_dict(self) -> dict:
        # 键序与旧 dict 构造逐字节一致：level / label / reasons / counters / source
        return {"level": self.level, "label": self.label, "reasons": list(self.reasons),
                "counters": dict(self.counters), "source": self.source}
