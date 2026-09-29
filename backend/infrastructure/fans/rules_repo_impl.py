"""风扇温控规则/曲线配置仓储适配器：实现 domain.interfaces.fan_rules.IFanRulesRepo。

包装 rules.py 既有函数（逻辑零改动）；_apply_temp_curve 原样暴露
（返回 (错误体, 状态码) 或 None，由用例/端点透传）。
"""
from backend.domain.interfaces.fan_rules import IFanRulesRepo
from backend.infrastructure.fans import rules


class FanRulesRepoImpl(IFanRulesRepo):
    def load_rules_raw(self):
        return rules._load_fan_rules_raw()

    def save_rules(self, d: dict) -> bool:
        return rules._save_fan_rules(d)

    def effective_rules(self) -> dict:
        return rules._effective_fan_rules()

    def rule_sources(self) -> tuple:
        return rules._FAN_RULE_SOURCES

    def validate_rule(self, r) -> tuple:
        return rules._validate_fan_rule(r)

    def rule_pwm(self, T, rule: dict):
        return rules._fan_rule_pwm(T, rule)

    def load_disk_temp(self) -> dict:
        return rules._load_fan_disk_temp()

    def save_disk_temp(self, cfg: dict) -> bool:
        return rules._save_fan_disk_temp(cfg)

    def disk_source_state(self, dt_cfg: dict) -> tuple:
        return rules._disk_source_state(dt_cfg)

    def disk_temp_pwm(self, states: dict, cfg: dict):
        return rules._fan_disk_temp_pwm(states, cfg)

    def load_sys_temp(self) -> dict:
        return rules._load_fan_sys_temp()

    def save_sys_temp(self, cfg: dict) -> bool:
        return rules._save_fan_sys_temp(cfg)

    def read_sys_temp(self, source: str):
        return rules._fan_read_sys_temp(source)

    def sys_temp_pwm(self, T, cfg: dict):
        return rules._fan_sys_temp_pwm(T, cfg)

    def apply_temp_curve(self, cfg: dict, data: dict, recover_max: int):
        return rules._apply_temp_curve(cfg, data, recover_max=recover_max)
