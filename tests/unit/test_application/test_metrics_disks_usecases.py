"""实时指标 / 历史趋势 / 硬盘面板 / 自检任务用例单测：mock 领域接口，验证编排与校验链（无真实硬件）。"""
import pytest

from backend.application.usecases.disk_selftest_usecase import DiskSelftestUseCase
from backend.application.usecases.disks_usecase import DisksUseCase
from backend.application.usecases.history_query_usecase import HistoryQueryUseCase
from backend.application.usecases.metrics_usecase import MetricsUseCase
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.disk_names import IDiskNamesRepo
from backend.domain.interfaces.disk_test import IDiskTestService
from backend.domain.interfaces.health_service import IHealthService
from backend.domain.interfaces.history_repo import IHistoryRepo


class FakeCollector(IHardwareCollector):
    def __init__(self, **kw):
        self._kw = kw

    def _raise(self, key):
        if key in self._kw:
            raise self._kw[key]

    def get_realtime_metrics(self):
        self._raise("rt_raise")
        return self._kw.get("rt", {"net": [{"name": "eno1", "rx_rate": 1.0, "tx_rate": 2.0},
                                           {"name": "eno1-ovs", "rx_rate": 1.0, "tx_rate": 2.0}],
                                   "disk": [{"device": "sda"}]})

    def get_disks_meta_map(self):
        self._raise("meta_raise")
        return self._kw.get("meta", {"sda": {"model": "WD Red", "size": "4TB",
                                             "brand": "西数", "type": "HDD"}})

    def get_cpu_usage(self):
        self._raise("cpu_raise")
        return self._kw.get("cpu", 12.3)

    def get_live_mem_percent(self):
        self._raise("mem_raise")
        return self._kw.get("mem", 41.2)

    def get_load_avg(self):
        self._raise("load_raise")
        return self._kw.get("load", [0.1, 0.2, 0.3])

    def get_cpu_freq_mhz(self):
        self._raise("freq_raise")
        return self._kw.get("freq", 2400.0)

    def get_gpu_live(self):
        self._raise("gpu_raise")
        return self._kw.get("gpu", [{"name": "UHD"}])

    def get_disks(self, force=False):
        self._raise("disks_raise")
        return self._kw.get("disks", [{"dev": "sda"}, {"dev": "sdb"}])

    def get_raid_card(self):
        self._raise("raid_raise")
        return self._kw.get("raid", {"mode": "mega"})

    def is_standalone_disk(self, dev):
        return self._kw.get("standalone", {}).get(dev, (True, ""))

    def get_temp_snapshot(self): return {}
    def disk_friendly_info(self, dev): return {"name": dev, "model": "", "category": "", "is_system": False}
    def get_disk_temps_cached(self, devs): return {}
    def list_all_disk_devs(self): return []
    def invalidate_system_cache(self): pass

    def get_system(self, force=False): return {}
    def get_docker(self): return {}
    def get_board(self): return {}
    def get_memory_modules(self): return []
    def get_storage(self): return {}
    def get_volume_map(self): return {}
    def get_listening_ports(self): return []
    def get_network_nics(self): return []
    def get_rapl_power(self): return {}


class FakeHealth(IHealthService):
    def __init__(self, fail_enrich=False):
        self.enriched_with = None
        self.fail_enrich = fail_enrich

    def evaluate_alerts(self, system, disks, docker=None): return []
    def enrich_disk_channels(self, disks, raid):
        if self.fail_enrich:
            raise RuntimeError("enrich")
        self.enriched_with = (disks, raid)
        return [dict(d, channel="raid") for d in disks]
    def dispatch_notifications(self, text, cfg, level="danger"): return {}
    def build_health_report(self): return {}
    def redact_report(self, rep): return rep
    def build_diagnostics(self): return {}
    def render_diagnostics_text(self, diag): return ""
    def render_report_html(self, rep): return None
    def clear_error_ring(self): pass


class FakeHistoryRepo(IHistoryRepo):
    def __init__(self, rows=None, fail=False):
        self.rows = rows if rows is not None else [
            (1700000000000, 10.24, 5.5, None, 1.0, 30, 45.0, 40.0, None, 38.0, 55.0, 987.6, 41.2)]
        self.fail = fail
        self.calls = []

    def fan_points(self, start): return []
    def metric_points(self, start, bucket):
        self.calls.append((start, bucket))
        if self.fail:
            raise RuntimeError("db gone")
        return self.rows

    def init_db(self): pass
    def write_sample(self): pass
    def rename_fan(self, idx, new_name, old_name=None): pass


class FakeNamesRepo(IDiskNamesRepo):
    def __init__(self, cfg=None, save_ok=True):
        self.cfg = cfg if cfg is not None else {}
        self.save_ok = save_ok
        self.saved = None

    def load(self): return self.cfg
    def save(self, cfg):
        self.saved = cfg
        return self.save_ok


class FakeTestService(IDiskTestService):
    def __init__(self, **kw):
        self._kw = kw
        self.started = []
        self.aborted = []

    def jobs_snapshot(self): return self._kw.get("jobs", {})
    def get_job(self, dev): return self._kw.get("job")
    def is_running(self, dev): return self._kw.get("running", False)
    def history(self):
        if "hist_raise" in self._kw:
            raise self._kw["hist_raise"]
        return self._kw.get("hist", [{"dev": "sda", "r": 1}, {"dev": "sdb", "r": 2}])
    def start_long(self, dev):
        if "start_raise" in self._kw:
            raise self._kw["start_raise"]
        self.started.append((dev, "long"))
    def start_badblocks(self, dev, mode="destructive"):
        if "start_raise" in self._kw:
            raise self._kw["start_raise"]
        self.started.append((dev, mode))
    def abort_long(self, dev):
        if "abort_raise" in self._kw:
            raise self._kw["abort_raise"]
        self.aborted.append((dev, "long"))
    def abort_badblocks(self, dev):
        if "abort_raise" in self._kw:
            raise self._kw["abort_raise"]
        self.aborted.append((dev, "badblocks"))


# ---------- /api/metrics ----------

def test_metrics_panel_shape_and_ovs_merge():
    r = MetricsUseCase(FakeCollector()).panel()
    assert list(r.keys()) == ["net", "diskio", "cpu_usage", "mem_percent", "load",
                              "cpu_freq_mhz", "gpu", "time"]
    # eno1-ovs 归并到 eno1，且先出现的物理口不被重复条目覆盖
    assert r["net"] == [{"name": "eno1", "rx_rate": 1.0, "tx_rate": 2.0}]


def test_metrics_diskio_enriched_from_meta_map():
    r = MetricsUseCase(FakeCollector()).panel()
    assert r["diskio"] == [{"device": "sda", "model": "WD Red", "size": "4TB",
                            "brand": "西数", "type": "HDD"}]


def test_metrics_partial_failures_degrade_to_none():
    r = MetricsUseCase(FakeCollector(cpu_raise=RuntimeError("c"), mem_raise=RuntimeError("m"),
                                     load_raise=RuntimeError("l"), freq_raise=RuntimeError("f"),
                                     meta_raise=RuntimeError("mm"))).panel()
    assert r["cpu_usage"] is None and r["mem_percent"] is None
    assert r["load"] is None and r["cpu_freq_mhz"] is None
    assert r["diskio"][0]["model"] == ""  # meta map 失败 → {} 兜底


def test_metrics_top_failure_error_shape():
    r = MetricsUseCase(FakeCollector(rt_raise=RuntimeError("x"))).panel()
    assert set(r.keys()) == {"error", "net", "diskio"}
    assert r["error"] == "x" and r["net"] == [] and r["diskio"] == []


# ---------- /api/history ----------

def test_history_range_and_bucket_mapping():
    repo = FakeHistoryRepo(rows=[])
    uc = HistoryQueryUseCase(repo)
    assert uc.points("24h")["bucket_s"] == 86400 // 240
    assert uc.points("7d")["bucket_s"] == 7 * 86400 // 240
    assert uc.points("未知档")["bucket_s"] == 86400 // 240  # 未知 range 回落 24h
    assert repo.calls[0][1] == 360 and repo.calls[1][1] == 2520
    # start 应为 now - secs（允许 1s 漂移）
    import time as _t
    now = int(_t.time())
    start24 = repo.calls[0][0]
    start7 = repo.calls[1][0]
    assert abs((now - start24) - 86400) <= 2
    assert abs((now - start7) - 7 * 86400) <= 2


def test_history_point_rendering():
    rows = [(1700000000000, 10.24, 5.55, None, 1.0, 30, 45.06, 40.0, None, 38.0, 55.0, 987.6, 41.26)]
    r = HistoryQueryUseCase(FakeHistoryRepo(rows=rows)).points("24h")
    assert r["range"] == "24h" and r["bucket_s"] == 360
    p = r["points"][0]
    assert list(p.keys()) == ["ts", "disk_read", "disk_write", "net_rx", "net_tx", "cpu_power",
                              "cpu_temp", "mb_temp", "gpu_temp", "disk_temp_max", "raid_temp",
                              "fan_rpm_avg", "mem_used_pct"]
    assert p["ts"] == 1700000000000 and p["disk_read"] == 10.2 and p["disk_write"] == 5.5
    assert p["net_rx"] is None and p["cpu_temp"] == 45.1  # None 保留 / 1 位小数
    assert p["fan_rpm_avg"] == 988  # digits=0 取整


def test_history_repo_failure_error_shape():
    r = HistoryQueryUseCase(FakeHistoryRepo(fail=True)).points()
    assert set(r.keys()) == {"error", "points"} and r["points"] == []


# ---------- /api/disks 面板与改名 ----------

def test_disks_panel_standalone_and_channel_enrich():
    health = FakeHealth()
    uc = DisksUseCase(FakeCollector(standalone={"sda": (True, ""), "sdb": (False, "已挂载")}),
                      health, FakeNamesRepo())
    r = uc.panel()
    assert list(r.keys()) == ["disks", "time", "elapsed"]
    assert r["disks"][0]["standalone"] is True and r["disks"][0]["channel"] == "raid"
    assert r["disks"][1]["standalone"] is False and r["disks"][1]["standalone_reason"] == "已挂载"
    disks_arg, raid_arg = health.enriched_with
    assert raid_arg == {"mode": "mega"} and len(disks_arg) == 2


def test_disks_panel_failures():
    r = DisksUseCase(FakeCollector(disks_raise=RuntimeError("scan")), FakeHealth(),
                     FakeNamesRepo()).panel()
    assert set(r.keys()) == {"error", "time"} and r["error"] == "scan"
    # raid 卡异常 → {} 降级，enrich 仍执行
    r2 = DisksUseCase(FakeCollector(raid_raise=RuntimeError("raid")), FakeHealth(),
                      FakeNamesRepo()).panel()
    assert r2["disks"][0]["channel"] == "raid"


def test_disks_rename_set_and_clear():
    repo = FakeNamesRepo(cfg={"旧key": "旧名"})
    uc = DisksUseCase(FakeCollector(), FakeHealth(), repo)
    s, b = uc.rename({"key": " SN123 ", "name": " 数据盘1 "})
    assert s == 200 and b == {"ok": True, "key": "SN123", "name": "数据盘1"}
    assert repo.cfg["SN123"] == "数据盘1"
    s2, _ = uc.rename({"key": "SN123", "name": ""})
    assert s2 == 200 and "SN123" not in repo.cfg and repo.cfg["旧key"] == "旧名"


def test_disks_rename_validation_and_save_failure():
    repo = FakeNamesRepo()
    uc = DisksUseCase(FakeCollector(), FakeHealth(), repo)
    assert uc.rename({"key": "", "name": "x"}) == (400, {"ok": False, "error": "缺少盘标识"})
    assert uc.rename({"key": "k" * 129, "name": "x"})[0] == 400
    bad = FakeNamesRepo(save_ok=False)
    s, b = DisksUseCase(FakeCollector(), FakeHealth(), bad).rename({"key": "k1", "name": "n"})
    assert s == 500 and b == {"ok": False, "error": "保存失败"}


# ---------- /api/disks/selftest* ----------

def _selftest(**kw):
    return DiskSelftestUseCase(FakeCollector(**kw), FakeTestService(**kw))


def test_selftest_status_and_history():
    svc = FakeTestService(jobs={"sda": {"state": "running", "type": "long"}})
    assert DiskSelftestUseCase(FakeCollector(), svc).status() == \
        {"ok": True, "jobs": {"sda": {"state": "running", "type": "long"}}}
    r = _selftest().history("sdb")
    assert r["ok"] is True and r["history"] == [{"dev": "sdb", "r": 2}]  # 新→旧 + 过滤


def test_selftest_start_validation_matrix():
    uc = _selftest()
    assert uc.start({"dev": "sda;reboot", "type": "long"}) == (400, {"ok": False, "error": "非法设备名"})
    assert uc.start({"dev": "sda", "type": "write"}) == \
        (400, {"ok": False, "error": "type 须为 long / badblocks / surface"})
    assert uc.start({"dev": "sdk", "type": "long"}) == (400, {"ok": False, "error": "未找到该硬盘"})
    uc2 = _selftest(running=True)
    assert uc2.start({"dev": "sda", "type": "long"}) == \
        (400, {"ok": False, "error": "该硬盘已有正在进行的自检"})
    uc3 = _selftest(standalone={"sdb": (False, "已挂载，无法破坏性测试")})
    assert uc3.start({"dev": "sdb", "type": "badblocks", "confirm": True}) == \
        (400, {"ok": False, "error": "已挂载，无法破坏性测试"})
    assert uc3.start({"dev": "sdb", "type": "badblocks"})[1]["error"].startswith("B 类")
    assert uc3.start({"dev": "sdb", "type": "surface"})[1]["error"].startswith("C 类")


def test_selftest_start_dispatch_by_type():
    svc = FakeTestService()
    uc = DiskSelftestUseCase(FakeCollector(), svc)
    assert uc.start({"dev": "sda", "type": "long"}) == (200, {"ok": True})
    assert uc.start({"dev": "sda", "type": "surface", "confirm": True}) == (200, {"ok": True})
    assert uc.start({"dev": "sda", "type": "badblocks", "confirm": True}) == (200, {"ok": True})
    assert svc.started == [("sda", "long"), ("sda", "surface"), ("sda", "destructive")]


def test_selftest_start_and_abort_failures():
    s, b = _selftest(start_raise=RuntimeError("smartctl 失败")).start({"dev": "sda", "type": "long"})
    assert s == 500 and b == {"ok": False, "error": "smartctl 失败"}
    assert _selftest().abort({"dev": "sda"}) == (400, {"ok": False, "error": "无进行中的自检"})
    assert _selftest(job={"state": "done", "type": "long"}).abort({"dev": "sda"}) == \
        (400, {"ok": False, "error": "该硬盘自检已结束"})
    svc = FakeTestService(job={"state": "running", "type": "long"})
    s, b = DiskSelftestUseCase(FakeCollector(), svc).abort({"dev": "sda"})
    assert s == 200 and b == {"ok": True} and svc.aborted == [("sda", "long")]
    svc2 = FakeTestService(job={"state": "running", "type": "badblocks"}, abort_raise=RuntimeError("kill"))
    s, b = DiskSelftestUseCase(FakeCollector(), svc2).abort({"dev": "sda"})
    assert s == 500 and b["error"] == "kill"
