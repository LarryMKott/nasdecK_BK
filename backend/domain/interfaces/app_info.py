"""应用信息抽象契约（阶段 3 扩展）：版本号读取（fnos_version 涉及文件 IO）。"""
from abc import ABC, abstractmethod


class IAppInfo(ABC):
    @abstractmethod
    def nasdeck_version(self) -> str:
        """nasdeck 应用版本（APP_VERSION）。"""

    @abstractmethod
    def fnos_version(self) -> str:
        """飞牛OS 版本（读系统文件，_fnos_version）。"""
