"""硬盘自检任务服务适配器：实现 domain.interfaces.disk_test.IDiskTestService。

包装 collectors/disks.py 的既有任务簿（DISK_TEST_JOBS / DISK_TEST_LOCK）与启停函数，
任务状态、锁语义与线程机制原样；本类只做取数原语，校验与错误码编排在应用层用例。
"""
from backend.domain.interfaces.disk_test import IDiskTestService
from backend.infrastructure.collectors.disks import (DISK_TEST_JOBS, DISK_TEST_LOCK, _abort_badblocks,
                                     _abort_smart_long, _load_disk_test_history, _start_badblocks,
                                     _start_smart_long)

# 任务对外暴露字段：滤除运行期内部键（与原端点过滤口径一致）
_JOB_INTERNAL_KEYS = ("pid", "_proc", "outfile")


def _job_public(v: dict) -> dict:
    return {kk: vv for kk, vv in v.items() if kk not in _JOB_INTERNAL_KEYS}


class DiskTestService(IDiskTestService):
    """自检任务原语（读快照 / 启动 / 中止）。"""

    def jobs_snapshot(self) -> dict:
        with DISK_TEST_LOCK:
            return {k: _job_public(v) for k, v in DISK_TEST_JOBS.items()}

    def get_job(self, dev: str) -> dict | None:
        with DISK_TEST_LOCK:
            job = DISK_TEST_JOBS.get(dev)
        return _job_public(job) if job else None

    def is_running(self, dev: str) -> bool:
        with DISK_TEST_LOCK:
            return dev in DISK_TEST_JOBS and DISK_TEST_JOBS[dev]["state"] == "running"

    def history(self) -> list:
        return _load_disk_test_history()

    def start_long(self, dev: str) -> None:
        _start_smart_long(dev)

    def start_badblocks(self, dev: str, mode: str = "destructive") -> None:
        _start_badblocks(dev, mode=mode)

    def abort_long(self, dev: str) -> None:
        _abort_smart_long(dev)

    def abort_badblocks(self, dev: str) -> None:
        _abort_badblocks(dev)
