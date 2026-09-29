"""DashboardUseCase 单测：mock 领域接口，验证编排逻辑（无真实硬件）。

断言聚焦编排语义（拼装/容错/合并/告警复用），出参形状与 /api/all 契约一致。
"""
import pytest

from backend.application.usecases.dashboard_usecase import DashboardUseCase
from backend.domain.interfaces.app_info import IAppInfo
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.health_service import IHealthService


class FakeCollector(IHardwareCollector):
    def __init__(self, **overrides):
        self.calls = []
        self.system = {"cpu_temp": 45, "gpus": [{"name": "UHD 610", "pci": "0000:00:02.0"}]}
        self.disks = [{"dev": "sda", "model": "WD Red", "size": "4TB", "brand": "西数",
                       "type": "HDD", "serial": "S1"}]
        self.raid = {"mode": "mega", "roc_temp": 62}
        self.gpu_live = [{"pci": "0000:00:02.0", "mem_used": 512, "mem_total": 4096,
                          "mem_pct": 12.5, "mem_basis": "GEM"}]
        self.rt = {"net": [{"iface": "eth0"}], "disk": [{"device": "sda"}]}
        self.fail = set()
        for k, v in overrides.items():
            setattr(self, k, v)

    def _call(self, name, fn, *a, **kw):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(name)
        return fn(*a, **kw)

    def get_system(self, force=False):
        self.calls.append("get_system")
        if "get_system" in self.fail:
            raise RuntimeError("get_system")
        return dict(self.system)

    def get_disks(self, force=False):
        self.calls.append("get_disks")
        if "get_disks" in self.fail:
            raise RuntimeError("get_disks")
        return [dict(d) for d in self.disks]
    def get_raid_card(self): return self._call("get_raid_card", lambda: dict(self.raid))
    def get_storage(self): return self._call("get_storage", lambda: {"volumes": []})
    def get_volume_map(self): return {}
    def get_docker(self): return self._call("get_docker", lambda: {"containers": []})
    def get_listening_ports(self): return []
    def get_cpu_usage(self): return self._call("get_cpu_usage", lambda: 12.3)
    def get_board(self): return self._call("get_board", lambda: {"manufacturer": "ASUS"})
    def get_memory_modules(self): return self._call("get_memory_modules", lambda: {"modules": [1]})
    def get_network_nics(self): return []
    def get_rapl_power(self): return {}
    def get_gpu_live(self): return self._call("get_gpu_live", lambda: [dict(g) for g in self.gpu_live])
    def get_realtime_metrics(self): return self._call("get_realtime_metrics", lambda: dict(self.rt))
    # ---- 阶段 3 批 A 第三单接口扩展（仪表盘编排不触达，补齐抽象方法即可）----
    def get_disks_meta_map(self): return {}
    def get_live_mem_percent(self): return None
    def get_load_avg(self): return [0.1, 0.2, 0.3]
    def get_cpu_freq_mhz(self): return None
    def is_standalone_disk(self, dev): return (True, "")
    def get_temp_snapshot(self): return {}
    def disk_friendly_info(self, dev): return {"name": dev, "model": "", "category": "", "is_system": False}
    def get_disk_temps_cached(self, devs): return {}
    def list_all_disk_devs(self): return []
    def invalidate_system_cache(self): pass


class FakeHealth(IHealthService):
    def __init__(self, alerts=None, fail_enrich=False):
        self.alerts = alerts if alerts is not None else [{"level": "warn", "title": "t", "detail": "d"}]
        self.fail_enrich = fail_enrich

    def evaluate_alerts(self, system, disks, docker=None):
        assert "board" in system  # /api/all 传入的是拼装后的 system
        return [dict(a) for a in self.alerts]

    def enrich_disk_channels(self, disks, raid):
        if self.fail_enrich:
            raise RuntimeError("enrich")
        return [dict(d, channel="raid") for d in disks]

    # ---- 阶段 3 批 A 第三单接口扩展（仪表盘编排不触达，补齐抽象方法即可）----
    def dispatch_notifications(self, text, cfg, level="danger"): return {"system": True}
    def build_health_report(self): return {}
    def redact_report(self, rep): return rep
    def build_diagnostics(self): return {}
    def render_diagnostics_text(self, diag): return ""
    def render_report_html(self, rep): return None
    def clear_error_ring(self): pass


class FakeInfo(IAppInfo):
    def __init__(self, fail_fnos=False):
        self.fail_fnos = fail_fnos

    def nasdeck_version(self): return "2.4.0"

    def fnos_version(self):
        if self.fail_fnos:
            raise RuntimeError("fnos")
        return "0.9.0"

    def check_latest_version(self, force=False):
        return {"current": "v1", "latest": None, "update_available": False, "url": None, "error": None}


def _uc(**kw):
    return DashboardUseCase(kw.get("collector", FakeCollector()),
                            kw.get("health", FakeHealth()), kw.get("app_info", FakeInfo()))


def test_execute_aggregate_shape_and_merges():
    r = _uc().execute()
    assert list(r.keys())[:9] == ["raid", "disks", "system", "storage", "docker", "time",
                                  "elapsed", "nasdeck_version", "fnos_version"]
    assert r["nasdeck_version"] == "2.4.0" and r["fnos_version"] == "0.9.0"
    # board/memory/cpu_usage 并入 system；阵列卡 ROC 温度并入 system.raid_temp
    assert r["system"]["board"] == {"manufacturer": "ASUS"}
    assert r["system"]["memory_modules"] == {"modules": [1]}
    assert r["system"]["cpu_usage"] == 12.3
    assert r["system"]["raid_temp"] == 62
    # 显卡实时显存按 pci 匹配并入静态 gpus
    assert r["system"]["gpus"][0]["mem_pct"] == 12.5
    # 通道标注 + 实时指标 + diskio 友好信息按 device==dev 关联
    assert r["disks"][0]["channel"] == "raid"
    assert r["net"] == [{"iface": "eth0"}]
    assert r["diskio"][0]["model"] == "WD Red" and r["diskio"][0]["serial"] == "S1"
    # 告警复用聚合数据
    assert r["alerts"][0]["title"] == "t"


def test_execute_fault_tolerance_defaults():
    c = FakeCollector(fail={"get_board", "get_memory_modules", "get_cpu_usage"})
    r = _uc(collector=c).execute()
    assert r["system"]["board"] == {"manufacturer": "", "product": "", "version": ""}
    assert r["system"]["memory_modules"] == {"modules": [], "total_gb": 0, "slots": 0, "brand_summary": ""}
    assert r["system"]["cpu_usage"] is None


def test_execute_gpu_live_no_match_leaves_static():
    c = FakeCollector(gpu_live=[{"pci": "0000:ff", "name": "RTX"}])
    r = _uc(collector=c).execute()
    assert "mem_used" not in r["system"]["gpus"][0]


def test_execute_realtime_failure_keeps_enriched_disks():
    c = FakeCollector(fail={"get_realtime_metrics"})
    r = _uc(collector=c).execute()
    assert "net" not in r and "diskio" not in r
    assert r["disks"][0]["channel"] == "raid"  # enrich 在同一 try 内先执行，已生效


def test_execute_enrich_failure_keeps_raw_disks():
    r = _uc(health=FakeHealth(fail_enrich=True)).execute()
    assert "channel" not in r["disks"][0] and "net" not in r


def test_execute_alerts_failure_empty():
    class BoomHealth(FakeHealth):
        def evaluate_alerts(self, system, disks, docker=None): raise RuntimeError("x")
    assert _uc(health=BoomHealth()).execute()["alerts"] == []


def test_execute_top_level_failure_error_shape():
    r = _uc(app_info=FakeInfo(fail_fnos=True)).execute()
    assert set(r.keys()) == {"error", "time"} and "fnos" in r["error"]


def test_system_panel_shape_and_fallbacks():
    r = _uc().system_panel()
    assert list(r.keys()) == ["system", "fnos_version", "time", "elapsed"]
    assert r["system"]["raid_temp"] == 62 and r["system"]["cpu_usage"] == 12.3
    c = FakeCollector(fail={"get_raid_card", "get_cpu_usage"})
    r2 = _uc(collector=c).system_panel()
    assert r2["system"]["raid_temp"] is None and r2["system"]["cpu_usage"] is None
    assert "error" not in r2
