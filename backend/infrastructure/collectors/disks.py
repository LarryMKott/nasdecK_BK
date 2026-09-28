# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.command import cmd_runner as shell
from backend.infrastructure.collectors.raid import _smart_rpm_by_serial
from backend.infrastructure.collectors.raid import disk_brand_and_feature
from backend.infrastructure.collectors.smart import parse_ata_smart
from backend.infrastructure.collectors.smart import parse_nvme_smart
from backend.infrastructure.collectors.smart import parse_sas_smart
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
from backend.infrastructure.command.cmd_runner import BADBLOCKS
from backend.infrastructure.command.cmd_runner import SMARTCTL
from backend.infrastructure.command.cmd_runner import _safe_token
import glob
import glob as _glob
import json
import os
import re
import signal
import subprocess
import threading as _threading
import time


def _disk_is_ssd(dev):
    """非 NVMe 的固态盘（SATA/SAS SSD）识别：/sys/block/<name>/queue/rotational=0。
    这类盘无机械轴、不会停转休眠，应排除出"全部休眠→停转"判断（否则像 SAS 一样
    把功能堵死）。NVMe 由调用方单独处理，这里只管 sdX 类设备。读 sysfs 失败则保守
    返回 False（按机械盘处理，宁可不排除也不误伤可休眠盘）。"""
    try:
        name = str(dev).replace("/dev/", "")
        with open("/sys/block/%s/queue/rotational" % name) as f:
            return f.read().strip() == "0"
    except Exception:
        return False


def get_disk_temps(devs):
    """读指定硬盘温度。sdX 用 smartctl -n standby（不唤醒休眠盘）；
    NVMe 不支持 -n standby，直接读温度（NVMe 一般不停机休眠）。
    返回 {dev: {"temp":int|None, "asleep":bool|None, "no_sleep":bool}}。
    no_sleep=True：该盘天生不会休眠——SAS 阵列企业盘被厂商为数据安全锁死不停转，
    NVMe 与 SATA/SAS 固态盘(SSD)无机械轴、也不停转休眠。这类盘不参与"全部休眠→停转"
    判断（否则一块 SAS/SSD 盘就永远满足不了 all(asleep)，把整个休眠停转功能堵死）。"""
    states = {}
    for dev in devs or []:
        # is_nvme 单独标记：NVMe 多为 M.2 被动散热（自带马甲、贴主板），机箱风扇气流基本吹不到，
        # 且其正常工作温区与机械盘完全不同（机械盘 44°C 偏热，NVMe 53°C 属正常、70°C 才近降频线）。
        # 故 NVMe 不能与机械盘共用 start_temp 阈值，需在停转兜底与温度源取值处单独换算。
        is_nvme = dev.startswith("/dev/nvme")
        try:
            no_sleep = False
            serial = None
            if is_nvme:
                out = shell.sudo_cmd([SMARTCTL, "-i", "-A", dev], 8)
                asleep = False
                no_sleep = True  # NVMe 不停机休眠
            else:
                # -i 顺带取序列号：温度快照用于与阵列卡物理盘按 SN 去重
                # （实测 fnOS 的 smartctl -A 输出不含 Information 段，必须显式加 -i）
                out = shell.sudo_cmd([SMARTCTL, "-n", "standby", "-i", "-A", dev], 8)
                asleep = False
                if out and "STANDBY" in out.upper():
                    states[dev] = {"dev": dev, "temp": None, "asleep": True,
                                   "no_sleep": False, "is_nvme": False, "serial": serial, "intf": ""}
                    continue
                # SAS 企业盘（阵列卡后）永不休眠：厂商为数据安全锁死。
                # smartctl -A 对 SAS 盘输出 "Current Drive Temperature"（SATA 走属性表
                # Temperature_Celsius），据此识别，无需额外调用；阵列卡后 lsblk TRAN 为空不可用。
                if out:
                    up = out.upper()
                    if ("CURRENT DRIVE TEMPERATURE" in up
                            or "DRIVE TRIP TEMPERATURE" in up
                            or "TRANSPORT PROTOCOL:   SAS" in up):
                        no_sleep = True
                # SATA/SAS 固态盘(SSD)无机械轴、不会停转休眠，同样排除出判断
                if not no_sleep and _disk_is_ssd(dev):
                    no_sleep = True
            if not out:
                states[dev] = {"dev": dev, "temp": None, "asleep": None,
                               "no_sleep": no_sleep, "is_nvme": is_nvme, "serial": serial, "intf": ""}
                continue
            temp = None
            for line in out.splitlines():
                if "Temperature_Celsius" in line or "Airflow_Temperature" in line:
                    m = re.search(r"-\s*(\d+)", line)
                    if m:
                        temp = int(m.group(1))
                        break
                elif "Current Drive Temperature" in line:
                    # SAS 企业盘（阵列卡后）走这条：smartctl -A 输出 "Current Drive Temperature: 34 C"
                    m = re.search(r"Current Drive Temperature:\s*(\d+)", line, re.I)
                    if m:
                        temp = int(m.group(1))
                        break
                elif "Temperature:" in line:
                    m = re.search(r"Temperature:\s*(\d+)", line)
                    if m:
                        t = int(m.group(1))
                        if t > 200:  # NVMe 偶报 Kelvin，转 Celsius
                            t = t - 273
                        temp = t
                        break
            serial = None
            intf = ""
            if out:
                # 大小写不敏感：SAS 盘输出 "Serial number:"、SATA/NVMe 为 "Serial Number:"
                m = re.search(r"Serial\s*Number\s*:\s*(\S+)", out, re.I)
                if m:
                    serial = m.group(1).strip()
                # 接口类型：SAS 盘 -i 段有 "Transport protocol: SAS (SPL-4)"；
                # SATA 盘一般无此行。供温度页给 SAS 盘加小徽标（直通 SAS 机械盘
                # 的 lsblk TRAN 为空，判不了，必须靠这里）。
                m = re.search(r"Transport protocol:\s*([A-Za-z0-9]+)", out)
                if m and m.group(1).upper().startswith("SAS"):
                    intf = "SAS"
            states[dev] = {"dev": dev, "temp": temp, "asleep": asleep,
                           "no_sleep": no_sleep, "is_nvme": is_nvme, "serial": serial, "intf": intf}
        except Exception:
            states[dev] = {"dev": dev, "temp": None, "asleep": None,
                           "no_sleep": False, "is_nvme": is_nvme, "serial": serial, "intf": ""}
    return states


# 磁盘温度短缓存：/api/fan/temps 被前端每 5s 轮询、风扇控速线程每 0.6s tick、
# /api/fan/status 每 1s 轮询都会读盘温，而 get_disk_temps 对每块盘跑 smartctl
# （每块 8s 超时），多盘时单次可能耗时数秒~数十秒（盘休眠/慢盘时尤甚）。
# 无缓存时这些高频调用会叠起海量慢扫描（实测曾每秒十几发 smartctl 打满磁盘 I/O），
# 把后端与网关拖死（表现为整个面板所有接口全部卡住、风扇转速冻结、温度卡片空转）。
# 因此统一走本缓存：TTL 4s 合并所有调用为同一次扫描；扫描进行中时调用方直接返回
# 旧快照（宁可旧几秒，绝不阻塞）。前端另有「沿用上次已知温度」兜底，数值不会消失。
_DISK_TEMP_CACHE = {"t": 0.0, "v": None, "devs": None}
_DISK_TEMP_TTL = 4.0
_DISK_TEMP_LOCK = _threading.Lock()

def get_disk_temps_cached(devs):
    now = time.time()
    key = tuple(devs or ())
    c = _DISK_TEMP_CACHE
    if c["devs"] == key and now - c["t"] <= _DISK_TEMP_TTL:
        return c["v"]
    # 缓存过期：尝试获取扫描权（非阻塞）；拿不到说明另一线程正在扫，直接返回旧值。
    if _DISK_TEMP_LOCK.acquire(blocking=False):
        try:
            # 双重检查：等待期间可能已被其他线程刷新
            if c["devs"] != key or now - c["t"] > _DISK_TEMP_TTL:
                c["v"] = get_disk_temps(devs or [])
                c["devs"] = key
                c["t"] = time.time()
        finally:
            _DISK_TEMP_LOCK.release()
        return c["v"]
    return c["v"]


_DISK_IO_CACHE = {}   # dev -> {"sectors": int, "t": float}；按 /proc/diskstats 扇区计数判断磁盘是否真的在读写
_DISK_FRIENDLY_CACHE = {}   # dev -> {"name","model","category","is_system","size","t"}; 5 分钟缓存
_DISK_FRIENDLY_TTL = 300
_SYSTEM_DISK_DEV = None   # 启动时确定一次（系统盘一般不会换）


def _detect_system_disk_dev():
    """确定 / 挂载的盘 dev（如 sda/nvme0n1）。挂载在 /boot、/boot/efi 不算系统盘。"""
    global _SYSTEM_DISK_DEV
    if _SYSTEM_DISK_DEV is not None:
        return _SYSTEM_DISK_DEV
    try:
        out = shell.run_cmd(["findmnt", "-n", "-o", "SOURCE", "/"], 3)
        src = (out.strip().splitlines() or [""])[0].strip()
        # 形如 /dev/nvme0n1p2[/subvol] 的 btrfs 源，先砍掉子卷部分
        m0 = re.match(r"^(/dev/[A-Za-z0-9/_.-]+?)(?:\[.*\])?$", src)
        if m0:
            src = m0.group(1)
        part = src.replace("/dev/", "").strip()
        if part:
            # 首选 lsblk PKNAME 直接问父盘（对 nvme/mmcblk/dm 都稳）
            try:
                pk = shell.run_cmd(["lsblk", "-no", "PKNAME", src], 3).strip().splitlines()
                pk0 = pk[0].strip() if pk else ""
                if pk0:
                    _SYSTEM_DISK_DEV = pk0
                    return _SYSTEM_DISK_DEV
            except Exception:
                pass
            # 回退：手工剥离分区后缀（nvme0n1p2 → nvme0n1；mmcblk0p1 → mmcblk0；sda3 → sda）
            mn = re.match(r"^(nvme\d+n\d+)p\d+$", part) or re.match(r"^(mmcblk\d+)p\d+$", part)
            if mn:
                _SYSTEM_DISK_DEV = mn.group(1)
            else:
                _SYSTEM_DISK_DEV = re.sub(r"\d+$", "", part)
    except Exception:
        pass
    return _SYSTEM_DISK_DEV or ""


def _short_size(size_b):
    """字节数 → '14.0T'/'120G'。"""
    try:
        b = int(size_b)
    except Exception:
        return "?"
    gb = b / 1e9
    if gb >= 1000:
        v = gb / 1000
        return f"{v:.0f}T" if v >= 10 else f"{v:.1f}T"
    return f"{gb:.0f}G"


def _short_disk_name(model, size_str, category, is_system):
    """生成盘的简短可读名（用于「当前温度」卡片）。"""
    if is_system:
        return f"系统盘({category})"
    # 取品牌简称（去掉 "（xxx）" 后缀）
    brand_cn = ""
    if model:
        b, _ = disk_brand_and_feature(model)
        if b:
            brand_cn = b.split("(")[0]
    if brand_cn and size_str and size_str != "?":
        return f"{brand_cn}{size_str}"
    if model:
        return model[:16]
    return "未知"


def _disk_friendly_info(dev):
    """返回该盘的友好名/型号/分类/盘位/是否系统盘。带 5 分钟缓存。
    设计目标：让前端「当前温度」卡片上的 nvme0n1/sda/sdb 一眼能认出是哪块物理盘。"""
    dev = str(dev).replace("/dev/", "").strip()
    if not dev:
        return {"dev": "", "name": dev, "model": "", "category": "HDD", "is_system": False}
    now = time.time()
    cached = _DISK_FRIENDLY_CACHE.get(dev)
    if cached and now - cached["t"] < _DISK_FRIENDLY_TTL:
        return cached
    info = {"dev": dev, "name": dev, "model": "", "category": "HDD",
            "is_system": False, "size": "?", "t": now}
    # 1) lsblk 轻量拿 ROTA/TRAN/SIZE
    try:
        lsblk = shell.run_cmd(["lsblk", "-dn", "-b", "-o", "NAME,SIZE,ROTA,TRAN", f"/dev/{dev}"], 3)
        for line in lsblk.strip().splitlines():
            p = line.split()
            if p and p[0] == dev and len(p) >= 2:
                info["size"] = _short_size(p[1])
                rota = p[2] if len(p) > 2 else "1"
                tran = p[3] if len(p) > 3 else ""
                if dev.startswith("nvme"):
                    info["category"] = "NVMe"
                elif rota == "0":
                    info["category"] = "SSD"
                else:
                    info["category"] = "HDD"
                break
    except Exception:
        pass
    # 2) smartctl -i 拿 model（带 -n standby 不唤醒休眠盘）
    try:
        out = shell.sudo_cmd([SMARTCTL, "-n", "standby", "-i", f"/dev/{dev}"], 5)
        if out:
            m = re.search(r"(?:Device Model|Model Number|Product):\s*(.+)", out)
            if m:
                info["model"] = m.group(1).strip()
    except Exception:
        pass
    # 3) 系统盘判定
    sys_dev = _detect_system_disk_dev()
    info["is_system"] = (dev == sys_dev)
    # 4) 友好名
    info["name"] = _short_disk_name(info["model"], info["size"], info["category"], info["is_system"])
    _DISK_FRIENDLY_CACHE[dev] = info
    return info

def _list_all_disk_devs():
    """返回本机全部块设备路径（/dev/sdX + /dev/nvmeXnY），供「逐风扇 disk 规则」在用户未配置
    全局监控盘白名单（fan_disk_temp.disks 为空）时，自动用全部盘作为温度源。
    避免用户只设了逐风扇 disk 规则、却因全局 disks 为空导致风扇被静默跳过、永远不控。
    轻量 glob 枚举，不触发 smartctl（温度在调速线程里按需读取）。"""
    names = set()
    for l in "\n".join(_glob.glob("/dev/sd*")).split():
        n = l.strip().split("/")[-1]
        if re.match(r"^sd[a-z]+$", n):
            names.add(n)
    for l in "\n".join(_glob.glob("/dev/nvme*")).split():
        n = l.strip().split("/")[-1]
        if re.match(r"^nvme\d+n\d+$", n):
            names.add(n)
    return ["/dev/" + n for n in sorted(names)]


def _disk_idle(dev, idle_minutes):
    """按 /proc/diskstats 的扇区读写计数判断磁盘是否已连续 idle 满 idle_minutes。
    与 smartctl 检测到的 STANDBY 状态互补：部分环境下 STANDBY 检测不到，但盘确实无 I/O，
    此时也应允许停转风扇（用户「设了 5 分钟休眠风扇却一直转」多因此而来）。
    返回 True=已连续无 I/O 足够久（可停转）；False=近期有 I/O 或尚在计时窗口内。"""
    try:
        name = str(dev).replace("/dev/", "")
        last = None
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                # 字段：1 major 2 minor 3 dev 4 reads 5 rmerged 6 rsect 7 rms 8 writes 9 wmerged 10 wsect
                if len(parts) >= 10 and parts[2] == name:
                    last = int(parts[5]) + int(parts[9])
                    break
        now = time.time()
        if last is None:
            return False
        prev = _DISK_IO_CACHE.get(dev)
        if prev is None or prev["sectors"] != last:
            _DISK_IO_CACHE[dev] = {"sectors": last, "t": now}
            return False
        return (now - prev["t"]) >= idle_minutes * 60
    except Exception:
        return False

# ===================== 硬盘自检（A/B/C 三档）=====================
# A 档：smartctl -t long（只读， firmware 面扫，安全，可后台运行）。
# B 档：badblocks -wsv（读写覆盖，破坏性，仅允许独立盘：未挂载、非 RAID/LVM 成员）。
# C 档：badblocks -sv（只读表面扫描，不写数据、不伤盘，可在用盘/阵列成员上运行，1 遍读）。
# B/C 档都会把坏块 LBA 实时/结束时写入 -o 文件，供前端画「哨兵式」扇区网格。
DISK_TEST_LOCK = _threading.Lock()
DISK_TEST_JOBS = {}
# 自检历史持久化：每次自检完成/中止/失败追加一条，保留最近 50 条（供「自检记录」回查）
DISK_TEST_HISTORY_FILE = os.path.join(_config_dir(), "disk_test_history.json")
DISK_TEST_HISTORY_MAX = 50


def _load_disk_test_history():
    h = _load_json_file(DISK_TEST_HISTORY_FILE, [])
    return h if isinstance(h, list) else []


def _append_disk_test_history(record):
    try:
        hist = _load_disk_test_history()
        hist.append(record)
        hist = hist[-DISK_TEST_HISTORY_MAX:]
        _save_json_file(DISK_TEST_HISTORY_FILE, hist)
    except Exception:
        pass


def _is_standalone_disk(dev):
    """判断 dev 是否为「独立盘」：整块磁盘、未挂载、未被 RAID/LVM 等持有。"""
    if not _safe_token(dev):
        return False, "非法设备名"
    # 注意：必须加 -d（只看该设备本身），否则会递归列出其子设备（分区/RAID/LVM）的类型，
    # 导致即便顶层是 disk 也被误判为「非整块磁盘」。
    typ = shell.run_cmd(["lsblk", "-ndno", "TYPE", "/dev/%s" % dev], 3).strip()
    if typ != "disk":
        return False, "%s 不是整块磁盘（类型：%s）" % (dev, typ or "未知")
    try:
        tree = json.loads(shell.run_cmd(["lsblk", "-J", "-o", "NAME,MOUNTPOINT,TYPE", "/dev/%s" % dev], 3) or "{}")
    except Exception:
        tree = {}
    mounted = False
    def _has_mount(node):
        if node.get("mountpoint"):
            return True
        for c in node.get("children", []):
            if _has_mount(c):
                return True
        return False
    for node in tree.get("blockdevices", []):
        if _has_mount(node):
            mounted = True
            break
    if mounted:
        return False, "%s 或其分区已被挂载，无法执行破坏性测试" % dev
    holders = []
    try:
        holders = os.listdir("/sys/block/%s/holders/" % dev)
    except Exception:
        pass
    if holders:
        return False, "%s 正被其他块设备持有（%s），可能是 RAID/LVM 成员" % (dev, ", ".join(holders))
    return True, ""


# 硬盘自检前后的 SMART 关键属性快照：用于历史记录里"重映射/待处理/不可校正"前后对比
_SMART_HISTORY_IDS = (5, 197, 198)
def _smart_short_attrs(dev):
    """读 dev 的 SMART ID 5/197/198 RAW_VALUE，返回 {5:int, 197:int, 198:int}；失败/无值返回 {}。"""
    if not _safe_token(dev):
        return {}
    try:
        out = shell.sudo_cmd([SMARTCTL, "-n", "standby", "-A", "/dev/%s" % dev], 8)
    except Exception:
        return {}
    result = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            aid = int(parts[0])
        except ValueError:
            continue
        if aid in _SMART_HISTORY_IDS:
            try:
                result[aid] = int(parts[-1])  # RAW_VALUE 是行末数字
            except ValueError:
                pass
    return result


def _set_disk_test_done(dev, message, error=None):
    with DISK_TEST_LOCK:
        job = DISK_TEST_JOBS.get(dev)
        if not job:
            return
        job["state"] = "aborted" if error == "用户中止" else ("error" if error else "done")
        job["message"] = message
        job["error"] = error
        job["elapsed"] = int(time.time() - job["started_at"])
    # 追加历史记录（写文件放锁外，避免长 IO 卡住自检轮询）
    try:
        _append_disk_test_history({
            "dev": dev,
            "type": job.get("type", "long"),
            "state": job["state"],
            "message": message,
            "error": error,
            "elapsed": job["elapsed"],
            "bad_blocks": len(job.get("bad_blocks") or []),
            "bad_lbas": (job.get("bad_blocks") or [])[:20],  # 坏块 LBA 位置（最多 20 个，前端可显示）
            "total_bytes": job.get("total_bytes") or 0,      # 扫描容量（字节）
            "smart_before": job.get("smart_before") or {},   # 扫描前 SMART 关键属性快照
            "smart_after": _smart_short_attrs(dev),          # 扫描后 SMART 关键属性快照
            "result": job.get("result"),
            "finished_at": int(time.time()),
        })
    except Exception:
        pass


def _abort_smart_long(dev):
    shell.sudo_cmd([SMARTCTL, "-X", "/dev/%s" % dev], 10)
    _set_disk_test_done(dev, "SMART 长自检已中止", error="用户中止")


def _start_smart_long(dev):
    out = shell.sudo_cmd([SMARTCTL, "-t", "long", "/dev/%s" % dev], 30)
    if "invalid" in out.lower() or ("error" in out.lower() and "abort" not in out.lower()):
        if "in progress" not in out.lower():
            raise RuntimeError(out.strip().splitlines()[-1] if out.strip() else "smartctl 启动自检失败")
    with DISK_TEST_LOCK:
        DISK_TEST_JOBS[dev] = {
            "type": "long",
            "state": "running",
            "started_at": time.time(),
            "message": "SMART 长自检已启动，正在轮询进度",
            "progress": 0,
            "elapsed": 0,
            "eta_total": None,
            "eta_remain": None,
            "result": None,
            "error": None,
        }
    _threading.Thread(target=_smart_long_worker, args=(dev,), daemon=True).start()


def _smart_long_worker(dev):
    dev_path = "/dev/%s" % dev
    last_poll = 0
    while True:
        with DISK_TEST_LOCK:
            job = DISK_TEST_JOBS.get(dev)
        if not job or job.get("state") != "running":
            break
        now = time.time()
        if now - last_poll < 20:
            time.sleep(2)
            continue
        last_poll = now
        try:
            cap = shell.sudo_cmd([SMARTCTL, "-c", dev_path], 15)
            m = re.search(r"Self-test routine in progress.*? (\d+)% remaining", cap, re.I | re.S)
            if m:
                rem = int(m.group(1))
                prog = max(0, min(100, 100 - rem))
                with DISK_TEST_LOCK:
                    j = DISK_TEST_JOBS.get(dev)
                    if j and j["state"] == "running":
                        j["progress"] = prog
                        j["message"] = "SMART 长自检进行中，剩余 %d%%" % rem
                        j["elapsed"] = int(now - j["started_at"])
                        if prog > 0.5:
                            _eta = (now - j["started_at"]) / (prog / 100.0)
                            j["eta_total"] = int(_eta)
                            j["eta_remain"] = max(0, int(_eta - (now - j["started_at"])))
                continue
            log_out = shell.sudo_cmd([SMARTCTL, "-l", "selftest", dev_path], 15)
            lines = [l for l in log_out.splitlines() if re.match(r"^\s*\d+", l.strip())]
            if lines:
                parts = lines[0].split()
                status = " ".join(parts[2:-2]) if len(parts) >= 4 else ""
                if "completed without error" in status.lower():
                    _set_disk_test_done(dev, "SMART 长自检完成，无错误")
                elif "aborted" in status.lower() or "interrupted" in status.lower():
                    _set_disk_test_done(dev, "SMART 长自检已中止", error="用户中止")
                else:
                    _set_disk_test_done(dev, "SMART 长自检结束：%s" % status, error=status if "error" in status.lower() else None)
            else:
                _set_disk_test_done(dev, "SMART 长自检完成")
        except Exception as e:
            _set_disk_test_done(dev, "SMART 长自检异常：%s" % e, error=str(e))
            break
        time.sleep(5)


def _estimate_badblocks_eta(dev, passes=4, readonly=False):
    """根据容量和 SSD/HDD 给 badblocks 一个保守参考总用时（秒）。
    destructive（-wsv）默认 4 pass 覆写；surface（-sv）1 pass 只读。"""
    try:
        size_bytes = int(subprocess.check_output(["blockdev", "--getsize64", "/dev/%s" % dev], text=True, stderr=subprocess.DEVNULL).strip())
    except Exception:
        return None
    try:
        with open("/sys/block/%s/queue/rotational" % dev) as f:
            is_hdd = f.read().strip() == "1"
    except Exception:
        is_hdd = True
    # 保守速度：只读 HDD 约 150MB/s、SSD 约 400MB/s；覆写 HDD 约 50MB/s、SSD 约 120MB/s
    if readonly:
        speed = 150 * 1024 * 1024 if is_hdd else 400 * 1024 * 1024
    else:
        speed = 50 * 1024 * 1024 if is_hdd else 120 * 1024 * 1024
    if size_bytes <= 0 or speed <= 0:
        return None
    return int(size_bytes * passes / speed)


def _start_badblocks(dev, mode="destructive"):
    """启动 badblocks 扫描。
    mode=destructive：-wsv 读写覆盖（破坏性，仅独立盘）；mode=surface：-sv 只读（可在用盘跑）。
    两种模式都用 -o 把坏块 LBA 列表写到 /tmp，供前端扇区网格标红。"""
    if mode == "destructive":
        ok, reason = _is_standalone_disk(dev)
        if not ok:
            raise RuntimeError(reason)
    dev_path = "/dev/%s" % dev
    try:
        total_bytes = int(subprocess.check_output(["blockdev", "--getsize64", dev_path], text=True, stderr=subprocess.DEVNULL).strip())
    except Exception:
        total_bytes = 0
    outfile = "/tmp/nasdeck_bb_%s.txt" % dev
    try:
        if os.path.exists(outfile):
            os.remove(outfile)
    except Exception:
        pass
    blocksize = 4096
    if mode == "surface":
        passes = 1
        cmd = [BADBLOCKS, "-sv", "-b", str(blocksize), "-o", outfile, dev_path]
        msg = "只读表面扫描已启动（不写数据，可在用盘上运行）"
        hint = _estimate_badblocks_eta(dev, passes=1, readonly=True)
    else:
        passes = 4
        cmd = [BADBLOCKS, "-wsv", "-b", str(blocksize), "-o", outfile, dev_path]
        msg = "坏块慢扫已启动（破坏性，会覆盖全盘数据）"
        hint = _estimate_badblocks_eta(dev, passes=4)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as e:
        raise RuntimeError("启动 badblocks 失败：%s" % e)
    with DISK_TEST_LOCK:
        DISK_TEST_JOBS[dev] = {
            "type": "surface" if mode == "surface" else "badblocks",
            "state": "running",
            "started_at": time.time(),
            "message": msg,
            "progress": 0,
            "elapsed": 0,
            "eta_total": None,
            "eta_remain": None,
            "eta_total_hint": hint,
            "result": None,
            "error": None,
            "pid": proc.pid,
            "blocksize": blocksize,
            "total_bytes": total_bytes,
            "smart_before": _smart_short_attrs(dev),  # 扫描前 SMART 关键属性快照（重映射/待处理/不可校正）
            "bad_blocks": [],
            "outfile": outfile,
        }
    _threading.Thread(target=_badblocks_worker, args=(dev, proc, passes, outfile), daemon=True).start()


def _badblocks_worker(dev, proc, passes=4, outfile=None):
    try:
        buf = ""
        pass_completed = 0
        total_passes = passes
        seen_bad = set()

        def _collect_bad(outf):
            """从 badblocks -o 文件收集坏块 LBA（可能扫描过程中实时写入，也可能只在结束时落盘）。"""
            nonlocal seen_bad
            try:
                with open(outf) as f:
                    for line in f:
                        line = line.strip()
                        if line.isdigit():
                            seen_bad.add(int(line))
                if seen_bad:
                    with DISK_TEST_LOCK:
                        j = DISK_TEST_JOBS.get(dev)
                        if j:
                            j["bad_blocks"] = sorted(seen_bad)
            except Exception:
                pass

        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buf += chunk
            tail = buf[-8192:]
            # badblocks -s 用退格符原地覆盖刷新进度，buf 里会堆满历史行，
            # re.search 会永远命中最早的 0.00% —— 必须 findall 取最后一个。
            ms = re.findall(r"([\d.]+)% done", tail)
            cur = float(ms[-1]) if ms else None
            pc = buf.count("Pass completed")
            if pc > pass_completed:
                pass_completed = pc
            if cur is not None:
                # 把多个 pass 各自的 0~100% 折算成整体进度，避免 ETA 被低估、进度条回弹
                overall = (pass_completed + cur / 100.0) / total_passes * 100.0
                overall = max(0.0, min(100.0, overall))
                now = time.time()
                with DISK_TEST_LOCK:
                    j = DISK_TEST_JOBS.get(dev)
                    if j and j["state"] == "running":
                        j["progress"] = round(overall, 1)
                        j["elapsed"] = int(now - j["started_at"])
                        if overall > 0:
                            total_eta = j["elapsed"] / (overall / 100.0)
                            j["eta_total"] = int(total_eta)
                            j["eta_remain"] = max(0, int(total_eta - j["elapsed"]))
            ems = re.findall(r"\((\d+)/(\d+)/(\d+) errors\)", tail)
            if ems:
                read_err, write_err, corr = ems[-1]
                with DISK_TEST_LOCK:
                    j = DISK_TEST_JOBS.get(dev)
                    if j:
                        j["result"] = {"read_errors": read_err, "write_errors": write_err, "corrected": corr}
            if outfile:
                _collect_bad(outfile)
        rc = proc.wait()
        if outfile:
            _collect_bad(outfile)
        with DISK_TEST_LOCK:
            j = DISK_TEST_JOBS.get(dev)
        if j and j["state"] == "running":
            bad_n = len(j.get("bad_blocks") or [])
            prefix = "表面扫描" if total_passes == 1 else "坏块慢扫"
            if rc == 0:
                _set_disk_test_done(dev, "%s完成，未发现坏块" % prefix)
            elif rc == 1:
                _set_disk_test_done(dev, "%s完成，发现 %d 个坏块" % (prefix, bad_n))
            else:
                _set_disk_test_done(dev, "%s退出（返回码 %d）" % (prefix, rc), error="返回码 %d" % rc)
    except Exception as e:
        _set_disk_test_done(dev, "扫描异常：%s" % e, error=str(e))


def _abort_badblocks(dev):
    with DISK_TEST_LOCK:
        job = DISK_TEST_JOBS.get(dev)
        pid = job.get("pid") if job else None
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    _set_disk_test_done(dev, "扫描已中止", error="用户中止")


_DISKS_CACHE = {"t": 0.0, "v": None}
_DISKS_TTL = 60
_DISKS_LOCK = _threading.Lock()

def get_disks(force=False):
    """硬盘 SMART 统一采集（60s TTL 缓存 + force 支持）。
    get_disks 全量 smartctl 扫盘很重（每盘最多 20s 超时），此前 /api/all、/api/raid、/api/disks、
    /api/metrics 等各自调用都会重复扫盘。统一为一份快照共享；force=True 强制重扫
    （用户手动「立即刷新」/硬盘自检完成后，需看最新 SMART 数据时）。"""
    now = time.time()
    c = _DISKS_CACHE
    if not force and c["v"] is not None and now - c["t"] <= _DISKS_TTL:
        return c["v"]
    if force:
        with _DISKS_LOCK:
            c["v"] = _collect_disks_full()
            c["t"] = time.time()
        return c["v"]
    if _DISKS_LOCK.acquire(blocking=False):
        try:
            if c["v"] is None or now - c["t"] > _DISKS_TTL:
                c["v"] = _collect_disks_full()
                c["t"] = time.time()
        finally:
            _DISKS_LOCK.release()
        return c["v"]
    return c["v"] or []

def _collect_disks_full():
    """采集所有块设备 + SMART（SD/SAS 用 ls /dev/sd*，NVMe 用 ls /dev/nvme*；再用正则过滤掉分区/控制器，
    支持多位盘名 sdaa/sdab 与多控制器 nvme10n1 等；smartctl 拿详情，不依赖 lsblk 字段对齐）"""
    disks = []
    out = "\n".join(glob.glob("/dev/sd*"))
    devnames = sorted(set(l.strip().split('/')[-1] for l in out.split()
                          if l.strip() and re.match(r"^sd[a-z]+$", l.strip().split('/')[-1])))
    # NVMe 命名空间（如 /dev/nvme0n1；控制器 /dev/nvme0 不匹配 n\d+，不会误纳入）
    nvme_out = "\n".join(glob.glob("/dev/nvme*"))
    for l in nvme_out.split():
        n = l.strip().split('/')[-1]
        if re.match(r"^nvme\d+n\d+$", n):
            devnames.append(n)
    devnames = sorted(set(devnames))
    # lsblk 补充容量/rota/tran（-n 不打印表头，但仍防御性跳过首行若为表头）
    lsblk = shell.run_cmd(["lsblk", "-dn", "-b", "-o", "NAME,SIZE,ROTA,TRAN"], 5)
    linfo = {}
    for line in lsblk.strip().splitlines():
        p = line.split()
        if not p or p[0].upper() == "NAME":
            continue
        if len(p) >= 2:
            linfo[p[0]] = {"size_b": p[1], "rota": p[2] if len(p) > 2 else "?",
                           "tran": p[3] if len(p) > 3 else ""}
    # 真实转速：smartctl -i 的 Rotation Rate（覆盖 ATA/SAS 机械盘；SSD 标“固态(SSD)”）
    rpm_map = _smart_rpm_by_serial()
    def _collect_one_disk(name, info, rpm_map):
        dev = f"/dev/{name}"
        size_b = info.get("size_b", "0")
        try:
            gb = int(size_b) / 1e9
            size_str = f"{gb/1000:.1f}T" if gb >= 1000 else f"{gb:.0f}G"
        except:
            size_str = "?"
        smart_out = shell.sudo_cmd([SMARTCTL, "-n", "standby", "-a", dev], 20)
        # 偶发超时/总线竞争导致空输出时重试一次；STANDBY 状态不重试，避免唤醒休眠盘。
        if not smart_out and "STANDBY" not in smart_out.upper():
            smart_out = shell.sudo_cmd([SMARTCTL, "-n", "standby", "-a", dev], 20)
        disk = {
            "dev": name, "size": size_str, "rota": info.get("rota", "?"),
            "model": "", "serial": "", "tran": info.get("tran", ""), "vendor": "",
            "type": "ata", "health": "N/A", "health_ok": False, "asleep": False,
            "temp": None, "power_on_hours": None,
        }
        if smart_out:
            if "SMART Health Status" in smart_out:
                disk["type"] = "sas"
                disk.update(parse_sas_smart(smart_out))
                m = re.search(r"Vendor:\s*(.+)", smart_out)
                disk["vendor"] = m.group(1).strip() if m else ""
                m = re.search(r"Product:\s*(.+)", smart_out)
                disk["model"] = m.group(1).strip() if m else ""
                m = re.search(r"Serial number:\s*(\S+)", smart_out)
                disk["serial"] = m.group(1) if m else ""
            elif "SMART/Health Information" in smart_out or ("Model Number:" in smart_out and "Namespace" in smart_out):
                disk["type"] = "nvme"
                disk["tran"] = "nvme"
                disk["rota"] = "0"
                disk.update(parse_nvme_smart(smart_out))
                m = re.search(r"Model Number:\s*(.+)", smart_out)
                disk["model"] = m.group(1).strip() if m else ""
                m = re.search(r"Serial Number:\s*(\S+)", smart_out)
                disk["serial"] = m.group(1) if m else ""
            elif "overall-health" in smart_out:
                disk["type"] = "ata"
                disk.update(parse_ata_smart(smart_out))
                m = re.search(r"Device Model:\s*(.+)", smart_out)
                if not m:
                    m = re.search(r"Model Family:\s*(.+)", smart_out)
                disk["model"] = m.group(1).strip() if m else ""
                m = re.search(r"Serial Number:\s*(\S+)", smart_out)
                disk["serial"] = m.group(1) if m else ""
        # 容量兜底：lsblk 返回 0 / 缺失 / 解析失败时，从 smartctl 取容量
        # ATA/SAS 用 "User Capacity"，NVMe 用 "Namespace 1 Size/Capacity" / "Total NVM Capacity"
        if smart_out and (disk["size"] in ("0G", "0.0G", "?", "0") or info.get("size_b") in ("0", "")):
            m = re.search(r"User Capacity:\s*([\d,]+)\s*bytes", smart_out)
            if not m:
                m = re.search(r"Namespace 1 Size/Capacity:\s*([\d,]+)", smart_out)
            if not m:
                m = re.search(r"Total NVM Capacity:\s*([\d,]+)", smart_out)
            if m:
                cap = int(m.group(1).replace(",", ""))
                gb = cap / 1e9
                disk["size"] = f"{gb/1000:.1f}T" if gb >= 1000 else f"{gb:.0f}G"
        b, f = disk_brand_and_feature(disk["model"])
        disk["brand"] = b
        disk["feature"] = f
        disk["rpm"] = rpm_map.get(disk.get("serial", "").upper(), "") if disk.get("serial") else ""
        # 类型兜底：lsblk ROTA 不可靠时，用 smartctl Rotation Rate 覆盖 HDD/SSD
        if disk.get("rpm"):
            if disk["rpm"] == "固态(SSD)":
                disk["rota"] = "0"
            elif "rpm" in disk["rpm"].lower():
                disk["rota"] = "1"
        # 休眠盘修正：smartctl -n standby 不会唤醒休眠盘，返回含 STANDBY 的提示且无健康行。
        # 此前被兜底成 health="N/A" / health_ok=False，导致健康卡误标红（与温度卡“休眠灰显”矛盾）。
        # 休眠是正常的省电状态，≠ 坏盘：标 asleep、health=休眠、health_ok=True，避免无谓报警。
        if smart_out and "STANDBY" in smart_out.upper() \
           and not re.search(r"overall-health|SMART Health Status|SMART/Health Information", smart_out):
            disk["asleep"] = True
            disk["health"] = "休眠"
            disk["health_ok"] = True
            disk["temp"] = None
        else:
            disk["asleep"] = disk.get("asleep", False)
            disk["health_ok"] = disk["health"].upper() in ("OK", "PASSED")
        disk["standalone"], disk["standalone_reason"] = _is_standalone_disk(name)
        return disk

    # 并行采集：每块盘的 smartctl 相互独立，多线程同时跑，首屏 /api/all 不再被逐盘串行拖慢。
    if devnames:
        try:
            from concurrent.futures import ThreadPoolExecutor
            _w = min(4, len(devnames))
            with ThreadPoolExecutor(max_workers=_w) as _ex:
                disks = list(_ex.map(
                    lambda n: _collect_one_disk(n, linfo.get(n, {}), rpm_map), devnames))
        except Exception:
            disks = [_collect_one_disk(n, linfo.get(n, {}), rpm_map) for n in devnames]
    else:
        disks = []
    return disks


# ===================== 磁盘静态元数据缓存（v2.4 阶段 3 自 presentation/api/endpoints/helpers.py 平移） =====================
# /api/metrics 被前端每 1s 轮询，而 get_disks() 对每块盘跑 smartctl
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
