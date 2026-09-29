"""风扇标注仓储适配器：实现 domain.interfaces.fan_labels.IFanLabelsRepo。

包装 labels.py 既有函数（逻辑零改动）；VOLT 常量经 volt_allowed() 暴露。
"""
from backend.domain.interfaces.fan_labels import IFanLabelsRepo
from backend.infrastructure.fans import labels


class FanLabelsRepoImpl(IFanLabelsRepo):
    def load(self) -> dict:
        return labels._load_fan_labels()

    def save(self, d: dict) -> bool:
        return labels._save_fan_labels(d)

    def label_for(self, hwmon: str, idx: int) -> dict:
        return labels._fan_label_for(hwmon, idx)

    def canon_key(self, d: dict, idx: int):
        return labels._canon_label_key(d, idx)

    def alias_members(self, hwmon: str, idx: int) -> list:
        return labels._fan_alias_members_of(hwmon, idx)

    def replicate(self, d: dict) -> None:
        labels._replicate_aliases(d)

    def consolidate(self, d: dict) -> None:
        labels._consolidate_fan_labels(d)

    def dedup_names(self, fans: list) -> list:
        return labels._dedup_fan_names(fans)

    def volt_allowed(self) -> tuple:
        return labels._FAN_VOLT_ALLOWED

    def read_drivers(self) -> dict:
        return labels._read_fan_drivers()
