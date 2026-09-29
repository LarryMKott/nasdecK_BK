"""风扇温控规则/曲线配置仓储抽象契约（阶段 3 批 B 新增）。

实现方：backend/infrastructure/fans/rules_repo_impl.py（包装 rules.py 存量函数）。
覆盖三份配置存储（逐扇规则 fan_rules / 硬盘温控 fan_disk_temp /
主板温控 fan_sys_temp）与温度源读取、目标 PWM 计算。
滞回状态与平滑步进留在 infrastructure（开发计划决策 #8）。
"""
from abc import ABC, abstractmethod


class IFanRulesRepo(ABC):
    """风扇温控规则与曲线配置存储。"""

    # ---- 逐风扇规则 ----

    @abstractmethod
    def load_rules_raw(self) -> dict | None:
        """用户逐扇规则原始文件内容（未自定义过返回 None）。"""

    @abstractmethod
    def save_rules(self, d: dict) -> bool:
        """写逐扇规则文件。"""

    @abstractmethod
    def effective_rules(self) -> dict:
        """生效规则表（旧全局面板派生默认 + 用户逐扇覆盖，key="hwmon::idx"）。"""

    @abstractmethod
    def rule_sources(self) -> tuple:
        """合法温度源常量（"disk"/"cpu"/"mb"/"raid"）。"""

    @abstractmethod
    def validate_rule(self, r) -> tuple:
        """校验单条规则，返回 (clean_rule|None, error|None)；r=None 表示删除。"""

    @abstractmethod
    def rule_pwm(self, T, rule: dict):
        """按规则计算目标 raw（纯计算核在 domain.rules.fan_rule，此处为壳）。"""

    # ---- 硬盘温控（旧全局面板）----

    @abstractmethod
    def load_disk_temp(self) -> dict:
        """硬盘温控配置。"""

    @abstractmethod
    def save_disk_temp(self, cfg: dict) -> bool:
        """写硬盘温控配置。"""

    @abstractmethod
    def disk_source_state(self, dt_cfg: dict) -> tuple:
        """监控盘温度源状态 (all_idle, temp, has)。"""

    @abstractmethod
    def disk_temp_pwm(self, states: dict, cfg: dict):
        """按盘温计算目标 raw（None=不参与）。"""

    # ---- 主板/CPU 温控（旧全局面板）----

    @abstractmethod
    def load_sys_temp(self) -> dict:
        """主板/CPU 温控配置。"""

    @abstractmethod
    def save_sys_temp(self, cfg: dict) -> bool:
        """写主板/CPU 温控配置。"""

    @abstractmethod
    def read_sys_temp(self, source: str):
        """读温度源实时读数（cpu/mb/raid；异常返回 None）。"""

    @abstractmethod
    def sys_temp_pwm(self, T, cfg: dict):
        """按 cpu/mb 温度计算目标 raw（None=不参与）。"""

    @abstractmethod
    def apply_temp_curve(self, cfg: dict, data: dict, recover_max: int):
        """把 HTTP 配置体套用到 cfg（disk_temp/sys_temp 共用）。
        返回 ({"ok": False, "error": ...}, 状态码) 或 None（成功）。"""
