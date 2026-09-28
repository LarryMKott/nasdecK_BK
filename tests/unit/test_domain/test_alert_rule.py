"""告警评估规则单测（纯 IO-free，cfg/mb_temp/raid 参数化传入）。

断言语义与文案对齐存量 _evaluate_alerts（文案逐字节一致是 API 契约的一部分）。
"""
from backend.domain.rules.alert_rule import evaluate_alerts


_SYS = {"ok": 1}  # 非空即可：空 dict 会被 evaluate_alerts 的「无系统数据」短路拦下


def _cfg(**kw):
    c = {"enabled": True}
    c.update(kw)
    return c


def test_invalid_system_returns_empty():
    assert evaluate_alerts(None, [], _cfg()) == []
    assert evaluate_alerts("x", [], _cfg()) == []


def test_disabled_returns_empty():
    assert evaluate_alerts({"cpu_temp": 100}, [], _cfg(enabled=False)) == []


def test_cpu_temp_over_threshold():
    alerts = evaluate_alerts({"cpu_temp": 85}, [], _cfg(temp={"enabled": True, "cpu_max": 80}))
    assert len(alerts) == 1
    a = alerts[0]
    assert (a.level, a.title) == ("danger", "CPU 温度过高")
    assert a.detail == "CPU 封装温度 85°C，超过阈值 80°C"
    assert a.to_dict() == {"level": "danger", "title": "CPU 温度过高",
                           "detail": "CPU 封装温度 85°C，超过阈值 80°C"}


def test_mb_temp_over_threshold():
    alerts = evaluate_alerts({"sensors": {}}, [], _cfg(temp={"enabled": True, "mb_max": 65}), mb_temp=70)
    assert len(alerts) == 1 and alerts[0].title == "主板/芯片组温度过高"


def test_disk_temp_over_threshold_per_disk():
    disks = [{"dev": "/dev/sda", "temp": 45}, {"dev": "/dev/sdb", "temp": 30}, "junk"]
    alerts = evaluate_alerts(_SYS, disks, _cfg(temp={"enabled": True, "disk_max": 40}))
    assert len(alerts) == 1 and alerts[0].level == "warn"
    assert "/dev/sda" in alerts[0].detail


def test_disk_health_alert():
    disks = [{"dev": "/dev/sda", "health": "FAILED", "health_ok": False},
             {"dev": "/dev/sdb", "health": "PASSED", "health_ok": True},
             {"dev": "/dev/sdc", "health": "UNKNOWN", "health_ok": False}]
    alerts = evaluate_alerts(_SYS, disks, _cfg(disk_health=True))
    assert len(alerts) == 1
    assert (alerts[0].level, alerts[0].title) == ("danger", "硬盘健康异常")
    assert "FAILED" in alerts[0].detail


def test_memory_over_threshold():
    system = {"memory": {"percent": 95}}
    alerts = evaluate_alerts(system, [], _cfg(memory_max=90))
    assert len(alerts) == 1 and alerts[0].level == "warn"
    assert "95%" in alerts[0].detail


def test_cachevault_alerts():
    ok = {"mode": "mega", "cachevault_status": "Optimal"}
    bad = {"mode": "mega", "cachevault_status": "Degraded"}
    assert evaluate_alerts(_SYS, [], _cfg(), raid=ok) == []
    alerts = evaluate_alerts(_SYS, [], _cfg(), raid=bad)
    assert len(alerts) == 1 and alerts[0].level == "warn" and "CacheVault" in alerts[0].title


def test_virtual_drive_state_alert():
    raid = {"mode": "hba", "virtual_drives": [{"name": "vd0", "state": "Offln"}]}
    alerts = evaluate_alerts(_SYS, [], _cfg(), raid=raid)
    assert len(alerts) == 1
    assert (alerts[0].level, alerts[0].title) == ("danger", "逻辑盘状态异常")


def test_copyback_alerts():
    raid = {"mode": "mega", "drives": [{"slot": "3", "copyback_active": True}],
            "hotspares": [{"slot": "5"}]}
    alerts = evaluate_alerts(_SYS, [], _cfg(), raid=raid)
    assert len(alerts) == 1 and alerts[0].title == "正在执行 CopyBack 换盘"
    raid2 = {"mode": "mega", "drives": [{"slot": "3", "failed": True}],
             "hotspares": [{"slot": "5"}], "auto_copyback": "enabled"}
    alerts2 = evaluate_alerts(_SYS, [], _cfg(), raid=raid2)
    assert len(alerts2) == 1 and alerts2[0].title == "硬盘故障待换盘"
    assert "自动换盘" in alerts2[0].detail


def test_non_raid_mode_no_cachevault_alerts():
    assert evaluate_alerts(_SYS, [], _cfg(), raid={"mode": "sata", "cachevault": "Weird"}) == []
