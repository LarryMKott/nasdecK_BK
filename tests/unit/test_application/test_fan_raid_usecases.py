"""风扇控制 / 阵列卡用例单测：mock 领域接口，验证编排与校验链（无真实硬件）。

覆盖批 B 收敛的关键编排：fan set 校验与别名组同步、hwmon 漂移校正、
接管开关、temps 组装与重名消歧、rules/labels 合并语义、history 解析容错、
raid 六端点校验链与 storcli 参数拼装。
"""
import time

import pytest

from backend.application.usecases.fan_control_usecase import FanControlUseCase
from backend.application.usecases.raid_usecase import RaidUseCase
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.fan_driver import IFanDriver
from backend.domain.interfaces.fan_labels import IFanLabelsRepo
from backend.domain.interfaces.fan_rules import IFanRulesRepo
from backend.domain.interfaces.history_repo import IHistoryRepo
from backend.domain.interfaces.raid_control import IRaidControl

H1 = "/sys/class/hwmon/hwmon1"


# ---------------- Fakes ----------------

class FakeDriver(IFanDriver):
    def __init__(self, **kw):
        self.enabled = kw.get("enabled", True)
        self.enum = kw.get("enum", [(H1, 1)])
        self.ext = kw.get("ext", False)
        self.takeover_ok = kw.get("takeover_ok", True)
        self.targets = {}
        self.modes = {}
        self.enables = []
        self.pwm_modes = {}
        self.snap = kw.get("snap", False)

    def read_raw(self, hwmon, idx):
        return None

    def write_raw(self, hwmon, idx, raw):
        pass

    def set_enable(self, hwmon, idx, val):
        self.enables.append((hwmon, idx, val))

    def release_auto(self, hwmon, idx):
        pass

    def read_pwm_mode(self, hwmon, idx):
        return self.pwm_modes.get((hwmon, idx))

    def set_pwm_mode(self, hwmon, idx, mode):
        self.pwm_modes[(hwmon, idx)] = mode
        return True, None

    def release_to_idle(self):
        pass

    def enumerate_fans(self):
        return list(self.enum)

    def control_enabled(self):
        return self.enabled

    def takeover(self, enabled):
        if not self.takeover_ok:
            return False
        self.enabled = enabled
        return True

    def get_status(self):
        return [{"idx": 1, "rpm": 900}]

    def read_fan_drivers(self):
        return {"it8613": 2}

    def fcs_status(self, clear=False):
        return {"running": False, "installed": True, "disabled_by_user": False}

    def fcs_board_cfg(self):
        return {"fans": []}

    def fcs_has_board_config(self):
        return False

    def fcs_disable(self):
        pass

    def fcs_enable(self):
        pass

    def ext_service_running(self):
        return self.ext

    def set_target(self, key, entry):
        self.targets[key] = entry

    def pop_target(self, key):
        self.targets.pop(key, None)

    def save_fan_mode(self, idx, mode, target):
        self.modes[idx] = (mode, target)


class FakeLabels(IFanLabelsRepo):
    def __init__(self, **kw):
        self.store = dict(kw.get("store", {}))
        self.aliases = kw.get("aliases", {})
        self.saved = None

    def load(self):
        return dict(self.store)

    def save(self, d):
        self.saved = d
        return True

    def label_for(self, hwmon, idx):
        return self.store.get("%s::%d" % (hwmon, idx), {})

    def canon_key(self, d, idx):
        for k in d:
            if k.endswith("::%d" % idx):
                return k
        return None

    def alias_members(self, hwmon, idx):
        return self.aliases.get((hwmon, idx), [(hwmon, idx)])

    def replicate(self, d):
        pass

    def consolidate(self, d):
        pass

    def dedup_names(self, fans):
        return fans

    def volt_allowed(self):
        return ("12V", "5V", "未知", "")

    def read_drivers(self):
        return {}


class FakeRules(IFanRulesRepo):
    def __init__(self, **kw):
        self.disk_cfg = kw.get("disk_cfg", {})
        self.sys_cfg = kw.get("sys_cfg", {})
        self.raw_rules = kw.get("raw_rules")
        self.effective = kw.get("effective", {})
        self.curve_err = kw.get("curve_err")
        self.saved = None

    def load_rules_raw(self):
        return self.raw_rules

    def save_rules(self, d):
        self.saved = d
        return True

    def effective_rules(self):
        return self.effective

    def rule_sources(self):
        return ("disk", "cpu", "mb", "raid")

    def validate_rule(self, r):
        if r is None:
            return None, None
        if not isinstance(r, dict):
            return None, "规则需为对象"
        return dict(r), None

    def rule_pwm(self, T, rule):
        return 64

    def load_disk_temp(self):
        return dict(self.disk_cfg)

    def save_disk_temp(self, cfg):
        self.saved = cfg
        return kw_save(self)

    def disk_source_state(self, dt_cfg):
        return (False, 38.0, True)

    def disk_temp_pwm(self, states, cfg):
        return 128

    def load_sys_temp(self):
        return dict(self.sys_cfg)

    def save_sys_temp(self, cfg):
        self.saved = cfg
        return kw_save(self)

    def read_sys_temp(self, source):
        return 45.0 if source in ("cpu", "mb") else None

    def sys_temp_pwm(self, T, cfg):
        return 100

    def apply_temp_curve(self, cfg, data, recover_max):
        if self.curve_err is not None:
            return self.curve_err
        return None


def kw_save(repo):
    return getattr(repo, "save_ok", True)


class FakeCollector(IHardwareCollector):
    def __init__(self, **kw):
        self.kw = kw
        self.cache_invalidated = 0

    def get_system(self, force=False):
        return {}

    def get_disks(self, force=False):
        return self.kw.get("disks", [])

    def get_raid_card(self):
        if "raid_raise" in self.kw:
            raise self.kw["raid_raise"]
        return self.kw.get("raid", {})

    def get_storage(self):
        return {}

    def get_volume_map(self):
        return {}

    def get_docker(self):
        return {}

    def get_listening_ports(self):
        return []

    def get_cpu_usage(self):
        return 0.0

    def get_board(self):
        return {}

    def get_memory_modules(self):
        return []

    def get_network_nics(self):
        return []

    def get_rapl_power(self):
        return {}

    def get_gpu_live(self):
        return self.kw.get("gpu", [{"name": "UHD 630", "temp": 45.5, "mem_used": 1, "mem_total": 2,
                                    "mem_pct": 50, "mem_basis": "shared"}])

    def get_realtime_metrics(self):
        return {}

    def get_disks_meta_map(self):
        return {}

    def get_live_mem_percent(self):
        return None

    def get_load_avg(self):
        return []

    def get_cpu_freq_mhz(self):
        return None

    def is_standalone_disk(self, dev):
        return (True, "")

    def get_temp_snapshot(self):
        return self.kw.get("snap", {"cpu_temp": 41.25, "mb_temp": 33.4,
                                    "disks": {"/dev/sda": {"temp": 37.6, "asleep": False},
                                              "/dev/sdb": {"temp": 37.1, "asleep": True}}})

    def disk_friendly_info(self, dev):
        return {"name": "WD 红", "model": "WD40", "category": "hdd", "is_system": dev == "/dev/sda"}

    def get_disk_temps_cached(self, devs):
        return {d: {"temp": 36.0, "asleep": False} for d in devs}

    def list_all_disk_devs(self):
        return ["/dev/sda", "/dev/sdb"]

    def invalidate_system_cache(self):
        self.cache_invalidated += 1


class FakeHistory(IHistoryRepo):
    def __init__(self, **kw):
        self.kw = kw
        self.renames = []

    def init_db(self):
        pass

    def write_sample(self):
        pass

    def rename_fan(self, idx, new_name, old_name=None):
        self.renames.append((idx, new_name, old_name))

    def metric_points(self, start, bucket):
        return []

    def fan_points(self, start):
        if "fan_raise" in self.kw:
            raise self.kw["fan_raise"]
        return self.kw.get("rows", [(1700000000, '[{"idx":1,"rpm":800}]'),
                                    (1700000030, "not-json")])


class FakeRaidCtl(IRaidControl):
    def __init__(self, out="Status = Success"):
        self.out = out
        self.calls = []

    def storcli(self, args, timeout):
        self.calls.append((args, timeout))
        return self.out

    def schedule_load(self):
        return {"enabled": False, "period": "daily", "hour": 3, "minute": 0,
                "weekday": 0, "vd": "all", "last_run": None}

    def schedule_save(self, cfg):
        self.saved = cfg
        return True


def make_fan_uc(driver=None, labels=None, rules=None, collector=None, history=None):
    return FanControlUseCase(driver or FakeDriver(),
                             collector or FakeCollector(),
                             labels or FakeLabels(),
                             rules or FakeRules(),
                             history or FakeHistory())


# ---------------- fan set / control ----------------

def test_fan_set_rejects_when_takeoff_disabled():
    uc = make_fan_uc(FakeDriver(enabled=False))
    status, body = uc.set({"hwmon": H1, "idx": 1, "mode": "manual", "pwm": 50})
    assert status == 400 and "接管已关闭" in body["error"]


@pytest.mark.parametrize("data, err", [
    ({"hwmon": "/etc/passwd", "idx": 1, "mode": "manual", "pwm": 50}, "invalid hwmon"),
    ({"hwmon": H1, "idx": 0, "mode": "manual", "pwm": 50}, "invalid idx"),
    ({"hwmon": H1, "idx": 11, "mode": "manual", "pwm": 50}, "invalid idx"),
    ({"hwmon": H1, "idx": 1, "mode": "manual", "pwm": "x"}, "invalid pwm"),
])
def test_fan_set_validation_chain(data, err):
    status, body = make_fan_uc().set(data)
    assert status == 400 and body["error"] == err


def test_fan_set_corrects_hwmon_drift():
    d = FakeDriver(enum=[("/sys/class/hwmon/hwmon5", 1)])
    status, body = make_fan_uc(d).set({"hwmon": "/sys/class/hwmon/hwmon3", "idx": 1,
                                       "mode": "manual", "pwm": 50})
    assert status == 200
    assert ("/sys/class/hwmon/hwmon5", 1) in d.targets
    assert d.targets[("/sys/class/hwmon/hwmon5", 1)]["target"] == 128  # 50% → raw 127.5 → round 128
    assert d.modes[1] == ("manual", 128)


def test_fan_set_alias_group_syncs_all_members():
    d = FakeDriver()
    lbl = FakeLabels(aliases={(H1, 1): [(H1, 1), (H1, 2)]})
    status, body = make_fan_uc(d, lbl).set({"hwmon": H1, "idx": 1, "mode": "manual", "pwm": 80})
    assert status == 200 and body["aliased"] is True
    assert (H1, 1) in d.targets and (H1, 2) in d.targets
    assert 1 in d.modes and 2 in d.modes


def test_fan_set_manual_clamps_pct():
    d = FakeDriver()
    uc = make_fan_uc(d)
    _, b1 = uc.set({"hwmon": H1, "idx": 1, "mode": "manual", "pwm": 150})
    assert b1["pwm"] == 100 and b1["raw"] == 255
    _, b2 = uc.set({"hwmon": H1, "idx": 1, "mode": "manual", "pwm": -5})
    assert b2["pwm"] == 0 and b2["raw"] == 0


def test_fan_set_auto_without_ext_service():
    d = FakeDriver(ext=False)
    status, body = make_fan_uc(d).set({"hwmon": H1, "idx": 1, "mode": "auto"})
    assert status == 200 and body == {"ok": True, "mode": "auto", "owner": "nasdeck"}
    assert d.targets[(H1, 1)] == {"mode": "auto", "target": None}
    assert d.modes[1] == ("auto", None)


def test_fan_set_auto_with_ext_service_hands_over():
    d = FakeDriver(ext=True)
    d.targets[(H1, 1)] = {"mode": "manual", "target": 100}
    status, body = make_fan_uc(d).set({"hwmon": H1, "idx": 1, "mode": "auto"})
    assert body["owner"] == "ext_service"
    assert d.enables == [(H1, 1, 2)]          # pwm_enable=2 交还系统服务
    assert (H1, 1) not in d.targets           # 目标清除


def test_control_set_toggle_and_failure():
    d = FakeDriver()
    status, body = make_fan_uc(d).control_set({"enabled": False})
    assert status == 200 and body == {"ok": True, "enabled": False}
    assert d.enabled is False
    d2 = FakeDriver(takeover_ok=False)
    status, body = make_fan_uc(d2).control_set({"enabled": True})
    assert status == 500 and body == {"ok": False, "error": "写入配置失败"}


def test_control_get_and_status_shapes():
    uc = make_fan_uc()
    assert uc.control_get() == {"enabled": True}
    st = uc.status()
    assert st["control_enabled"] is True
    assert st["fans"] == [{"idx": 1, "rpm": 900}]
    assert st["fcs"]["installed"] is True and st["fcs"]["running"] is False


# ---------------- temps ----------------

def test_temps_assembly_and_name_dedup():
    snap = {"cpu_temp": 41.25, "mb_temp": None,
            "disks": {"/dev/sda": {"temp": 37.6, "asleep": False, "is_nvme": False, "intf": "sas"},
                      "/dev/sdb": {"temp": 37.1, "asleep": True, "is_nvme": True, "intf": ""}},
            "raid_drives": [{"slot": "0"}], "temps": [], "raid_temp": 40.0}
    out = make_fan_uc(collector=FakeCollector(snap=snap)).temps()
    assert out["cpu_temp"] == 41.2 and out["mb_temp"] is None  # round(41.25,1) 银行家舍入 → 41.2
    names = [d["name"] for d in out["disks"]]
    # 同名友好名撞车 → 补内核名后缀
    assert names[0] == "WD 红·sda" and names[1] == "WD 红·sdb"
    assert out["disks"][0]["nvme_start_temp"] == 65.0
    assert out["disks"][1]["is_nvme"] is True
    assert out["disks"][0]["intf"] == "SAS"
    assert out["gpus"][0]["mem_basis"] == "shared"


def test_temps_round_is_py_round():
    out = make_fan_uc(collector=FakeCollector(snap={"cpu_temp": 41.2, "disks": {}})).temps()
    assert out["cpu_temp"] == 41.2


# ---------------- disk_temp / sys_temp ----------------

def test_disk_temp_get_falls_back_to_all_devs():
    out = make_fan_uc(rules=FakeRules(disk_cfg={})).disk_temp_get()
    assert out["disks"] and {d["dev"] for d in out["disks"]} == {"/dev/sda", "/dev/sdb"}
    assert out["computed_raw"] == 128 and out["computed_pwm"] == 50


def test_disk_temp_set_validation_and_curve_error_passthrough():
    uc = make_fan_uc(rules=FakeRules())
    assert uc.disk_temp_set({"disks": "sda"})[0] == 400           # 非数组
    assert uc.disk_temp_set({"idle_minutes": 0})[0] == 400        # 越界
    assert uc.disk_temp_set({"idle_minutes": "x"})[0] == 400      # 非数字
    # apply_temp_curve 错误 → (code, body) 正确转置（回归修复：原直接 return 元组）
    r = FakeRules(curve_err=({"ok": False, "error": "curve 需为数组"}, 400))
    status, body = make_fan_uc(rules=r).disk_temp_set({"curve": "x"})
    assert status == 400 and body == {"ok": False, "error": "curve 需为数组"}


def test_disk_temp_set_ok_and_save_fail():
    r = FakeRules()
    status, body = make_fan_uc(rules=r).disk_temp_set({"sleep_stop": True, "idle_minutes": 30})
    assert status == 200 and body["config"]["sleep_stop"] is True
    r.save_ok = False
    assert make_fan_uc(rules=r).disk_temp_set({})[0] == 500


def test_sys_temp_set_source_gate_and_curve_passthrough():
    uc = make_fan_uc(rules=FakeRules())
    assert uc.sys_temp_set({"source": "gpu"})[0] == 400
    r = FakeRules(curve_err=({"ok": False, "error": "min_pwm 需在 0~100"}, 400))
    status, body = make_fan_uc(rules=r).sys_temp_set({"min_pwm": 101})
    assert status == 400 and "min_pwm" in body["error"]


# ---------------- rules ----------------

def test_rules_set_single_key_null_placeholder():
    r = FakeRules(raw_rules={"rules": {}})
    status, body = make_fan_uc(rules=r).rules_set({"key": "%s::1" % H1, "rule": None})
    assert status == 200
    assert r.saved["rules"]["%s::1" % H1] is None   # 显式 null 占位（摘除 legacy 默认）


def test_rules_set_bulk_and_errors():
    r = FakeRules()
    uc = make_fan_uc(rules=r)
    assert uc.rules_set({"nokey": 1})[0] == 400                 # 缺 key/rules
    assert uc.rules_set({"key": "bad"})[0] == 400               # key 无 ::
    assert uc.rules_set({"rules": "x"})[0] == 400               # 非对象
    status, body = uc.rules_set({"rules": {"%s::1" % H1: {"source": "cpu"},
                                           "%s::2" % H1: None}})
    assert status == 200 and len(r.saved["rules"]) == 2
    assert r.saved["rules"]["%s::2" % H1] is None


# ---------------- labels ----------------

def test_labels_post_merge_and_invalidate():
    lbl = FakeLabels(store={"/sys/class/hwmon/hwmon9::1": {"name": "后扇", "voltage": "12V"}})
    c = FakeCollector()
    uc = make_fan_uc(labels=lbl, collector=c)
    status, body = uc.labels_post({"%s::1" % H1: {"name": "前扇", "voltage": "5V"}})
    assert status == 200 and body == {"ok": True}
    # 归并语义：同 idx 既有 key（旧 hwmon 路径）是 canon 锚点，写入落到旧 key，
    # 防止 hwmon 漂移后新建孤立 key 导致「改名像没保存」
    assert lbl.saved.get("/sys/class/hwmon/hwmon9::1") == {"name": "前扇", "voltage": "5V"}
    assert "%s::1" % H1 not in lbl.saved
    assert c.cache_invalidated == 1


def test_labels_post_canon_and_empty_removal():
    lbl = FakeLabels(store={"/sys/class/hwmon/hwmon9::2": {"name": "CPU 扇"}})
    uc = make_fan_uc(labels=lbl)
    # 取消标注须显式 voltage=null（voltage 缺省的 "" 属合法档位，会写入电压空档——原样语义）
    status, _ = uc.labels_post({"%s::2" % H1: {"name": "", "voltage": None}})
    assert status == 200
    assert "/sys/class/hwmon/hwmon9::2" not in lbl.saved
    # 非法 hwmon key 被跳过
    status, _ = uc.labels_post({"boot::3": {"name": "x"}})
    assert status == 200


def test_labels_post_rename_rewrites_history():
    lbl = FakeLabels(store={"%s::1" % H1: {"name": "旧名"}})
    h = FakeHistory()
    make_fan_uc(labels=lbl, history=h).labels_post({"%s::1" % H1: {"name": "新名"}})
    deadline = time.time() + 2
    while time.time() < deadline and not h.renames:
        time.sleep(0.02)
    assert h.renames == [(1, "新名", "旧名")]


# ---------------- history ----------------

def test_history_parses_and_tolerates_bad_json():
    status, body = make_fan_uc(history=FakeHistory()).history("7d")
    assert status == 200 and body["range"] == "7d"
    assert body["points"][0] == {"ts": 1700000000000, "fans": [{"idx": 1, "rpm": 800}]}
    assert body["points"][1]["fans"] == []            # 非 JSON 行容错为空


def test_history_error_fallback():
    status, body = make_fan_uc(history=FakeHistory(fan_raise=RuntimeError("db down"))).history("24h")
    assert status == 200 and body["points"] == [] and "db down" in body["error"]


# ---------------- raid ----------------

def test_raid_panel_error_fallback():
    uc = RaidUseCase(FakeCollector(raid_raise=RuntimeError("storcli missing")), FakeRaidCtl())
    out = uc.panel()
    assert "storcli missing" in out["error"] and "time" in out


def test_raid_locate_chain():
    ctl = FakeRaidCtl(out="CLI ... Succeeded")
    rc = {"locate_supported": True, "drives": [{"slot": "252:0"}]}
    uc = RaidUseCase(FakeCollector(raid=rc), ctl)
    assert uc.locate({"slot": "bad", "action": "start"}) == {"ok": False, "error": "invalid slot"}
    assert uc.locate({"slot": "252:0", "action": "blink"})["error"] == "invalid action"
    rc2 = {"locate_supported": False, "drives": []}
    assert RaidUseCase(FakeCollector(raid=rc2), ctl).locate(
        {"slot": "252:0", "action": "start"})["error"] == "本机阵列卡/背板不支持定位闪灯"
    rc3 = {"locate_supported": True, "drives": [{"slot": "252:1"}]}
    assert RaidUseCase(FakeCollector(raid=rc3), ctl).locate(
        {"slot": "252:0", "action": "start"})["error"] == "slot 不在阵列卡接管盘列表中"
    out = RaidUseCase(FakeCollector(raid=rc), ctl).locate({"slot": "252:0", "action": "start"})
    assert out["ok"] is True
    assert ctl.calls[-1] == (["/c0", "/e252", "/s0", "start", "locate"], 20)  # 不含二进制路径


def test_raid_cc_gates_and_args():
    ctl = FakeRaidCtl()
    uc = RaidUseCase(FakeCollector(raid={}), ctl)
    assert "JBOD" in uc.cc("GET", "0", {})["error"]          # 无 VD
    uc_vd = RaidUseCase(FakeCollector(raid={"virtual_drives": [{"dgvd": "0/0"}]}), ctl)
    assert uc_vd.cc("GET", None, {}) == {"ok": False, "error": "invalid vd"}
    assert uc_vd.cc("POST", None, {"vd": "9", "action": "start"}) == {"ok": False, "error": "invalid vd"}
    assert uc_vd.cc("POST", None, {"vd": "0", "action": "boom"})["error"].startswith("invalid action")
    out = uc_vd.cc("POST", None, {"vd": "0/0", "action": "start", "force": True})
    assert out["ok"] is True and out["vd"] == "0"
    assert ctl.calls[-1] == (["/c0", "/v0", "start", "cc", "force"], 30)


def test_raid_cc_schedule_clamp():
    ctl = FakeRaidCtl()
    out = RaidUseCase(FakeCollector(), ctl).cc_schedule("POST", {"hour": 99, "minute": -5,
                                                                 "weekday": 9, "period": "x"})
    assert out["ok"] is True
    assert (ctl.saved["hour"], ctl.saved["minute"], ctl.saved["weekday"], ctl.saved["period"]) == (23, 0, 6, "daily")


def test_raid_hotspare_chain():
    ctl = FakeRaidCtl()
    uc = RaidUseCase(FakeCollector(raid={"mode": "jbod"}), ctl)
    assert "非 MegaRAID" in uc.hotspare("POST", {"slot": "252:0", "action": "add"})["error"]
    uc2 = RaidUseCase(FakeCollector(raid={"mode": "mega"}), ctl)
    assert uc2.hotspare("GET", {}) == {"ok": True, "hotspares": []}
    assert uc2.hotspare("POST", {"slot": "x", "action": "add"})["error"].startswith("invalid slot")
    out = uc2.hotspare("POST", {"slot": "252:0", "action": "add_dedicated"})
    assert out["ok"] is True
    assert ctl.calls[-1] == (["/c0", "/e252", "/s0", "add", "hotsparedrive", "dedicated"], 30)


def test_raid_copyback_chain():
    ctl = FakeRaidCtl()
    rc = {"mode": "mega", "virtual_drives": [{"dgvd": "0/0"}],
          "drives": [{"slot": "252:0", "copyback_active": True}], "copyback_needed": ["252:1"]}
    uc = RaidUseCase(FakeCollector(raid=rc), ctl)
    got = uc.copyback("GET", {})
    assert got["copyback_active"] == ["252:0"] and got["auto_copyback"] == "unknown"
    assert uc.copyback("POST", {"action": "boom"})["error"].startswith("invalid action")
    out = uc.copyback("POST", {"action": "disable"})
    assert out == {"ok": True, "action": "disable", "auto_copyback": "off", "out": "Status = Success"}
    assert ctl.calls[-1] == (["/c0", "set", "copyback=off"], 30)
    out2 = uc.copyback("POST", {"action": "start", "slot": "252:0"})
    assert out2["ok"] is True and ctl.calls[-1][0] == ["/c0", "/e252", "/s0", "start", "copyback"]
