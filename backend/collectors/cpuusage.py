# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.shell import read_file
from backend.state import _CPU_USAGE_CACHE
from backend.state import _CPU_USAGE_LOCK
import time


def _cpu_snap():
    """读 /proc/stat 首行聚合，返回 (idle, total)。idle 含 iowait，total 含 user..steal。"""
    parts = read_file("/proc/stat", "").split('\n', 1)[0].split()
    cols = list(map(int, parts[1:]))
    idle = cols[3] + cols[4]          # idle + iowait
    total = sum(cols[:8])             # user..steal（不含 guest，已计入 user）
    return idle, total

def _cpu_sample_1s():
    """兜底采样：真实睡眠 1s 的两次 /proc/stat 读数，算 1s 窗口使用率。返回 0-100 浮点或 None。"""
    try:
        i1, t1 = _cpu_snap()
        time.sleep(1.0)
        i2, t2 = _cpu_snap()
        d_total = t2 - t1
        if d_total <= 0:
            v = 0.0
        else:
            v = (1.0 - (i2 - i1) / d_total) * 100.0
        v = max(0.0, min(100.0, round(v, 1)))
        with _CPU_USAGE_LOCK:
            _CPU_USAGE_CACHE["v"] = v
            _CPU_USAGE_CACHE["t"] = time.time()
        return v
    except Exception:
        return None

def get_cpu_usage():
    """真实 CPU 使用率（%）：优先返回后台 daemon 每 ~1s 刷新的缓存值（低延迟）；
    若缓存 >2s 未更新（daemon 异常）则就地 1s 窗口采样兜底。永不返回毫秒级窗口的尖刺值。"""
    try:
        now = time.time()
        with _CPU_USAGE_LOCK:
            c = dict(_CPU_USAGE_CACHE)
        if c["v"] is not None and (now - c["t"]) < 2.0:
            return c["v"]
        return _cpu_sample_1s()
    except Exception:
        return None
