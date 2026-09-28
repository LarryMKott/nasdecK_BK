"""硬盘自检任务服务抽象契约（阶段 3 批 A 第三单）。

实现方：backend/infrastructure/collectors/disktest_service.py
（包装 collectors/disks.py 的 DISK_TEST_JOBS / 启停函数，任务状态与线程机制原样）。
"""
from abc import ABC, abstractmethod


class IDiskTestService(ABC):
    """硬盘自检（SMART 长自检 / badblocks 破坏性与只读表面扫描）任务生命周期。"""

    @abstractmethod
    def jobs_snapshot(self) -> dict:
        """全部任务状态快照（dev → 任务 dict，锁内取并滤除 pid/_proc/outfile 内部键）。"""

    @abstractmethod
    def get_job(self, dev: str) -> dict | None:
        """单盘任务状态（锁内取，滤除内部键；无任务返回 None）。"""

    @abstractmethod
    def is_running(self, dev: str) -> bool:
        """该盘是否已有正在进行的自检（锁内判定）。"""

    @abstractmethod
    def history(self) -> list:
        """自检历史记录（旧→新原序，最多 50 条；排序/过滤由用例编排）。"""

    @abstractmethod
    def start_long(self, dev: str) -> None:
        """启动 SMART 长自检（smartctl -t long）；失败抛异常。"""

    @abstractmethod
    def start_badblocks(self, dev: str, mode: str = "destructive") -> None:
        """启动 badblocks 扫描：mode=destructive 破坏性读写 / surface 只读表面扫描；失败抛异常。"""

    @abstractmethod
    def abort_long(self, dev: str) -> None:
        """中止 SMART 长自检（smartctl -X）；失败抛异常。"""

    @abstractmethod
    def abort_badblocks(self, dev: str) -> None:
        """中止 badblocks 扫描（SIGTERM→SIGKILL）；失败抛异常。"""
