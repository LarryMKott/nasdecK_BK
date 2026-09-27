# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.paths import APP_DIR
import os
import re
import shutil
import subprocess
import time


# 命令全路径（admin 的 PATH 不含 /usr/sbin）
def _find_storcli():
    """动态探测 storcli 二进制：兼容只装了 storcli64 的环境（部分 fnOS 用户机器只有 storcli64）"""
    candidates = [
        "/usr/local/bin/storcli64",
        "/usr/local/bin/storcli",
        "/opt/MegaRAID/storcli/storcli64",
        "/opt/MegaRAID/storcli/storcli",
        "/usr/sbin/storcli64",
        "/usr/sbin/storcli",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return shutil.which("storcli64") or shutil.which("storcli") or ""

STORCLI = _find_storcli()
SMARTCTL = "/usr/sbin/smartctl"
BADBLOCKS = "/usr/sbin/badblocks"
SENSORS = "/usr/bin/sensors"
DMIDECODE = "/usr/sbin/dmidecode"

# ---------- 基础执行 ----------
def log(msg):
    """轻量日志：追加到应用目录 debug.log，便于排查静默失败（如 storcli 命令执行失败）"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(APP_DIR, "debug.log"), "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

# ===================== 安全执行封装（shell=False，杜绝命令注入）=====================
# 原 run()/sudo() 用 shell=True + 字符串拼命令，一旦拼接系统枚举值即存在注入面。
# 现统一改用 run_cmd/sudo_cmd（参数列表 + shell=False）；读文件直接 open() 不用 shell。
def read_file(path, default=""):
    """安全读取文件内容（替代 run('cat ...')）。路径须为受控系统路径。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return default

def _safe_token(s, maxlen=128):
    """校验系统枚举值（设备名/hwmon 路径/容器名等）只含安全字符，防路径/命令注入。
    返回原值或 None（非法）。"""
    if not isinstance(s, str) or not s or len(s) > maxlen:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_./@:-]+", s):
        return None
    return s

def _run_raw(args, timeout=30, as_root=False, quiet=False):
    try:
        args = [str(a) for a in args]
        # 裸命令名按 PATH 解析（还原 shell=True 旧行为：fnOS 默认 PATH 不含 /usr/sbin，
        # 旧代码靠 shell 找 ip/lspci 等；此处仅解析路径，不拼接字符串，无注入面）
        if args and "/" not in args[0]:
            _resolved = shutil.which(args[0])
            if _resolved:
                args[0] = _resolved
        if as_root and os.geteuid() != 0:
            args = ["sudo", "-n"] + args
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if not quiet and r.returncode != 0 and r.stderr.strip():
            log("cmd failed (rc=%d): %s\n%s" % (r.returncode, " ".join(args), r.stderr.strip()))
        return r.stdout
    except Exception as e:
        log("cmd error: %s\n%s" % (" ".join(map(str, args)), e))
        return ""

def run_cmd(args, timeout=30, quiet=False):
    """shell=False 执行（推荐）：args 为参数列表，不接受字符串。"""
    return _run_raw(args, timeout, as_root=False, quiet=quiet)

def sudo_cmd(args, timeout=30, quiet=False):
    """shell=False 执行需 root 的命令（自动 passwordless sudo 兜底）。"""
    return _run_raw(args, timeout, as_root=True, quiet=quiet)
