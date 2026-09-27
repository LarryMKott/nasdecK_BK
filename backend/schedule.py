# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend import shell
from backend.collectors.raid import get_raid_card
from backend.paths import _config_dir
from backend.shell import STORCLI
import json
import os
import re
import time


def _raid_cc_vnum(vd_arg, vds):
    """把前端传入的 vd 参数（纯数字或 dgvd 如 0/0）解析为 storcli 可用的 vX 序号，并校验存在。"""
    if vd_arg is None:
        return None
    vd_arg = str(vd_arg).strip()
    vnum = vd_arg.split("/")[1] if re.match(r"^\d+/\d+$", vd_arg) else vd_arg
    if not re.match(r"^\d+$", vnum):
        return None
    if 0 <= int(vnum) < len(vds):
        return vnum
    valid = set()
    for v in vds:
        dgvd = v.get("dgvd", "")
        if re.match(r"^\d+/\d+$", dgvd):
            valid.add(dgvd.split("/")[1])
    return vnum if vnum in valid else None


_CC_SCHEDULE_FILE = os.path.join(_config_dir(), "cc_schedule.json")
_cc_sched_last_check = 0


def _load_cc_schedule():
    """读取一致性检查定时调度配置（JSON），缺失字段补默认值。"""
    try:
        with open(_CC_SCHEDULE_FILE) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    d.setdefault("enabled", False)
    d.setdefault("period", "daily")      # daily | weekly
    d.setdefault("hour", 3)
    d.setdefault("minute", 0)
    d.setdefault("weekday", 0)           # 0=周一 … 6=周日（仅 weekly 用）
    d.setdefault("vd", "all")            # all | 特定 dgvd 如 "0/0"
    d.setdefault("last_run", None)       # 上次触发的周期键，防重复触发
    return d


def _save_cc_schedule(cfg):
    try:
        with open(_CC_SCHEDULE_FILE, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _check_cc_schedule():
    """由 metrics_collect_loop 周期调用：到点则对硬件逻辑盘触发一致性检查。
    无 VD（如 JBOD 直通）或配置未启用时静默返回，绝不抛异常。"""
    try:
        cfg = _load_cc_schedule()
        if not cfg.get("enabled"):
            return
        now = time.localtime()
        if int(cfg.get("hour", 3)) != now.tm_hour or int(cfg.get("minute", 0)) != now.tm_min:
            return
        if cfg.get("period") == "weekly" and int(cfg.get("weekday", 0)) != now.tm_wday:
            return
        cycle_key = time.strftime("%Y-W%W") if cfg.get("period") == "weekly" else time.strftime("%Y-%m-%d")
        if cfg.get("last_run") == cycle_key:
            return  # 本周期已触发
        try:
            rc = get_raid_card()
            vds = rc.get("virtual_drives") or []
        except Exception:
            vds = []
        if not vds:
            # 无 VD（JBOD 直通等）：更新 last_run 避免反复尝试，不报错
            cfg["last_run"] = cycle_key
            _save_cc_schedule(cfg)
            return
        targets = vds
        sel = cfg.get("vd")
        if sel not in ("all", None, ""):
            targets = [v for v in vds if v.get("dgvd") == sel]
        for v in targets:
            vnum = _raid_cc_vnum(v.get("dgvd") or "0/0", vds)
            if vnum is None:
                continue
            try:
                shell.sudo_cmd([STORCLI, "/c0", "/v" + vnum, "start", "cc"], 30)
            except Exception:
                pass
        cfg["last_run"] = cycle_key
        _save_cc_schedule(cfg)
    except Exception:
        pass
