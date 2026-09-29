"""阵列卡面板用例：/api/raid* 六端点的编排核。

自 presentation/api/endpoints/raid.py 原样搬迁（v2.4 阶段 3 批 B）：
slot/vd/action 校验链、storcli 参数拼装、出参判定逐行等价。
vd 序号解析经 domain.rules.raid_rule.raid_cc_vnum（纯规则收敛 domain），
storcli 执行与 CC 调度存储经 IRaidControl，板块数据经 IHardwareCollector。
注意：本组端点错误路径全部 200 + {"ok": false}（原样保留，不改状态码）。
"""
import re
import time

from backend.domain.interfaces.collector_repo import IHardwareCollector
from backend.domain.interfaces.raid_control import IRaidControl
from backend.domain.rules.raid_rule import raid_cc_vnum


def _panel_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class RaidUseCase:
    def __init__(self, collector: IHardwareCollector, raid_ctl: IRaidControl):
        self._c = collector
        self._ctl = raid_ctl

    def panel(self) -> dict:
        """阵列卡板块。#raid 渲染同时依赖 raid + disks，故一并返回（raid 带 60s 缓存，disks 带 300s 缓存）。"""
        t0 = time.time()
        try:
            return {"raid": self._c.get_raid_card(), "disks": self._c.get_disks(),
                    "cc_schedule": self._ctl.schedule_load(),
                    "time": _panel_time(), "elapsed": round(time.time() - t0, 2)}
        except Exception as e:
            return {"error": str(e), "time": _panel_time()}

    def locate(self, data: dict) -> dict:
        """物理盘定位（locate LED 闪烁）。仅当控制器/背板支持时可用（locate_supported）。"""
        try:
            slot = str(data.get("slot", "")).strip()
            action = str(data.get("action", "")).strip().lower()
            if not re.match(r"^\d+:\d+$", slot):
                return {"ok": False, "error": "invalid slot"}
            if action not in ("start", "stop"):
                return {"ok": False, "error": "invalid action"}
            rc = self._c.get_raid_card()
            if not rc.get("locate_supported"):
                return {"ok": False, "error": "本机阵列卡/背板不支持定位闪灯"}
            valid = {d.get("slot") for d in (rc.get("drives") or [])}
            if slot not in valid:
                return {"ok": False, "error": "slot 不在阵列卡接管盘列表中"}
            e, s = slot.split(":")
            verb = "start" if action == "start" else "stop"
            out = self._ctl.storcli(["/c0", "/e" + e, "/s" + s, verb, "locate"], 20)
            ok = bool(out and "Succeeded" in out)
            return {"ok": ok, "action": action, "slot": slot, "out": out}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def cc(self, method: str, vd_query, data: dict) -> dict:
        """阵列卡一致性检查（Consistency Check）。

        仅对存在的硬件 RAID 逻辑盘（VD）执行；GET ?vd=0 查询进度，POST {vd, action} 执行。
        """
        try:
            rc = self._c.get_raid_card()
            vds = rc.get("virtual_drives") or []
            if not vds:
                return {"ok": False,
                        "error": "本机无硬件 RAID 逻辑盘（VD），无法执行一致性检查（当前为 JBOD 直通模式，阵列由系统层 mdadm 管理）"}
            if method == "GET":
                vnum = raid_cc_vnum(vd_query, vds)
                if vnum is None:
                    return {"ok": False, "error": "invalid vd"}
                out = self._ctl.storcli(["/c0", "/v" + vnum, "show", "cc"], 15) or ""
                prog = re.search(r"Progress\s*=?\s*(\d+)%", out)
                pct = int(prog.group(1)) if prog else None
                state = "idle"
                if re.search(r"running|In progress|Active", out, re.I):
                    state = "running"
                elif re.search(r"paused|suspended", out, re.I):
                    state = "paused"
                return {"ok": True, "vd": vnum, "state": state, "progress": pct, "raw": out}
            vnum = raid_cc_vnum(data.get("vd"), vds)
            if vnum is None:
                return {"ok": False, "error": "invalid vd"}
            action = str(data.get("action", "")).strip().lower()
            if action not in ("start", "pause", "resume", "stop"):
                return {"ok": False, "error": "invalid action (start|pause|resume|stop)"}
            force = bool(data.get("force", False))
            cmd = ["/c0", "/v" + vnum, action, "cc"]
            if action == "start" and force:
                cmd.append("force")
            out = self._ctl.storcli(cmd, 30) or ""
            ok = ("Succeeded" in out) or ("Status = Success" in out)
            return {"ok": ok, "vd": vnum, "action": action, "out": out}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def cc_schedule(self, method: str, data: dict) -> dict:
        """一致性检查定时调度配置。
        GET：返回当前配置；POST {enabled, period, hour, minute, weekday, vd}：保存。
        配置本身与是否存在 VD 无关（保存合法）；真正触发由 _check_cc_schedule 按时执行，
        无 VD 时静默跳过。前端仅在存在硬件逻辑盘时展示该配置 UI。"""
        if method == "GET":
            return self._ctl.schedule_load()
        try:
            cfg = self._ctl.schedule_load()
            if "enabled" in data:
                cfg["enabled"] = bool(data["enabled"])
            if "period" in data:
                cfg["period"] = "weekly" if str(data["period"]) == "weekly" else "daily"
            if "hour" in data:
                cfg["hour"] = max(0, min(23, int(data["hour"])))
            if "minute" in data:
                cfg["minute"] = max(0, min(59, int(data["minute"])))
            if "weekday" in data:
                cfg["weekday"] = max(0, min(6, int(data["weekday"])))
            if "vd" in data:
                cfg["vd"] = data["vd"]
            self._ctl.schedule_save(cfg)
            return {"ok": True, "config": cfg}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def hotspare(self, method: str, data: dict) -> dict:
        """阵列卡热备盘分配（全局 GHS / 专用 DHS）。
        GET：返回当前热备盘列表；POST {slot, action}：add(全局) | add_dedicated(专用) | remove。"""
        try:
            rc = self._c.get_raid_card()
            if rc.get("mode") != "mega":
                return {"ok": False, "error": "当前非 MegaRAID 模式，无法管理热备盘"}
            if method == "GET":
                return {"ok": True, "hotspares": rc.get("hotspares", [])}
            slot = str(data.get("slot", "")).strip()
            m = re.match(r"^(\d+):(\d+)$", slot)
            if not m:
                return {"ok": False, "error": "invalid slot (需形如 252:0)"}
            e, s = m.group(1), m.group(2)
            action = str(data.get("action", "")).strip().lower()
            if action not in ("add", "add_dedicated", "remove"):
                return {"ok": False, "error": "invalid action (add|add_dedicated|remove)"}
            if action == "remove":
                cmd = ["/c0", "/e" + e, "/s" + s, "delete", "hotsparedrive"]
            else:
                cmd = ["/c0", "/e" + e, "/s" + s, "add", "hotsparedrive"]
                if action == "add_dedicated":
                    cmd.append("dedicated")
            out = self._ctl.storcli(cmd, 30) or ""
            ok = ("Succeeded" in out) or ("Status = Success" in out)
            return {"ok": ok, "action": action, "slot": slot, "out": out}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def copyback(self, method: str, data: dict) -> dict:
        """阵列卡 CopyBack 自动换盘（监控 + 触发）。
        GET：开关状态 + 当前正在/待换盘的盘；POST {action}：enable | disable | start。"""
        try:
            rc = self._c.get_raid_card()
            if rc.get("mode") != "mega":
                return {"ok": False, "error": "当前非 MegaRAID 模式，无 CopyBack 能力"}
            if not rc.get("virtual_drives"):
                return {"ok": False, "error": "当前无硬件 RAID 逻辑盘（VD），CopyBack 仅在已配置热备盘的阵列环境下生效"}
            if method == "GET":
                return {
                    "ok": True,
                    "auto_copyback": rc.get("auto_copyback", "unknown"),
                    "copyback_active": [d["slot"] for d in rc.get("drives", []) if d.get("copyback_active")],
                    "copyback_needed": rc.get("copyback_needed", []),
                }
            action = str(data.get("action", "")).strip().lower()
            if action in ("enable", "disable"):
                val = "on" if action == "enable" else "off"
                out = self._ctl.storcli(["/c0", "set", "copyback=" + val], 30) or ""
                ok = ("Succeeded" in out) or ("Status = Success" in out)
                return {"ok": ok, "action": action, "auto_copyback": val, "out": out}
            if action == "start":
                slot = str(data.get("slot", "")).strip()
                m = re.match(r"^(\d+):(\d+)$", slot)
                if not m:
                    return {"ok": False, "error": "invalid slot (需形如 252:0)"}
                e, s = m.group(1), m.group(2)
                out = self._ctl.storcli(["/c0", "/e" + e, "/s" + s, "start", "copyback"], 30) or ""
                ok = ("Succeeded" in out) or ("Status = Success" in out)
                return {"ok": ok, "action": "start", "slot": slot, "out": out}
            return {"ok": False, "error": "invalid action (enable|disable|start)"}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}
