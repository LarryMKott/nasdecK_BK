# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.collectors.disks import get_disks
import threading as _threading
import time


# ===================== 按板块独立接口（切换导航只拉当前板块，避免全量 /api/all）=====================
def _panel_time():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def get_live_mem():
    """实时内存占用率(%):读 /proc/meminfo 用 MemAvailable 估算(无 psutil 依赖)。"""
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if ':' in line:
                    k, v = line.split(':', 1)
                    mem[k.strip()] = int(v.split()[0])  # KB
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", 0)
        if total <= 0:
            return None
        return round((1 - avail / total) * 100, 1)
    except Exception:
        return None


# 磁盘静态元数据缓存：/api/metrics 被前端每 1s 轮询，而 get_disks() 对每块盘跑 smartctl
# （每块 20s 超时）只为取 model/size/brand/type 这类几乎不变的静态信息。
# 无缓存时每秒全量扫盘（与 /api/fan/temps 同源风暴），把磁盘 I/O 打满并拖慢接口（实测 ~2s）。
# 元数据 5 分钟不变，TTL 300s；health/temp 等动态字段由磁盘页各自实时读取，不受影响。
# 加锁防并发双扫：过期时非阻塞抢锁，抢不到就返回旧值，绝不排队等待。
_DISKS_META_CACHE = {"t": 0.0, "v": None}
_DISKS_META_TTL = 300
_DISKS_META_LOCK = _threading.Lock()

def _disks_meta_map():
    now = time.time()
    c = _DISKS_META_CACHE
    if now - c["t"] <= _DISKS_META_TTL and c["v"] is not None:
        return c["v"]
    if _DISKS_META_LOCK.acquire(blocking=False):
        try:
            if now - c["t"] > _DISKS_META_TTL:
                m = {}
                for d in get_disks():
                    m[d.get("dev")] = d
                c["v"] = m
                c["t"] = time.time()
        except Exception:
            pass
        finally:
            _DISKS_META_LOCK.release()
    return c["v"] or {}
