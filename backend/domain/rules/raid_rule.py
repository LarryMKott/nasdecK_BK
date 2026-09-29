# -*- coding: utf-8 -*-
"""阵列卡纯规则（阶段 3 批 B 自 schedule.py 收敛）。

_raid_cc_vnum 原样搬迁为 raid_cc_vnum：把前端传入的 vd 参数解析为
storcli 可用的 vX 序号并校验存在。纯字符串/列表逻辑，零 IO。
"""
import re


def raid_cc_vnum(vd_arg, vds):
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
