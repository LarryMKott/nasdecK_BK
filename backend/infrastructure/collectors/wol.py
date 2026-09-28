# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.command import cmd_runner as shell
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
import os
import re


# ---------------- WOL（网络唤醒）开关 + 持久化（v2.3.0 3.2） ----------------
# 只对「网卡硬件支持 WOL」的口开放。设置后写进配置，进程启动时回放一次——
# 因为 `ethtool -s` 改的值在部分网卡/主板组合下重启不保留，不回放就会「关了又开」。

def _wol_cfg_path():
    return os.path.join(_config_dir(), "wol.json")


def _wol_load_cfg():
    d = _load_json_file(_wol_cfg_path(), {})
    return d if isinstance(d, dict) else {}


def _wol_save_cfg(d):
    try:
        _save_json_file(_wol_cfg_path(), d)
    except Exception:
        pass


def _wol_phy_name(name):
    """展示名 -> 物理口名。fnOS 的 OVS 桥（eno1-ovs）与物理口（eno1）共享 MAC，
    页面展示桥名，但 WOL 必须写在物理口上。"""
    n = str(name or "").strip()
    return n[:-4] if n.endswith("-ovs") else n


def _wol_read_modes(phy):
    """读 `ethtool <phy>` 的 Supports/Wake-on 两行 -> (硬件能力串, 当前模式串)。"""
    out = shell.run_cmd(["ethtool", phy], 3, quiet=True)
    sup = ""
    cur = ""
    for line in out.splitlines():
        t = line.strip()
        if t.startswith("Supports Wake-on:"):
            sup = t.split(":", 1)[1].strip()
        elif t.startswith("Wake-on:"):
            cur = t.split(":", 1)[1].strip()
    return sup, cur


def _wol_hw_supported(phy):
    """能力串里除 'd'（禁用）外含 p/u/m/b/g/a 任一 = 该网卡硬件支持 WOL。"""
    sup, _ = _wol_read_modes(phy)
    return any(ch in sup for ch in "pumbga")


def _wol_apply(phy, enable):
    """应用 WOL 开关，返回 (ok, err)。只动用户点的那张网卡，不做其它系统改动。"""
    if not re.match(r"^[A-Za-z0-9_.:@-]{1,32}$", phy or ""):
        return False, "网卡名不合法"
    if not os.path.isdir("/sys/class/net/" + phy):
        return False, "找不到网卡 " + phy
    # 先读能力串：读不到（如系统缺 ethtool）与「真的不支持」要分开提示，
    # 否则会把「工具缺失」误报成「你网卡不行」，误导用户。
    _sup, _ = _wol_read_modes(phy)
    if not _sup:
        return False, "读不到这张网卡的唤醒能力（系统可能缺少 ethtool 工具）"
    if not any(ch in _sup for ch in "pumbga"):
        return False, "该网卡硬件不支持网络唤醒（WOL）"
    shell.run_cmd(["ethtool", "-s", phy, "wol", ("g" if enable else "d")], 5, quiet=True)
    _, cur = _wol_read_modes(phy)
    if any(ch in cur for ch in "pumbga") != bool(enable):
        return False, "设置未生效（可能被系统策略或驱动覆盖）"
    return True, ""


def _wol_reapply_on_start():
    """开机把用户设过的 WOL 开关重新应用一遍（ethtool 设置不保证跨重启保留）。"""
    cfg = _wol_load_cfg()
    if not cfg:
        return
    changed = False
    for phy, want in list(cfg.items()):
        try:
            if not os.path.isdir("/sys/class/net/" + str(phy)):
                cfg.pop(phy, None)      # 网卡已拆走，顺手清配置项
                changed = True
                continue
            _wol_apply(str(phy), bool(want))
        except Exception:
            pass
    if changed:
        _wol_save_cfg(cfg)
