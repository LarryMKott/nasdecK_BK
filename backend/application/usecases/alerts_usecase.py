"""告警面板用例：/api/alerts 三端点（查询/配置读写/测试通知）的编排核。

自 presentation/api/endpoints/alerts.py 原样搬迁（v2.4 阶段 3 批 A 第三单）：
配置合并校验逐行等价（含错误文案与 400/500 语义），差异仅——
数据获取经构造注入的领域抽象（IHardwareCollector / IHealthService / IAlertRepo / IAppInfo）。
set_config 返回 (status, body)，由端点 _jr 透传。
"""
import time

from backend.domain.interfaces.alert_repo import IAlertRepo
from backend.domain.interfaces.app_info import IAppInfo
from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.health_service import IHealthService


class AlertsUseCase:
    def __init__(self, collector: IHardwareCollector, health: IHealthService,
                 alert_repo: IAlertRepo, app_info: IAppInfo):
        self._c = collector
        self._health = health
        self._repo = alert_repo
        self._info = app_info

    def panel(self) -> dict:
        """/api/alerts：配置 + 以当前采集数据即时评估的活动告警。"""
        cfg = self._repo.load_alerts()
        system = self._c.get_system()
        disks = self._c.get_disks()
        docker = self._c.get_docker()
        system_full = {**system, "board": self._c.get_board(), "memory_modules": self._c.get_memory_modules()}
        alerts = self._health.evaluate_alerts(system_full, disks, docker)
        return {"config": cfg, "alerts": alerts, "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": True}

    def get_config(self) -> dict:
        """/api/alerts/config GET。"""
        return self._repo.load_alerts()

    def set_config(self, data: dict) -> tuple:
        """/api/alerts/config POST：逐字段合并校验（字段级错误 400，落盘失败 500）。"""
        cfg = self._repo.load_alerts()
        if "enabled" in data:
            cfg["enabled"] = bool(data["enabled"])
        if "disk_health" in data:
            cfg["disk_health"] = bool(data["disk_health"])
        if "memory_max" in data:
            try:
                cfg["memory_max"] = float(data["memory_max"])
            except (TypeError, ValueError):
                return 400, {"ok": False, "error": "memory_max 需为数字"}
        if "temp" in data and isinstance(data["temp"], dict):
            t = cfg["temp"]; nt = data["temp"]
            for k in ("cpu_max", "mb_max", "disk_max"):
                if k in nt:
                    try:
                        t[k] = float(nt[k])
                    except (TypeError, ValueError):
                        return 400, {"ok": False, "error": k + " 需为数字"}
            if "enabled" in nt:
                t["enabled"] = bool(nt["enabled"])
        if "channels" in data and isinstance(data["channels"], dict):
            ch = cfg["channels"]; nc = data["channels"]
            for name in ("telegram", "bark", "email"):
                if name in nc and isinstance(nc[name], dict):
                    cur = ch.get(name, {})
                    for fld in ("enabled", "bot_token", "chat_id", "url", "smtp_host", "smtp_port", "user", "pass", "to"):
                        if fld in nc[name]:
                            cur[fld] = nc[name][fld]
                    ch[name] = cur
            if "system" in nc:
                ch["system"] = bool(nc["system"])
        if "level_channels" in data and isinstance(data["level_channels"], dict):
            lc = cfg.get("level_channels", {})
            for lvl in ("danger", "warn", "info"):
                if lvl in data["level_channels"]:
                    val = data["level_channels"][lvl]
                    if isinstance(val, list):
                        lc[lvl] = [c for c in val if c in ("system", "telegram", "bark", "email")]
            cfg["level_channels"] = lc
        if self._repo.save_alerts(cfg):
            return 200, {"ok": True, "config": cfg}
        return 500, {"ok": False, "error": "写配置失败"}

    def test_notify(self) -> dict:
        """/api/alerts/test：向当前配置启用的渠道发一条测试通知。"""
        cfg = self._repo.load_alerts()
        text = "这是一条来自 nasdeck 的测试通知（当前版本 %s）。若收到说明通知渠道配置正确。" % self._info.nasdeck_version()
        res = self._health.dispatch_notifications(text, cfg, "danger")
        return {"ok": True, "results": res}
