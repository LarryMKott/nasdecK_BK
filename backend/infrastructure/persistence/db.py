# -*- coding: utf-8 -*-
"""历史趋势存储（自 app.py L6752-6888 搬迁；sqlite3 → SQLAlchemy 2.x Core 重写）。

【迁移差异】原实现直接用 sqlite3 驱动；现按硬性规范改为 SQLAlchemy 2.x Core
（Engine + Core Table，保持轻量、不过度封装）。**表结构与列顺序与旧 history.db
完全一致**（ts 主键 + 13 数据列 + fan_detail），旧库无缝续用：
- 新库：create_all 一次建全列（等价原 CREATE + 11 次 ALTER 的最终形态）
- 旧库缺列：inspector 检出后逐列 ALTER 补齐（保留原「缺列则补」兼容逻辑）

线程模型不变：由 daemon 的 metrics 主循环每 30s 调 _write_history_sample()；
_db_lock 互斥；30 天自清理。
"""
import json
import os
import re
import sqlite3 as _sqlite3
import threading as _threading
import time

from sqlalchemy import (Column, Float, Integer, MetaData, Table, Text,
                        bindparam, create_engine, delete, inspect, select,
                        update)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.infrastructure.collectors.gpu import _get_gpu_live
from backend.infrastructure.collectors.metrics import _METRICS_LOCK, _metrics_cur
from backend.infrastructure.fans.control import get_fan_status
from backend.infrastructure.config.paths import _config_dir
from backend.infrastructure.command.cmd_runner import read_file
from backend.infrastructure.state import _TEMP_SNAP

_DB_PATH = os.path.join(_config_dir(), "history.db")

# SQLAlchemy 2.x Core 表定义：与旧 history.db 的 samples 表逐列一致（结构冻结）
_metadata = MetaData()
samples = Table(
    "samples", _metadata,
    Column("ts", Integer, primary_key=True),
    Column("disk_read", Float), Column("disk_write", Float),
    Column("net_rx", Float), Column("net_tx", Float), Column("cpu_power", Float),
    Column("cpu_temp", Float), Column("mb_temp", Float), Column("gpu_temp", Float),
    Column("disk_temp_max", Float), Column("raid_temp", Float),
    Column("fan_rpm_avg", Float), Column("mem_used_pct", Float),
    Column("fan_detail", Text),
)

engine = create_engine(f"sqlite:///{_DB_PATH}")

_db_lock = _threading.Lock()
_db_last_write = 0.0

# 兼容旧库的补列清单（与原 ALTER 循环一致；fan_detail 单独补 TEXT）
_LEGACY_COLS = ("net_rx", "net_tx", "cpu_power",
                "cpu_temp", "mb_temp", "gpu_temp",
                "disk_temp_max", "raid_temp",
                "fan_rpm_avg", "mem_used_pct")


def _init_history_db():
    """建库 + 旧库补列。由 serve.main() 启动序列显式调用（原为 import 期执行）。"""
    try:
        with _db_lock:
            _metadata.create_all(engine)  # CREATE TABLE IF NOT EXISTS（新库直建全列）
            existing = {c["name"] for c in inspect(engine).get_columns("samples")}
            with engine.begin() as con:
                # 兼容旧库：缺列则补，避免 INSERT 报「no column」被吞、历史停止写入
                for col in _LEGACY_COLS:
                    if col not in existing:
                        con.exec_driver_sql(f"ALTER TABLE samples ADD COLUMN {col} REAL")
                if "fan_detail" not in existing:
                    con.exec_driver_sql("ALTER TABLE samples ADD COLUMN fan_detail TEXT")
    except Exception:
        pass


def _write_history_sample():
    """把当前实时指标聚合一行写入 SQLite；并删除 30 天前样本（自清理）。"""
    global _db_last_write
    try:
        now = int(time.time())
        with _METRICS_LOCK:
            disk = _metrics_cur["disk"]; net = _metrics_cur["net"]
            cpu = _metrics_cur.get("cpu_power_w", 0.0)
        dr = sum((d.get("read_rate") or 0) for d in disk)
        dw = sum((d.get("write_rate") or 0) for d in disk)
        nr = sum((n.get("rx_rate") or 0) for n in net)
        nw = sum((n.get("tx_rate") or 0) for n in net)
        # 温度/风扇/内存快照（复用已有采样结果，避免每次开 shell）
        ts = _TEMP_SNAP
        cpu_temp = ts.get("cpu_temp")
        mb_temp = ts.get("mb_temp")
        raid_temp = ts.get("raid_temp")
        disk_states = ts.get("disks") or {}
        disk_temps = [v.get("temp") for v in disk_states.values()
                      if isinstance(v, dict) and isinstance(v.get("temp"), (int, float))]
        disk_temp_max = max(disk_temps) if disk_temps else None
        try:
            gpus = _get_gpu_live()
            gpu_temp = next((g.get("temp") for g in gpus if isinstance(g.get("temp"), (int, float))), None)
        except Exception:
            gpu_temp = None
        try:
            fans = get_fan_status()
            rpms = [f.get("rpm") for f in fans if isinstance(f.get("rpm"), (int, float))]
            fan_rpm_avg = round(sum(rpms) / len(rpms), 0) if rpms else None
        except Exception:
            fans = []
            fan_rpm_avg = None
        try:
            # 每通道转速 + 占空比 + 接管状态(pwm_enable: 1=软件控 2=交还主板) 快照，
            # 供「风扇问题排查」复盘：哪台扇何时被谁接管/抢走。
            fan_detail = json.dumps(
                [{"n": f.get("name"), "idx": f.get("idx"), "rpm": f.get("rpm"), "pwm": f.get("pwm"),
                  "pe": int(f.get("pwm_enable")) if f.get("pwm_enable") is not None else None} for f in fans],
                ensure_ascii=False)
        except Exception:
            fan_detail = None
        try:
            meminfo = read_file("/proc/meminfo")
            mi = {}
            for line in meminfo.splitlines():
                m = re.match(r"(\w+):\s+(\d+)", line)
                if m:
                    mi[m.group(1)] = int(m.group(2))
            mt = mi.get("MemTotal", 0); ma = mi.get("MemAvailable", 0)
            mem_used_pct = round((mt - ma) / mt * 100, 1) if mt else None
        except Exception:
            mem_used_pct = None
        with _db_lock:
            with engine.begin() as con:
                # INSERT OR REPLACE：ts 主键冲突即整行覆盖，与原 sqlite3 语义一致
                stmt = sqlite_insert(samples).prefix_with("OR REPLACE").values(
                    ts=now, disk_read=dr, disk_write=dw, net_rx=nr, net_tx=nw,
                    cpu_power=cpu, cpu_temp=cpu_temp, mb_temp=mb_temp, gpu_temp=gpu_temp,
                    disk_temp_max=disk_temp_max, raid_temp=raid_temp,
                    fan_rpm_avg=fan_rpm_avg, mem_used_pct=mem_used_pct, fan_detail=fan_detail)
                con.execute(stmt)
                con.execute(delete(samples).where(samples.c.ts < now - 30 * 86400))
        _db_last_write = time.time()
    except Exception:
        pass


def _rename_history_fan(idx, new_name, old_name=None):
    """把历史库里所有属于该 idx 的采样点名字改为 new_name。

    改名必须连带历史：趋势图按名字分组,若不回填旧样本,改名后旧样本(旧名)+新样本(新名)
    会被拆成两条通道,旧名残留。这里把整库该 idx 的 fan_detail.n 全部改写为新名,
    既保证趋势图无残留,又保留全部历史(调试查 bug 用),只是名字跟着更新。
    old_name 用于兜底匹配「idx 字段上线前的旧样本」(无 idx 但有旧名)。
    """
    try:
        with engine.connect() as con:
            rows = con.execute(
                select(samples.c.ts, samples.c.fan_detail).where(
                    samples.c.fan_detail.is_not(None))).fetchall()
        upd = []
        for ts, detail in rows:
            try:
                fl = json.loads(detail) if detail else []
            except Exception:
                continue
            if not isinstance(fl, list):
                continue
            changed = False
            for f in fl:
                if not isinstance(f, dict):
                    continue
                if f.get("idx") == idx or (old_name is not None and f.get("n") == old_name):
                    if f.get("n") != new_name:
                        f["n"] = new_name
                        changed = True
            if changed:
                upd.append({"ts": ts, "fan_detail": json.dumps(fl, ensure_ascii=False)})
        if upd:
            with engine.begin() as con:
                # executemany：bindparam 命名参数，逐行绑定（等价原 con.executemany）
                stmt = update(samples).where(samples.c.ts == bindparam("_p_ts")).values(
                    fan_detail=bindparam("_p_detail"))
                con.execute(stmt, upd)
    except Exception as e:
        print("[history] rename fan failed idx=%s: %s" % (idx, e), flush=True)


def _query_metric_points(start, bucket):
    """按时间桶聚合的指标降采样查询（/api/history 查询核，SQL 自端点逐字搬迁）。

    返回行元组列表（bts 毫秒时间戳 + 12 个均值列，NULL 保留，bts 升序）；
    读取走 sqlite3 直连 + _db_lock，与写路径互斥语义不变。
    """
    with _db_lock:
        con = _sqlite3.connect(_DB_PATH)
        rows = con.execute(
            "SELECT (ts/?)*?*1000 AS bts, AVG(disk_read), AVG(disk_write), AVG(net_rx), AVG(net_tx), AVG(cpu_power), "
            "AVG(cpu_temp), AVG(mb_temp), AVG(gpu_temp), AVG(disk_temp_max), AVG(raid_temp), "
            "AVG(fan_rpm_avg), AVG(mem_used_pct) "
            "FROM samples WHERE ts>=? GROUP BY bts ORDER BY bts",
            (bucket, bucket, start)).fetchall()
        con.close()
    return rows
