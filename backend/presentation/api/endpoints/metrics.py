from backend.infrastructure.collectors.cpuusage import get_cpu_usage
from backend.infrastructure.collectors.gpu import _get_gpu_live
from backend.infrastructure.collectors.metrics import get_realtime_metrics
from backend.infrastructure.persistence.db import _DB_PATH
from backend.infrastructure.persistence.db import _db_lock
from backend.presentation.api.endpoints.helpers import _disks_meta_map
from backend.presentation.api.endpoints.helpers import get_live_mem
import os
import sqlite3 as _sqlite3
import time

# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.infrastructure.gateway import require_admin
from backend.presentation.api.dependencies import _jr, json_body

router = APIRouter()



@router.get("/api/metrics")
def api_metrics():
    """轻量实时指标：网络吞吐 + 磁盘 I/O + CPU/内存/负载。供前端高频(1s)轮询，不触发重型 /api/all(阵列卡/SMART等)。
    网络速率会把 OVS 桥（如 eno1-ovs）归并到物理口名（eno1），与 /api/system 的合并显示名对齐，
    这样前端按 data-nic 直接覆盖即可。"""
    try:
        rt = get_realtime_metrics()
        net = rt.get("net", [])
        merged = {}
        for e in net:
            name = e.get("name", "")
            if name.endswith("-ovs"):
                base = name[:-4]
                merged[base] = {"name": base,
                                "rx_rate": e.get("rx_rate", 0.0),
                                "tx_rate": e.get("tx_rate", 0.0)}
            elif name not in merged:
                merged[name] = {"name": name,
                                "rx_rate": e.get("rx_rate", 0.0),
                                "tx_rate": e.get("tx_rate", 0.0)}
        diskio = rt["disk"]
        try:
            disk_map = _disks_meta_map()
        except Exception:
            disk_map = {}
        for d in diskio:
            info = disk_map.get(d["device"], {})
            d["model"] = info.get("model", "")
            d["size"] = info.get("size", "")
            d["brand"] = info.get("brand", "")
            d["type"] = info.get("type", "")
        # 实时 CPU / 内存 / 负载（无缓存，供前端 1s 增量刷新）。
        # CPU 走 get_cpu_usage 的 1s 窗口缓存（daemon 每 1s 刷新；异常时就地 1s 采样兜底），
        # 采样窗口恒为 ~1s，与 fnOS 口径一致，不再有毫秒级窗口导致的瞬时 100%/50% 尖刺。
        try:
            cpu_usage = get_cpu_usage()
        except Exception:
            cpu_usage = None
        try:
            mem_percent = get_live_mem()
        except Exception:
            mem_percent = None
        try:
            load = list(os.getloadavg())
        except Exception:
            load = None
        # CPU 当前频率（全部核心瞬时值的平均，口径同 /api/system 的 cpu_info.current_freq_mhz）。
        # /proc/cpuinfo 是内核内存里的虚拟文件，每秒读一次开销极小（几 KB），供检测页频率行 1s 增量刷新。
        try:
            with open("/proc/cpuinfo") as _f:
                _mhz = [float(_ln.split(":", 1)[1]) for _ln in _f if _ln[:7].lower() == "cpu mhz" and ":" in _ln]
            cpu_freq_mhz = round(sum(_mhz) / len(_mhz), 1) if _mhz else None
        except Exception:
            cpu_freq_mhz = None
        return _jr({"net": list(merged.values()), "diskio": diskio,
                        "cpu_usage": cpu_usage, "mem_percent": mem_percent, "load": load,
                        "cpu_freq_mhz": cpu_freq_mhz,
                        "gpu": _get_gpu_live(),
                        "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception as e:
        return _jr({"error": str(e), "net": [], "diskio": []})

@router.get("/api/history")
def api_history(request: Request):
    """返回历史趋势降采样点（磁盘读/写速率），按 range 时间桶聚合，省流量。"""
    try:
        rng = request.query_params.get("range", "24h")
        secs = {"24h": 86400, "7d": 7*86400, "30d": 30*86400}.get(rng, 86400)
        end = int(time.time()); start = end - secs
        bucket = max(60, secs // 240)
        with _db_lock:
            con = _sqlite3.connect(_DB_PATH)
            rows = con.execute(
                "SELECT (ts/?)*?*1000 AS bts, AVG(disk_read), AVG(disk_write), AVG(net_rx), AVG(net_tx), AVG(cpu_power), "
                "AVG(cpu_temp), AVG(mb_temp), AVG(gpu_temp), AVG(disk_temp_max), AVG(raid_temp), "
                "AVG(fan_rpm_avg), AVG(mem_used_pct) "
                "FROM samples WHERE ts>=? GROUP BY bts ORDER BY bts",
                (bucket, bucket, start)).fetchall()
            con.close()
        def _v(row, idx, digits=1):
            v = row[idx]
            if v is None:
                return None
            return round(v, digits)
        points = [{"ts": r[0], "disk_read": _v(r, 1), "disk_write": _v(r, 2),
                   "net_rx": _v(r, 3), "net_tx": _v(r, 4),
                   "cpu_power": _v(r, 5),
                   "cpu_temp": _v(r, 6), "mb_temp": _v(r, 7),
                   "gpu_temp": _v(r, 8), "disk_temp_max": _v(r, 9),
                   "raid_temp": _v(r, 10), "fan_rpm_avg": _v(r, 11, 0),
                   "mem_used_pct": _v(r, 12)} for r in rows]
        return _jr({"range": rng, "points": points, "bucket_s": bucket})
    except Exception as e:
        return _jr({"error": str(e), "points": []})