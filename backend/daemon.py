# -*- coding: utf-8 -*-
"""后台常驻线程唯一启动点（收敛自 app.py 6 处 import 期线程启动）。

【迁移差异】原代码在 import 期即启动全部后台线程（L1430/6993/6997/7576/7579/7927），
任何 import 方式都会拉起线程并写风扇 sysfs；现统一为 start_background_workers()，
由 serve.main() 启动序列显式调用。线程清单与启动顺序与原版一致：

  1. fan-smooth    风扇平滑 PWM 循环（fans.control）
  2. metrics       实时指标采样 + 历史写入驱动 + CC 调度驱动（本模块主循环）
  3. temp-snap     统一温度快照（collectors.cputemp）
  4. cache-warmup  首屏采集预热
  5. wol-reapply   开机回放网口 WOL 开关（collectors.wol）
  6. disk-health   硬盘健康趋势（health.diskhealth，6h 一轮）
"""
import threading
import time

from backend import schedule
from backend.collectors import metrics as _metrics_mod
from backend.collectors.board import get_board, get_memory_modules
from backend.collectors.cpuusage import _cpu_snap, get_cpu_usage
from backend.collectors.cputemp import _temp_collect_loop
from backend.collectors.disks import get_disks
from backend.collectors.dockermod import get_docker
from backend.collectors.metrics import (_CPU_POWER_EMA, _DISK_LAST_IO,
                                        _DISK_STANDBY_IDLE_MIN, _METRICS_LOCK,
                                        _metrics_cur, _metrics_prev,
                                        _read_disk_stats, _read_net_bytes,
                                        _read_rapl_energy)
from backend.collectors.raid import get_raid_card
from backend.collectors.storage import get_storage
from backend.collectors.system import get_system
from backend.collectors.wol import _wol_reapply_on_start
from backend.fans.control import _fan_smooth_runner
from backend.health.diskhealth import disk_health_loop
from backend.history import db as _db
from backend.schedule import _check_cc_schedule, _cc_sched_last_check
from backend.state import _CPU_USAGE_CACHE, _CPU_USAGE_LOCK

# metrics 主循环逐 tick 使用的可变归属状态说明：
# - _CPU_POWER_EMA / _cc_sched_last_check / _db_last_write 原为 app.py 模块级 global，
#   且被 metrics 循环跨区重赋值；拆分后分别归属 collectors.metrics / schedule /
#   history.db，本循环一律用「模块属性写」回写（_mod.X = ...），语义与 global 等价。


def metrics_collect_loop():
    """daemon 线程：每 ~1s 采样一次，计算速率/功率/CPU 使用率并写入 _metrics_cur。
    采样窗口固定（net/disk 用真实间隔 dt，CPU 用相邻两次迭代差），不随请求并发抖动，
    因此 /api/metrics 高频拉取也不会把 CPU 窗口压成毫秒级而出现瞬时 100%/50% 尖刺。"""
    while True:
        try:
            t_now = time.time()
            now = int(t_now)
            with _METRICS_LOCK:
                dt = max(0.5, t_now - _metrics_prev["t"])
            # 历史趋势：每 30s 聚合写一行 SQLite（免维护，超 30 天自动清理）
            if now - _db._db_last_write >= 30:
                _db._write_history_sample()
            # 一致性检查定时调度：每 60s 在后台线程检查是否到点触发 CC（无 VD 时静默跳过）
            if now - schedule._cc_sched_last_check >= 60:
                schedule._cc_sched_last_check = now
                threading.Thread(target=_check_cc_schedule, daemon=True).start()
            # 网络吞吐
            net_now = _read_net_bytes()
            net_out = []
            with _METRICS_LOCK:
                prev = _metrics_prev["net"]
                for iface, (rx, tx) in net_now.items():
                    prx, ptx = prev.get(iface, (rx, tx))
                    rx_rate = max(0.0, (rx - prx) / dt)
                    tx_rate = max(0.0, (tx - ptx) / dt)
                    net_out.append({
                        "name": iface,
                        "rx_rate": round(rx_rate, 1),   # bytes/s，前端动态格式化为 B/s/KB/s/MB/s
                        "tx_rate": round(tx_rate, 1),
                        "rx_total_mb": round(rx / 1048576, 1),
                        "tx_total_mb": round(tx / 1048576, 1),
                    })
                _metrics_prev["net"] = net_now
                _metrics_cur["net"] = net_out
            # 磁盘 I/O
            disk_now = _read_disk_stats()
            disk_out = []
            with _METRICS_LOCK:
                prev = _metrics_prev["disk"]
                for dev, (rd, wr, iot) in disk_now.items():
                    prd, pwr, piot = prev.get(dev, (rd, wr, iot))
                    rd_rate = max(0.0, (rd - prd) * 512 / dt)
                    wr_rate = max(0.0, (wr - pwr) * 512 / dt)
                    # busy%：io_ticks 两次采样差 / 间隔毫秒(dt*1000)，钳到 [0,100]
                    busy = 0.0
                    if iot >= piot and dt > 0:
                        busy = max(0.0, min(100.0, (iot - piot) / (dt * 1000) * 100))
                    # standby：连续无 I/O（扇区计数未变）满阈值即视为待机，不唤醒盘
                    if rd != prd or wr != pwr:
                        _DISK_LAST_IO[dev] = now
                        standby = False
                    else:
                        last = _DISK_LAST_IO.get(dev)
                        standby = last is not None and (now - last) >= _DISK_STANDBY_IDLE_MIN * 60
                    disk_out.append({
                        "device": dev,
                        "read_rate": round(rd_rate, 1),   # bytes/s，前端动态格式化为 B/s/KB/s/MB/s
                        "write_rate": round(wr_rate, 1),
                        "busy": round(busy, 1),           # 占用率 %，对齐飞牛 disk[].busy
                        "standby": bool(standby),         # 待机标志，对齐飞牛 disk[].standby（diskstats 轻量代理）
                    })
                _metrics_prev["disk"] = disk_now
                _metrics_cur["disk"] = disk_out
            # CPU 使用率：固定窗口（两次迭代差，≈1s）采样，写 _CPU_USAGE_CACHE，供 /api/metrics 与 /api/system 直接读取。
            # 不再由请求各自读 /proc/stat 改共享快照——那样并发请求会把窗口压到毫秒级，产生瞬时 100%/50% 尖刺。
            try:
                idle, total = _cpu_snap()
                with _CPU_USAGE_LOCK:
                    pc = _CPU_USAGE_CACHE.get("prev")
                    if pc is not None:
                        d_total = total - pc[0]
                        d_idle = idle - pc[1]
                        if d_total > 0:
                            v = max(0.0, min(100.0, round((1.0 - d_idle / d_total) * 100, 1)))
                            _CPU_USAGE_CACHE["v"] = v
                            _CPU_USAGE_CACHE["t"] = time.time()
                    _CPU_USAGE_CACHE["prev"] = (total, idle)
            except Exception:
                pass
            # CPU 封装功耗 (RAPL)：两次采样差算功率 + EMA 平滑
            e = _read_rapl_energy()
            with _METRICS_LOCK:
                pe = _metrics_prev["rapl"]; pt = _metrics_prev["rapl_t"]
                if e is not None and pe is not None and pt:
                    dt = now - pt
                    if dt > 0:
                        w = (e - pe) / 1e6 / dt
                        if 0 < w < 1000:   # 合理性过滤（微焦回绕/异常）
                            ema = _metrics_mod._CPU_POWER_EMA
                            ema = w if ema is None else (ema * 0.8 + w * 0.2)
                            _metrics_mod._CPU_POWER_EMA = ema
                            _metrics_cur["cpu_power_w"] = round(ema, 2)
                            _metrics_cur["cpu_power_valid"] = True
                _metrics_prev["rapl"] = e
                _metrics_prev["rapl_t"] = now
            # 记录本轮回合时间，供下一轮计算真实间隔 dt（net/disk 速率用）
            with _METRICS_LOCK:
                _metrics_prev["t"] = t_now
        except Exception:
            time.sleep(2)
        time.sleep(1)


def _warmup_caches():
    """启动预热：首屏涉及的采集函数先各跑一遍，避免首屏 /api/all 空数据。
    （自 app.py L7562 原样搬迁）"""
    for fn in (get_system, get_board, get_memory_modules,
               get_raid_card, get_disks, get_storage, get_docker):
        try:
            fn()
        except Exception:
            pass
    # 预热 CPU 使用率基准，避免首屏 /api/all 首次 get_cpu_usage 返回 None。
    # 直接走 get_cpu_usage()：首次会做 1s 窗口采样并写进 _CPU_USAGE_CACHE，同时给 daemon 留好 prev 基准。
    try:
        get_cpu_usage()
    except Exception:
        pass


def start_background_workers():
    """启动全部 6 条常驻线程（幂等：重复调用不会重复拉起）。"""
    if getattr(start_background_workers, "_started", False):
        return
    start_background_workers._started = True
    # 1. 风扇平滑循环（启动前须已完成 _restore_fan_modes，见 serve.main 序列）
    threading.Thread(target=_fan_smooth_runner, daemon=True, name="fan-smooth").start()
    # 2. metrics 主循环
    threading.Thread(target=metrics_collect_loop, daemon=True, name="metrics").start()
    # 3. 统一温度采集循环（~2s）：一次 sensors -j → _TEMP_SNAP 快照，所有温度消费方共享。
    threading.Thread(target=_temp_collect_loop, daemon=True, name="temp-snap").start()
    # 4. 首屏预热
    threading.Thread(target=_warmup_caches, daemon=True, name="cache-warmup").start()
    # 5. 开机回放用户设过的 WOL 开关（放在预热线程之后，避免抢占首屏）
    threading.Thread(target=_wol_reapply_on_start, daemon=True, name="wol-reapply").start()
    # 6. 硬盘健康趋势（6h 一轮）
    threading.Thread(target=disk_health_loop, daemon=True, name="disk-health").start()
