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
    def read_pwm_mode(self, hwmon: str, idx: int) -> str | None:
        """读 pwm_mode（'pwm'=4pin 脉宽 / 'dc'=3pin 电压 / None=该通道无此寄存器）。"""

    @abstractmethod
    def set_pwm_mode(self, hwmon: str, idx: int, mode: str) -> tuple:
        """写 pwm_mode（'pwm' | 'dc'），返回 (ok, err_msg)。"""

    @abstractmethod
    def release_to_idle(self) -> None:
        """整机风扇交还空闲/自动态（停用接管时的兜底动作）。"""

    # ---- 阶段 3 批 B 扩展（fan_control_usecase 需要）----

    @abstractmethod
    def enumerate_fans(self) -> list:
        """本机可控风扇通道全集 [(hwmon, idx), ...]（实时枚举，权威 key）。"""

    @abstractmethod
    def control_enabled(self) -> bool:
        """风扇接管总开关当前值（活读 infrastructure 单点全局，非导入期快照）。"""

    @abstractmethod
    def takeover(self, enabled: bool) -> bool:
        """切换接管总开关（基础设施侧整套操作：持久化配置 + 活写全局 +
        清 FCS 状态缓存 + FCS 停服/风扇释放线程）。返回 False = 配置写失败。"""

    @abstractmethod
    def get_status(self) -> list:
        """风扇实时状态列表（转速/占空比/模式/规则目标，供前端高频轮询）。"""

    @abstractmethod
    def read_fan_drivers(self) -> dict:
        """主板风扇芯片驱动清单（/sys/class/hwmon 枚举）。"""

    @abstractmethod
    def fcs_status(self, clear: bool = False) -> dict:
        """FCS（飞牛 FanControlServer）状态（只读 TTL 缓存，clear=True 强制失效）。"""

    @abstractmethod
    def fcs_board_cfg(self) -> dict:
        """FCS 板卡配置缓存内容。"""

    @abstractmethod
    def fcs_has_board_config(self) -> bool:
        """FCS 是否真的配置了风扇参数（空配置的服务只是空跑）。"""

    @abstractmethod
    def fcs_disable(self) -> None:
        """永久禁用 FCS（stop + disable + 持久化标志）。"""

    @abstractmethod
    def fcs_enable(self) -> None:
        """恢复 FCS（enable + start）。"""

    @abstractmethod
    def ext_service_running(self) -> bool:
        """系统风扇服务（FCS）是否正在运行。"""

    @abstractmethod
    def set_target(self, key: tuple, entry: dict) -> None:
        """设置风扇平滑过渡目标（infrastructure FAN_TARGETS 单点状态，FAN_LOCK 内写入）。"""

    @abstractmethod
    def pop_target(self, key: tuple) -> None:
        """清除风扇平滑过渡目标。"""

    @abstractmethod
    def save_fan_mode(self, idx: int, mode: str, target) -> None:
        """持久化单风扇模式（按 idx 抗 hwmon 漂移，重启自动恢复）。"""
