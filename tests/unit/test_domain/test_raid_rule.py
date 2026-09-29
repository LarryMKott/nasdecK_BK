# -*- coding: utf-8 -*-
"""raid_cc_vnum 纯规则单测（阶段 3 批 B 自 schedule.py 收敛 domain）。"""
from backend.domain.rules.raid_rule import raid_cc_vnum

_VDS = [{"dgvd": "0/0"}, {"dgvd": "1/1"}]


def test_none_and_non_digit_rejected():
    assert raid_cc_vnum(None, _VDS) is None
    assert raid_cc_vnum("abc", _VDS) is None
    assert raid_cc_vnum("0/x", _VDS) is None


def test_plain_index_in_range():
    assert raid_cc_vnum("0", _VDS) == "0"
    assert raid_cc_vnum("1", _VDS) == "1"


def test_dgvd_form_uses_vnum_part():
    assert raid_cc_vnum("0/0", _VDS) == "0"
    assert raid_cc_vnum("1/1", _VDS) == "1"


def test_out_of_range_resolves_via_dgvd_whitelist():
    # 越界纯数字：仅当与某 VD 的 dgvd 尾号匹配时放行
    assert raid_cc_vnum("5", [{"dgvd": "0/5"}]) == "5"
    assert raid_cc_vnum("5", _VDS) is None


def test_whitespace_and_empty_vds():
    assert raid_cc_vnum(" 0 ", _VDS) == "0"
    assert raid_cc_vnum("0", []) is None
