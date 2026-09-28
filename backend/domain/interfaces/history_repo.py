"""历史时序库抽象契约。

实现方：backend/infrastructure/persistence/history_repo_impl.py
（包装现 history/db.py 的 SQLAlchemy 2.x Core 读写，表结构与 history.db 完全一致）。
查询侧契约（范围/降采样/指标元数据）在阶段 3 history_query_usecase 落地时冻结。
"""
from abc import ABC, abstractmethod


class IHistoryRepo(ABC):
    """SQLite 历史时序仓储（30 天自动降采样，重启不丢）。"""

    @abstractmethod
    def init_db(self) -> None:
        """建表/迁移（等价现 _init_history_db；幂等，启动时调用一次）。"""

    @abstractmethod
    def write_sample(self, payload: dict) -> None:
        """写入一个采样点（等价现 _write_history_sample 的入参载荷）。"""

    @abstractmethod
    def rename_fan(self, idx: int, new_name: str, old_name: str | None = None) -> None:
        """风扇改名同步：该扇全部历史记录零残留改名（等价现 _rename_history_fan）。"""
