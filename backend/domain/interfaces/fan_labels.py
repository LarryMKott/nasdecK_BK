"""风扇标注仓储抽象契约（阶段 3 批 B 新增）。

实现方：backend/infrastructure/fans/labels_repo_impl.py（包装 labels.py 存量函数）。
key 格式 "hwmon::idx" -> {"name","voltage","hidden","no_tach"}；
按 idx 归并（canon key）抗 hwmon 跨重启漂移。
"""
from abc import ABC, abstractmethod


class IFanLabelsRepo(ABC):
    """用户风扇标注（名称/电压/隐藏/无转速线）持久化与派生 helpers。"""

    @abstractmethod
    def load(self) -> dict:
        """读全部标注（缺失/损坏返回空 dict）。"""

    @abstractmethod
    def save(self, labels: dict) -> bool:
        """整体写回标注。"""

    @abstractmethod
    def label_for(self, hwmon: str, idx: int) -> dict:
        """取单扇标注：精确 key 缺失时按 idx 跨 hwmon 兜底（抗漂移）。"""

    @abstractmethod
    def canon_key(self, labels: dict, idx: int) -> str | None:
        """该 idx 的既有标注 key（跨 hwmon 归并锚点；无则 None）。"""

    @abstractmethod
    def alias_members(self, hwmon: str, idx: int) -> list:
        """同名别名组通道列表（含自身）：控速同步到全部成员。"""

    @abstractmethod
    def replicate(self, labels: dict) -> None:
        """别名同步：同名标注通道改名/隐藏联动（就地改写传入 dict）。"""

    @abstractmethod
    def consolidate(self, labels: dict) -> None:
        """归并重复 idx key（就地改写传入 dict）。"""

    @abstractmethod
    def dedup_names(self, fans: list) -> list:
        """同名单扇去重：重名者补内核名后缀（就地改写传入 list）。"""

    @abstractmethod
    def volt_allowed(self) -> tuple:
        """合法电压档位常量。"""

    @abstractmethod
    def read_drivers(self) -> dict:
        """主板风扇芯片驱动清单。"""
