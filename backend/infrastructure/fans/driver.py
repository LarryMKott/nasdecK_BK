"""风扇硬件驱动适配器：实现 domain.interfaces.fan_driver.IFanDriver。

包装 hwmon sysfs 读写原函数（签名逐一对齐，逻辑零改动）。
"""
from backend.domain.interfaces.fan_driver import IFanDriver
from backend.infrastructure.fans import hwmon


class HwmonFanDriver(IFanDriver):
    def read_raw(self, hwmon: str, idx: int) -> int | None:
        return hwmon._fan_read_raw(hwmon, idx)

    def write_raw(self, hwmon: str, idx: int, raw: int) -> None:
        hwmon._fan_write_raw(hwmon, idx, raw)

    def set_enable(self, hwmon: str, idx: int, val: bool) -> None:
        hwmon._fan_set_enable(hwmon, idx, val)

    def release_auto(self, hwmon: str, idx: int) -> None:
        hwmon._fan_release_auto(hwmon, idx)

    def read_pwm_mode(self, hwmon: str, idx: int) -> int | None:
        return hwmon._fan_read_pwm_mode(hwmon, idx)

    def set_pwm_mode(self, hwmon: str, idx: int, mode: int) -> None:
        hwmon._fan_set_pwm_mode(hwmon, idx, mode)

    def release_to_idle(self) -> None:
        hwmon._fan_release_to_idle()
