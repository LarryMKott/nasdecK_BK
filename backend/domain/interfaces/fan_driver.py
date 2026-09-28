"""风扇硬件驱动抽象契约。

实现方：backend/infrastructure/fans/（hwmon.py 的 sysfs 读写 + fcs.py 的 FCS 服务状态机）。
签名逐一对齐现有 hwmon 函数；调用方传 hwmon 设备定位（阶段 2 适配器封装枚举细节）。
"""
from abc import ABC, abstractmethod


class IFanDriver(ABC):
    """PWM 风扇 sysfs / FCS 驱动（全部为硬件 IO，无业务判定——判定在 domain.rules.fan_rule）。"""

    @abstractmethod
    def read_raw(self, hwmon: str, idx: int) -> int | None:
        """读当前 PWM raw 值。"""

    @abstractmethod
    def write_raw(self, hwmon: str, idx: int, raw: int) -> None:
        """写 PWM raw 值（接管态下的设速动作）。"""

    @abstractmethod
    def set_enable(self, hwmon: str, idx: int, val: bool) -> None:
        """使能/禁用该风扇口（禁用=交还主板自动控制）。"""

    @abstractmethod
    def release_auto(self, hwmon: str, idx: int) -> None:
        """交还主板自动控速（pwm_enable 还原）。"""

    @abstractmethod
    def read_pwm_mode(self, hwmon: str, idx: int) -> int | None:
        """读 pwm_mode（0=无转速信号 1=手动 2=自动 等，原样透传 sysfs 值）。"""

    @abstractmethod
    def set_pwm_mode(self, hwmon: str, idx: int, mode: int) -> None:
        """写 pwm_mode。"""

    @abstractmethod
    def release_to_idle(self) -> None:
        """整机风扇交还空闲/自动态（停用接管时的兜底动作）。"""
