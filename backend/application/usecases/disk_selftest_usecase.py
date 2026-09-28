"""硬盘自检用例：/api/disks/selftest* 四端点的编排核（计划批 C 项，随 disks.py 收敛前置落地）。

自 presentation/api/endpoints/disks.py 原样搬迁（v2.4 阶段 3 批 A 第三单）：
校验链与错误文案逐行等价（非法设备名/type/盘存在性/任务互斥/B 档独立盘确认/C 档耗时确认）；
设备名合法性经 domain.rules.disk_rule.safe_token（纯规则），
任务簿与启停原语经 IDiskTestService（状态与线程机制留基础设施）。
start/abort 返回 (status, body)，由端点 _jr 透传。
"""
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.disk_test import IDiskTestService
from backend.domain.rules.disk_rule import safe_token


class DiskSelftestUseCase:
    def __init__(self, collector: IHardwareCollector, test_service: IDiskTestService):
        self._c = collector
        self._svc = test_service

    def status(self) -> dict:
        """返回当前硬盘自检任务状态（只读）。"""
        return {"ok": True, "jobs": self._svc.jobs_snapshot()}

    def history(self, dev: str = "") -> dict:
        """返回硬盘自检历史记录（新→旧，最多 50 条）；dev 非空时按设备过滤。"""
        hist = self._svc.history()
        hist = list(reversed(hist))
        if dev:
            hist = [r for r in hist if r.get("dev") == dev]
        return {"ok": True, "history": hist}

    def start(self, data: dict) -> tuple:
        """启动硬盘自检：type=long（SMART 长自检）/ badblocks（破坏性，仅独立盘）/ surface（只读表面扫描，所有盘）。"""
        dev = data.get("dev", "")
        ttype = data.get("type", "")
        confirm = data.get("confirm", False)
        if not isinstance(dev, str) or not safe_token(dev):
            return 400, {"ok": False, "error": "非法设备名"}
        if ttype not in ("long", "badblocks", "surface"):
            return 400, {"ok": False, "error": "type 须为 long / badblocks / surface"}
        disks = self._c.get_disks()
        if not any(d.get("dev") == dev for d in disks):
            return 400, {"ok": False, "error": "未找到该硬盘"}
        if self._svc.is_running(dev):
            return 400, {"ok": False, "error": "该硬盘已有正在进行的自检"}
        if ttype == "badblocks":
            if not confirm:
                return 400, {"ok": False, "error": "B 类坏块慢扫为破坏性测试，请确认"}
            ok, reason = self._c.is_standalone_disk(dev)
            if not ok:
                return 400, {"ok": False, "error": reason}
        if ttype == "surface":
            if not confirm:
                return 400, {"ok": False, "error": "C 类表面扫描耗时较长，请确认"}
        try:
            if ttype == "long":
                self._svc.start_long(dev)
            elif ttype == "surface":
                self._svc.start_badblocks(dev, mode="surface")
            else:
                self._svc.start_badblocks(dev, mode="destructive")
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"ok": False, "error": str(e)}

    def abort(self, data: dict) -> tuple:
        """中止指定硬盘的自检任务。"""
        dev = data.get("dev", "")
        if not isinstance(dev, str) or not safe_token(dev):
            return 400, {"ok": False, "error": "非法设备名"}
        job = self._svc.get_job(dev)
        if not job:
            return 400, {"ok": False, "error": "无进行中的自检"}
        if job["state"] != "running":
            return 400, {"ok": False, "error": "该硬盘自检已结束"}
        try:
            if job["type"] == "long":
                self._svc.abort_long(dev)
            else:
                self._svc.abort_badblocks(dev)
            return 200, {"ok": True}
        except Exception as e:
            return 500, {"ok": False, "error": str(e)}
