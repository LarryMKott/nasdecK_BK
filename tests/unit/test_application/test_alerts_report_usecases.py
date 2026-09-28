"""告警面板 / 健康报告用例单测：mock 领域接口，验证编排与配置合并语义（无真实硬件/网络）。"""
import pytest

from backend.application.usecases.alerts_usecase import AlertsUseCase
from backend.application.usecases.health_report_usecase import HealthReportUseCase
from backend.domain.interfaces.alert_repo import IAlertRepo
from backend.domain.interfaces.app_info import IAppInfo
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.health_service import IHealthService


class FakeCollector(IHardwareCollector):
    def __init__(self, **kw):
        self._kw = kw

    def _raise(self, key):
        if key in self._kw:
            raise self._kw[key]

    def get_system(self, force=False):
        self._raise("system_raise")
        return self._kw.get("system", {"cpu_temp": 45})

    def get_disks(self, force=False):
        self._raise("disks_raise")
        return self._kw.get("disks", [{"dev": "sda"}])

    def get_docker(self):
        self._raise("docker_raise")
        return self._kw.get("docker", {"containers": []})

    def get_board(self):
        self._raise("board_raise")
        return self._kw.get("board", {"manufacturer": "ASUS"})

    def get_memory_modules(self):
        self._raise("mem_raise")
        return self._kw.get("mem", {"modules": [1]})

    def get_raid_card(self): return {}
    def get_storage(self): return {}
    def get_volume_map(self): return {}
    def get_listening_ports(self): return []
    def get_cpu_usage(self): return None
    def get_network_nics(self): return []
    def get_rapl_power(self): return {}
    def get_gpu_live(self): return []
    def get_realtime_metrics(self): return {"net": [], "disk": []}
    def get_disks_meta_map(self): return {}
    def get_live_mem_percent(self): return None
    def get_load_avg(self): return [0.1, 0.2, 0.3]
    def get_cpu_freq_mhz(self): return None
    def is_standalone_disk(self, dev): return (True, "")


class FakeAlertRepo(IAlertRepo):
    def __init__(self, cfg=None, save_ok=True):
        self.cfg = cfg if cfg is not None else {
            "enabled": True, "disk_health": True, "memory_max": 90,
            "temp": {"enabled": True, "cpu_max": 85, "mb_max": 75, "disk_max": 60},
            "channels": {"system": True,
                         "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
                         "bark": {"enabled": False, "url": ""},
                         "email": {"enabled": False, "smtp_host": "", "smtp_port": 465,
                                   "user": "", "pass": "", "to": ""}},
            "level_channels": {"danger": ["system", "telegram", "bark", "email"],
                               "warn": ["system", "telegram"], "info": ["system"]},
        }
        self.save_ok = save_ok
        self.saved = None

    def load_alerts(self):
        return self.cfg

    def save_alerts(self, cfg):
        self.saved = cfg
        return self.save_ok


class FakeHealth(IHealthService):
    def __init__(self, alerts=None):
        self.alerts = alerts if alerts is not None else [{"level": "warn", "title": "t", "detail": "d"}]
        self.dispatch_calls = []
        self.redacted = False

    def evaluate_alerts(self, system, disks, docker=None):
        self.eval_args = (system, disks, docker)
        return [dict(a) for a in self.alerts]

    def enrich_disk_channels(self, disks, raid): return disks

    def dispatch_notifications(self, text, cfg, level="danger"):
        self.dispatch_calls.append((text, cfg, level))
        return {"system": True, "telegram": True}

    def build_health_report(self): return {"ok": 1}
    def redact_report(self, rep):
        self.redacted = True
        return {"ok": 1, "masked": True}
    def build_diagnostics(self): return {"diag": 1}
    def render_diagnostics_text(self, diag): return "== diag text =="
    def render_report_html(self, rep): return "<html>report</html>"


class FakeInfo(IAppInfo):
    def nasdeck_version(self): return "v2.4.0"
    def fnos_version(self): return "fnOS 1.0"


def _uc(**kw):
    return AlertsUseCase(kw.get("collector", FakeCollector()), kw.get("health", FakeHealth()),
                         kw.get("repo", FakeAlertRepo()), kw.get("info", FakeInfo()))


# ---------- /api/alerts 面板 ----------

def test_panel_shape_and_evaluation_inputs():
    health = FakeHealth()
    r = _uc(health=health).panel()
    assert list(r.keys()) == ["config", "alerts", "evaluated_at", "ok"]
    assert r["ok"] is True and r["alerts"] == [{"level": "warn", "title": "t", "detail": "d"}]
    system, disks, docker = health.eval_args
    assert system["board"] == {"manufacturer": "ASUS"} and system["memory_modules"] == {"modules": [1]}
    assert "cpu_temp" in system and docker == {"containers": []}


def test_get_config_passthrough():
    repo = FakeAlertRepo()
    assert _uc(repo=repo).get_config() is repo.cfg


# ---------- /api/alerts/config 写路径 ----------

def test_set_config_scalars_bool_coerced_and_saved():
    repo = FakeAlertRepo()
    s, b = _uc(repo=repo).set_config({"enabled": 1, "disk_health": 0, "memory_max": "88.5"})
    assert s == 200 and b["ok"] is True
    assert repo.cfg["enabled"] is True and repo.cfg["disk_health"] is False
    assert repo.cfg["memory_max"] == 88.5 and repo.saved is repo.cfg


def test_set_config_memory_max_invalid_400():
    repo = FakeAlertRepo()
    s, b = _uc(repo=repo).set_config({"memory_max": "abc"})
    assert s == 400 and b == {"ok": False, "error": "memory_max 需为数字"}
    assert repo.saved is None  # 校验失败不落盘


def test_set_config_temp_merge():
    repo = FakeAlertRepo()
    s, b = _uc(repo=repo).set_config({"temp": {"cpu_max": 80, "enabled": False}})
    assert s == 200
    assert repo.cfg["temp"] == {"enabled": False, "cpu_max": 80.0, "mb_max": 75, "disk_max": 60}
    s2, b2 = _uc(repo=repo).set_config({"temp": {"mb_max": "x"}})
    assert s2 == 400 and b2["error"] == "mb_max 需为数字"


def test_set_config_channels_merge_field_whitelist():
    repo = FakeAlertRepo()
    s, _ = _uc(repo=repo).set_config({
        "channels": {"telegram": {"enabled": True, "bot_token": "tk", "evil": 1},
                     "bark": {"url": "https://b"}}})
    assert s == 200
    tg = repo.cfg["channels"]["telegram"]
    assert tg["enabled"] is True and tg["bot_token"] == "tk" and "evil" not in tg
    assert repo.cfg["channels"]["bark"]["url"] == "https://b"


def test_set_config_level_channels_filtered():
    repo = FakeAlertRepo()
    _uc(repo=repo).set_config({"level_channels": {"danger": ["system", "sms"], "warn": "not-a-list"}})
    assert repo.cfg["level_channels"]["danger"] == ["system"]  # sms 非法渠道被滤
    assert repo.cfg["level_channels"]["warn"] == ["system", "telegram"]  # 非列表不动


def test_set_config_save_failure_500():
    repo = FakeAlertRepo(save_ok=False)
    s, b = _uc(repo=repo).set_config({"enabled": False})
    assert s == 500 and b == {"ok": False, "error": "写配置失败"}


# ---------- /api/alerts/test ----------

def test_test_notify_uses_version_and_danger_level():
    repo, health = FakeAlertRepo(), FakeHealth()
    r = _uc(repo=repo, health=health).test_notify()
    assert r == {"ok": True, "results": {"system": True, "telegram": True}}
    text, cfg, level = health.dispatch_calls[0]
    assert "v2.4.0" in text and cfg is repo.cfg and level == "danger"


# ---------- 健康报告 / 诊断包 ----------

def test_report_redact_flag():
    uc = HealthReportUseCase(FakeHealth())
    h_on, h_off = FakeHealth(), FakeHealth()
    r1 = HealthReportUseCase(h_on).report(redact=True)
    assert h_on.redacted is True and r1["masked"] is True
    HealthReportUseCase(h_off).report()
    assert h_off.redacted is False


def test_report_html_passthrough():
    uc = HealthReportUseCase(FakeHealth())
    assert uc.report_html(redact=False) == "<html>report</html>"


def test_diagnostics_text_roundtrip():
    uc = HealthReportUseCase(FakeHealth())
    diag = uc.diagnostics()
    assert diag == {"diag": 1}
    assert uc.diagnostics_text(diag) == "== diag text =="
