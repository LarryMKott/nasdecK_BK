"""disk_rule 单测：输入合法性/盘名清洗纯规则（忠实副本契约，含换线前后等价锚点）。"""
from backend.domain.rules.disk_rule import DISK_NAME_MAX, disk_name_clean, safe_token


# ---------- safe_token（原 cmd_runner._safe_token） ----------

def test_safe_token_accepts_common_tokens():
    for ok in ("sda", "nvme0n1", "hwmon2", "homebox", "eno1-ovs", "a.b_c@d:e/f", "0"):
        assert safe_token(ok) == ok


def test_safe_token_rejects_bad_input():
    assert safe_token("") is None
    assert safe_token(None) is None
    assert safe_token(123) is None
    assert safe_token("sda; rm -rf") is None          # 命令注入
    assert safe_token("sda$(reboot)") is None         # 命令替换
    assert safe_token("盘名") is None                  # 非白名单字符
    assert safe_token("x" * 129) is None              # 超长
    assert safe_token("x" * 128) == "x" * 128         # 恰好上限放行


def test_safe_token_allows_relative_path_chars():
    # 白名单本就含 . / : @ -（hwmon 路径/网卡名需要），路径穿越防护由调用方的设备名枚举兜底
    assert safe_token("../../etc") == "../../etc"
    assert safe_token("eno1-ovs") == "eno1-ovs"


def test_safe_token_maxlen_override():
    assert safe_token("x" * 200, maxlen=256) == "x" * 200


# ---------- disk_name_clean（原 diskhealth._disk_name_clean） ----------

def test_disk_name_clean_strips_control_and_whitespace():
    assert disk_name_clean(" 数据盘1 ") == "数据盘1"
    assert disk_name_clean("数\x01据\x1f[1m盘") == "数据[1m盘"  # 控制字符剔除，可见字符保留
    assert disk_name_clean(None) == ""                # None = 清除语义


def test_disk_name_clean_truncates_to_max():
    long = "盘" * (DISK_NAME_MAX + 10)
    assert len(disk_name_clean(long)) == DISK_NAME_MAX == 40
