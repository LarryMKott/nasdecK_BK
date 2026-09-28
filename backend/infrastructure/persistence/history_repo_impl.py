"""历史时序仓储适配器：实现 domain.interfaces.history_repo.IHistoryRepo。

包装现 persistence/db.py（SQLAlchemy 2.x Core，表结构与 history.db 完全一致）。
"""
from backend.domain.interfaces.history_repo import IHistoryRepo
from backend.infrastructure.persistence import db


class HistoryRepoImpl(IHistoryRepo):
    def init_db(self) -> None:
        db._init_history_db()

    def write_sample(self) -> None:
        db._write_history_sample()

    def rename_fan(self, idx: int, new_name: str, old_name: str | None = None) -> None:
        db._rename_history_fan(idx, new_name, old_name)
