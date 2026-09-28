"""风扇控制领域实体。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FanDecision:
    """一次温控决策的结果（替代原模块级 _st_engaged / _dt_engaged 全局态的返回值形态）。

    action:  "control" → nasdash 接管，pwm 为目标 raw(0~255)；"release" → 交还主板/内核自动
    pwm:     目标占空 raw；action=release 时为 None
    engaged: 本次决策后风扇是否处于 nasdash 接管态。原实现把该标志写在模块级全局 dict，
             现改为随值返回，由 infrastructure 持有者自行保存 —— 滞回状态归属不变，
             只是规则函数不再写全局（纯函数化，可脱离硬件单测）。
    """
    action: str
    pwm: int | None
    engaged: bool
