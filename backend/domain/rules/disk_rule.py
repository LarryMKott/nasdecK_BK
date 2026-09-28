"""磁盘运维输入规则（纯字符串判定，无 IO）。

忠实副本来源（v2.4 阶段 3 批 A 第三单换线，原副本改委托本模块）：
  safe_token       ← backend/infrastructure/command/cmd_runner.py::_safe_token
  disk_name_clean  ← backend/infrastructure/health/diskhealth.py::_disk_name_clean
忠实性约束：正则/截断/默认值逐行等价（含文案），仅归属层变化。
"""
import re

# 自定义盘名长度上限（diskhealth 同源）
DISK_NAME_MAX = 40


def safe_token(s, maxlen=128):
    """校验系统枚举值（设备名/hwmon 路径/容器名等）只含安全字符，防路径/命令注入。
    返回原值或 None（非法）。"""
    if not isinstance(s, str) or not s or len(s) > maxlen:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_./@:-]+", s):
        return None
    return s


def disk_name_clean(name):
    """清洗自定义盘名：去掉控制字符与首尾空白、超长截断；返回空串表示「清除」。"""
    if name is None:
        return ""
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name))
    return name.strip()[:DISK_NAME_MAX]
