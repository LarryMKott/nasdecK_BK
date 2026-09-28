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
    def write_sample(self) -> None:
        """采集当前快照并写入一个采样点（等价现 _write_history_sample，无参自取）。"""

    @abstractmethod
    def rename_fan(self, idx: int, new_name: str, old_name: str | None = None) -> None:
        """风扇改名同步：该扇全部历史记录零残留改名（等价现 _rename_history_fan）。"""

    @abstractmethod
    def metric_points(self, start: int, bucket: int) -> list:
        """按时间桶聚合的指标降采样行（/api/history 查询核）。

        参数：start = 起始 unix 秒；bucket = 桶宽（秒）。
        返回行元组列表 (bts, disk_read, disk_write, net_rx, net_tx, cpu_power,
        cpu_temp, mb_temp, gpu_temp, disk_temp_max, raid_temp, fan_rpm_avg, mem_used_pct)，
        bts 为毫秒时间戳，聚合列为均值（NULL 保留），bts 升序——与现 /api/history 的 SQL 逐字等价。
        """
