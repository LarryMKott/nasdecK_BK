"""阵列卡控制适配器：实现 domain.interfaces.raid_control.IRaidControl。

storcli 执行委托 cmd_runner.sudo_cmd；CC 定时调度配置存储委托
backend/schedule.py 顶层胶水（阶段 5 随 tasks/ 迁移一并归位）。
命令参数拼装与出参判定留在 RaidUseCase（契约面）。
"""
from backend.domain.interfaces.raid_control import IRaidControl
from backend.infrastructure.command.cmd_runner import STORCLI, sudo_cmd
from backend import schedule


class RaidControl(IRaidControl):
    def storcli(self, args: list, timeout: int) -> str:
        return sudo_cmd([STORCLI] + list(args), timeout)

    def schedule_load(self) -> dict:
        return schedule._load_cc_schedule()

    def schedule_save(self, cfg: dict) -> bool:
        return schedule._save_cc_schedule(cfg)
