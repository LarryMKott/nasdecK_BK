"""阵列卡控制抽象契约（阶段 3 批 B 新增）。

实现方：backend/infrastructure/raid_control.py（RaidControl）。
storcli 命令执行与 CC 定时调度配置存储的写路径；命令参数拼装与
出参判定留在 RaidUseCase（契约面，逐字保留）。
"""
from abc import ABC, abstractmethod


class IRaidControl(ABC):
    """storcli 写操作 + CC 定时调度存储。"""

    @abstractmethod
    def storcli(self, args: list, timeout: int) -> str:
        """执行 storcli 子命令（实现方自动前置二进制路径），返回 stdout 文本。

        args 不含二进制本身（如 ["/c0", "/v0", "show", "cc"]）——二进制路径属
        基础设施配置，用例只拼契约相关的子命令参数。"""

    @abstractmethod
    def schedule_load(self) -> dict:
        """读 CC 定时调度配置（缺失字段补默认值）。"""

    @abstractmethod
    def schedule_save(self, cfg: dict) -> bool:
        """写 CC 定时调度配置。"""
