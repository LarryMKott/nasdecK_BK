"""运维自定义盘名仓储适配器：实现 domain.interfaces.disk_names.IDiskNamesRepo。

包装 diskhealth 的 _disk_names_load / _disk_names_path（disk_names.json，位置与格式不变）。
"""
from backend.domain.interfaces.disk_names import IDiskNamesRepo
from backend.infrastructure.health.diskhealth import _disk_names_load, _disk_names_path
from backend.infrastructure.jsonstore import _save_json_file


class DiskNamesRepoImpl(IDiskNamesRepo):
    def load(self) -> dict:
        return _disk_names_load()

    def save(self, cfg: dict) -> bool:
        return bool(_save_json_file(_disk_names_path(), cfg))
