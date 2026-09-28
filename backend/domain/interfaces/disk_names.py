"""运维自定义盘名存储抽象契约（阶段 3 批 A 第三单）。

实现方：backend/infrastructure/health/disknames_repo_impl.py
（包装 diskhealth 的 _disk_names_load / _save_json_file(_disk_names_path())，
文件位置 disk_names.json 与格式不变）。
"""
from abc import ABC, abstractmethod


class IDiskNamesRepo(ABC):
    """自定义盘名（key = name_key，序列号优先）的加载与保存。"""

    @abstractmethod
    def load(self) -> dict:
        """读盘名映射；无文件/损坏时返回 {}（与现 _disk_names_load 行为一致）。"""

    @abstractmethod
    def save(self, cfg: dict) -> bool:
        """保存盘名映射（原子写）；返回是否写入成功（端点据其映射 200/500）。"""
