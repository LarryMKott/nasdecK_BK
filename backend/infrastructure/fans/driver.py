"""风扇硬件驱动适配器：实现 domain.interfaces.fan_driver.IFanDriver。

包装 hwmon sysfs 读写 / fans.enumerate / fans.fcs / fans.modes / fans.control /
state.FAN_TARGETS 的既有函数（签名逐一对齐，逻辑零改动）。
阶段 3 批 B：接管开关经 modes.set_control_enabled 活写单点全局（控速循环
以模块属性活读同一值——严禁 from-import 快照，历史回归见批B进度补记）。
"""
from backend.domain.interfaces.fan_driver import IFanDriver
from backend.infrastructure.fans import control
from backend.infrastructure.fans import enumerate as fan_enum
from backend.infrastructure.fans import fcs, hwmon, labels, modes
from backend.infrastructure.state import FAN_LOCK, FAN_TARGETS


class HwmonFanDriver(IFanDriver):
    def read_raw(self, hwmon: str, idx: int) -> int | None:
        return hwmon._fan_read_raw(hwmon, idx)

    def write_raw(self, hwmon: str, idx: int, raw: int) -> None:
        hwmon._fan_write_raw(hwmon, idx, raw)

    def set_enable(self, hwmon: str, idx: int, val) -> None:
        hwmon._fan_set_enable(hwmon, idx, val)

    def release_auto(self, hwmon: str, idx: int) -> None:
        hwmon._fan_release_auto(hwmon, idx)

    def read_pwm_mode(self, hwmon: str, idx: int) -> str | None:
        return hwmon._fan_read_pwm_mode(hwmon, idx)

    def set_pwm_mode(self, hwmon: str, idx: int, mode: str) -> tuple:
        return hwmon._fan_set_pwm_mode(hwmon, idx, mode)

    def release_to_idle(self) -> None:
        hwmon._fan_release_to_idle()

    # ---- 阶段 3 批 B 扩展 ----

    def enumerate_fans(self) -> list:
        return fan_enum._enumerate_fans()

    def control_enabled(self) -> bool:
        return modes._FAN_CTRL_ENABLED

    def takeover(self, enabled: bool) -> bool:
        return modes.set_control_enabled(enabled)

    def get_status(self) -> list:
        return control.get_fan_status()

    def read_fan_drivers(self) -> dict:
        return labels._read_fan_drivers()

    def fcs_status(self, clear: bool = False) -> dict:
        return fcs._fcs_status_cached(clear=clear)

    def fcs_board_cfg(self) -> dict:
        return fcs._fcs_board_cfg_cached()

    def fcs_has_board_config(self) -> bool:
        return fcs._fcs_has_board_config()

    def fcs_disable(self) -> None:
        fcs._fcs_disable()

    def fcs_enable(self) -> None:
        fcs._fcs_enable()

    def ext_service_running(self) -> bool:
        return fcs._fan_ext_service_running()

    def set_target(self, key: tuple, entry: dict) -> None:
        with FAN_LOCK:
            FAN_TARGETS[key] = entry

    def pop_target(self, key: tuple) -> None:
        with FAN_LOCK:
            FAN_TARGETS.pop(key, None)

    def save_fan_mode(self, idx: int, mode: str, target) -> None:
        modes._save_fan_mode(idx, mode, target)
