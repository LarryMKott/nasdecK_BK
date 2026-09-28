"""磁盘健康分级规则单测（纯 IO-free）。

断言语义对齐存量 _grade_disk_health 的行为（阶段 2 换线后由同一套用例守护两边一致性）。
"""
from backend.domain.rules.health_judge import grade_disk_health


def test_green_healthy_disk():
    g = grade_disk_health({"health": "PASSED", "temp": 35, "temp_trip": 55,
                           "reallocated": 0, "pending": 0, "uncorrectable": 0})
    assert g.level == "green" and g.label == "正常"
    assert g.reasons == ()
    assert g.counters["temp"] == 35 and g.counters["reallocated"] == 0
    assert g.source == "系统 SMART"


def test_to_dict_keys_match_legacy_order():
    d = grade_disk_health({"health": "OK"}).to_dict()
    assert list(d.keys()) == ["level", "label", "reasons", "counters", "source"]


def test_red_failed_verdict():
    g = grade_disk_health({"health": "FAILED"})
    assert g.level == "red" and g.label == "异常"
    assert any("FAILED" in r for r in g.reasons)


def test_unknown_when_asleep():
    g = grade_disk_health({"asleep": True, "health": "PASSED"})
    assert g.level == "unknown" and g.source == "休眠"


def test_red_pending_sectors():
    g = grade_disk_health({"health": "OK", "pending": 4})
    assert g.level == "red"
    assert any("待处理扇区 4" in r for r in g.reasons)


def test_yellow_reallocated_only():
    g = grade_disk_health({"health": "OK", "reallocated": 8})
    assert g.level == "yellow" and g.label == "注意"
    assert any("重映射扇区 8" in r for r in g.reasons)


def test_pd_side_red_alerts():
    g = grade_disk_health({"health": "OK"},
                          pd={"state": "FAILED", "pd_health": {"smart_alert": True}})
    assert g.level == "red"
    assert any("S.M.A.R.T 告警" in r for r in g.reasons)
    assert any("不在线" in r for r in g.reasons)
    assert g.source == "阵列卡 + 系统 SMART"  # pd 传入即视为阵列卡来源，verdict 非空叠加系统 SMART


def test_sas_defects_yellow_only_without_reallocated():
    g = grade_disk_health({"health": "OK", "defects": 100})
    assert g.level == "yellow" and g.counters["defects"] == 100
    # 已有重映射计数时，SAS 缺陷表不再重复报（存量行为）
    g2 = grade_disk_health({"health": "OK", "defects": 100, "reallocated": 5})
    assert not any("缺陷扇区" in r for r in g2.reasons)
    assert g2.counters["defects"] == 100


def test_udma_crc_in_counters_but_never_grades():
    g = grade_disk_health({"health": "OK", "udma_crc": 12})
    assert g.level == "green" and g.counters["udma_crc"] == 12
    assert not any("UDMA" in r or "udma" in r for r in g.reasons)


def test_temp_over_trip_yellow():
    g = grade_disk_health({"health": "OK", "temp": 60, "temp_trip": 55})
    assert g.level == "yellow" and any("高温线 55" in r for r in g.reasons)


def test_nvme_critical_warning_yellow():
    g = grade_disk_health({"health": "OK", "critical_warning": "0x01"})
    assert g.level == "yellow" and any("临界告警" in r for r in g.reasons)


def test_empty_input_unknown():
    g = grade_disk_health({})
    assert g.level == "unknown"
    assert g.reasons == ("读不到这块盘的健康数据（可能已休眠或无法访问）",)
    assert g.source == "—"


def test_ssd_life_over_80_yellow():
    g = grade_disk_health({"health": "OK", "percentage_used": 85})
    assert g.level == "yellow" and any("寿命已用 85%" in r for r in g.reasons)
