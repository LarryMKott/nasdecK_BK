"""风扇温控纯规则单测（纯 IO-free）。

曲线/线性换算与存量 backend/fans/rules.py 逐式一致；个别断言直接复用同式计算
（round(x/100*255)），目的是把现行为钉死，防止阶段 2 换线时漂移。
"""
from backend.domain.rules.fan_rule import (NVME_FULL_TEMP, NVME_START_TEMP,
                                           disk_source_max_temp, disk_temp_decision,
                                           disk_temp_pwm, fan_curve_pwm, nvme_temp_as_hdd,
                                           sys_temp_decision, sys_temp_pwm)


# ---------- NVMe 量纲换算 ----------

def test_nvme_below_start_excluded():
    assert nvme_temp_as_hdd(50, 40, 60) is None
    assert nvme_temp_as_hdd(NVME_START_TEMP - 0.5, 40, 60) is None


def test_nvme_boundaries_map_linear():
    assert nvme_temp_as_hdd(NVME_START_TEMP, 40, 60) == 40
    assert nvme_temp_as_hdd(NVME_FULL_TEMP, 40, 60) == 60
    assert abs(nvme_temp_as_hdd(72.5, 40, 60) - 50.0) < 1e-9


def test_nvme_non_numeric_none():
    assert nvme_temp_as_hdd("n/a", 40, 60) is None


# ---------- 自定义曲线 ----------

def test_curve_invalid_or_missing_returns_none():
    assert fan_curve_pwm(50, {}, 30, 100) is None
    assert fan_curve_pwm(50, {"curve": [[40, 30]]}, 30, 100) is None
    assert fan_curve_pwm(50, {"curve": "x"}, 30, 100) is None


def test_curve_interpolation_and_edges():
    cfg = {"curve": [[40, 30], [60, 100]], "min_pwm": 30, "max_pwm": 100}
    # 低于曲线起点（start_temp 缺省取首点温度）→ 0
    assert fan_curve_pwm(30, cfg, 30, 100) == 0
    # 中点线性：30 + (10/20)*70 = 65 → round(65/100*255)
    assert fan_curve_pwm(50, cfg, 30, 100) == round(65 / 100 * 255)
    # 超过末点 → 末点占空（clamp 在 min/max 内）
    assert fan_curve_pwm(90, cfg, 30, 100) == 255


def test_curve_clamped_by_min_max():
    cfg = {"curve": [[40, 5], [60, 120]], "min_pwm": 30, "max_pwm": 100}
    assert fan_curve_pwm(40, cfg, 30, 100) == round(30 / 100 * 255)   # 低点被 min 抬升
    assert fan_curve_pwm(60, cfg, 30, 100) == round(100 / 100 * 255)  # 高点被 max 压回


# ---------- 线性回退 ----------

def test_sys_temp_pwm_linear_fallback():
    cfg = {}  # 无曲线：start45 full70 min30 max100
    assert sys_temp_pwm(50, cfg) == round(44 / 100 * 255)  # r=0.2 → raw 44
    assert sys_temp_pwm(40, cfg) == round(30 / 100 * 255)  # 低于开转维持 min 占空
    assert sys_temp_pwm(80, cfg) == 255                    # 满转


def test_sys_temp_pwm_curve_wins():
    cfg = {"curve": [[40, 30], [60, 100]], "start_temp": 45, "full_temp": 70}
    assert sys_temp_pwm(50, cfg) == round(65 / 100 * 255)  # 命中曲线而非线性


def test_sys_temp_pwm_none_temp():
    assert sys_temp_pwm(None, {}) is None


def test_sys_temp_decision_engaged_flag():
    d = sys_temp_decision(60, {})
    assert (d.action, d.engaged) == ("control", True) and d.pwm == sys_temp_pwm(60, {})
    r = sys_temp_decision(None, {})
    assert (r.action, r.pwm, r.engaged) == ("release", None, False)


# ---------- 硬盘温度源 ----------

def test_disk_source_max_temp_nvme_remap():
    valid = [{"temp": 42}, {"temp": 55, "is_nvme": True}, {"temp": 70, "is_nvme": True}]
    t = disk_source_max_temp(valid, 40, 60)
    # NVMe 55 低于 65 不参与；NVMe 70 映射到 40+((70-65)/15)*20；机械盘 42
    assert abs(t - (40 + (5 / 15) * 20)) < 1e-9


def test_disk_source_max_temp_skips_non_numeric():
    assert disk_source_max_temp([{"temp": "n/a"}, {"dev": "sdb"}], 40, 60) is None
    assert disk_source_max_temp([{"temp": "n/a"}, {"temp": 45}], 40, 60) == 45


# ---------- 硬盘 PWM / 决策 ----------

def test_disk_temp_pwm_empty_states():
    assert disk_temp_pwm({}, {}, all_idle=False) is None


def test_disk_temp_pwm_all_idle_stop():
    states = {"sda": {"temp": 30, "asleep": True}}
    assert disk_temp_pwm(states, {}, all_idle=True) == 0


def test_disk_temp_pwm_no_temp_conservative():
    # 完全读不到温度 → 保守兜底按 min 占空转起来（怕真热却不知道）
    assert disk_temp_pwm({"sda": {"dev": "sda"}}, {}, all_idle=False) == round(30 / 100 * 255)


def test_disk_temp_pwm_below_start_keeps_min():
    assert disk_temp_pwm({"sda": {"temp": 35}}, {}, all_idle=False) == round(30 / 100 * 255)


def test_disk_temp_pwm_linear_and_curve():
    assert disk_temp_pwm({"sda": {"temp": 50}}, {}, all_idle=False) == round(50 / 100 * 255)
    cfg = {"curve": [[40, 30], [60, 100]]}
    assert disk_temp_pwm({"sda": {"temp": 50}}, cfg, all_idle=False) == round(65 / 100 * 255)


def test_disk_temp_decision_matrix():
    # 空状态 → release（交还主板）
    d0 = disk_temp_decision({}, {}, all_idle=False)
    assert (d0.action, d0.pwm, d0.engaged) == ("release", None, False)
    # 全部休眠 → control/0 但 engaged=False（存量语义：接管写 0，不算"接管中"）
    d = disk_temp_decision({"sda": {"temp": 30}}, {}, all_idle=True)
    assert (d.action, d.pwm, d.engaged) == ("control", 0, False)
    # 有效温度 → 接管
    d2 = disk_temp_decision({"sda": {"temp": 50}}, {}, all_idle=False)
    assert d2.engaged is True and d2.action == "control"
    # 读得到盘但读不到温度 → release
    d3 = disk_temp_decision({"sda": {"dev": "sda"}}, {}, all_idle=False)
    assert d3.action == "release"
