# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
import os
import threading as _threading
import time


# ===================== 采集层：实时指标（网络吞吐 / 磁盘 I/O / CPU 功耗） =====================
# 这些指标需「两次采样差」才算速率，故由常驻 daemon 线程周期采样，/api/all 仅读最新值。
# 模式复用 fan_smooth_loop 的 daemon 线程做法。
_METRICS_LOCK = _threading.Lock()
_metrics_prev = {"net": {}, "disk": {}, "rapl": None, "rapl_t": 0.0, "cpu": None, "t": time.time()}
_metrics_cur = {"net": [], "disk": [], "cpu_usage": None, "cpu_power_w": 0.0, "cpu_power_valid": False}
_CPU_POWER_EMA = None

def _read_net_bytes():
    """返回 {iface: (rx_bytes, tx_bytes)}，过滤回环/虚拟接口"""
    res = {}
    try:
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]
        for line in lines:
            if ":" not in line:
                continue
            name, data = line.split(":", 1)
            name = name.strip()
            if name == "lo" or name.startswith(("docker", "br-", "veth")):
                continue
            parts = data.split()
            if len(parts) < 9:
                continue
            res[name] = (int(parts[0]), int(parts[8]))
    except Exception:
        pass
    return res

def _read_disk_stats():
    """返回 {dev: (rd_sectors, wr_sectors, io_ticks)}，只保留**物理整盘**。
    io_ticks = /proc/diskstats 第13列：设备累计花在 I/O 上的毫秒数；
    两次采样差 / 采样间隔毫秒 = 该盘 busy 占用率(%)。

    过滤规则（2026-09-11 修正，见论坛反馈③）：
    - loop/ram/sr/zram/nbd/rbd：回环、内存盘、光驱、网络块设备，非物理硬盘。
    - md*/dm-*/drbd*：软 RAID / device-mapper / DRBD 等**虚拟聚合设备**——
      它们的 I/O 由下面的物理成员盘累加而来，一并显示会重复计数。
    - 分区：以 /sys/class/block/<dev>/partition 是否存在判定。
      ⚠️ 旧写法用「名字末位是数字就跳过」，本意只跳过分区（sda1），
      却把 nvme0n1 / mmcblk0 / md0 / dm-0 这类**名字以数字结尾的整盘**也一并误杀，
      导致 NVMe 系统盘永远不出现在「磁盘 I/O」里。
    """
    res = {}
    _VIRTUAL = ("loop", "ram", "sr", "zram", "md", "dm-", "drbd", "nbd", "rbd")
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                cols = line.split()
                if len(cols) < 13:
                    continue
                dev = cols[2]
                if dev.startswith(_VIRTUAL):
                    continue
                # 真分区判定（sda1 / nvme0n1p1 / mmcblk0p1 都命中，整盘不命中）
                if os.path.exists("/sys/class/block/%s/partition" % dev):
                    continue
                res[dev] = (int(cols[5]), int(cols[9]), int(cols[12]))
    except Exception:
        pass
    return res

# 每块盘最近一次有 I/O 的时刻，用于派生 standby（连续无 I/O 满阈值即视为待机）。
# 与调速线程判定口径一致：基于 /proc/diskstats 扇区差，轻量且不唤醒休眠盘。
_DISK_LAST_IO = {}
_DISK_STANDBY_IDLE_MIN = 5   # 分钟；与风扇休眠 idle_minutes 默认值对齐

def _read_rapl_energy():
    """读 CPU 封装能耗(微焦)，root 可读；返回 energy_uj 或 None。admin 无权限→None。"""
    base = "/sys/class/powercap/intel-rapl/intel-rapl:0"
    try:
        return int(open(base + "/energy_uj").read().strip())
    except Exception:
        return None

def get_realtime_metrics():
    with _METRICS_LOCK:
        return {
            "net": _metrics_cur["net"],
            "disk": _metrics_cur["disk"],
            "cpu_power_w": _metrics_cur["cpu_power_w"],
            "cpu_power_valid": _metrics_cur["cpu_power_valid"],
        }
