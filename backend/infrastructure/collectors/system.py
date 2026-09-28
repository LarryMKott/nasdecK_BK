# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.command import cmd_runner as shell
from backend.infrastructure.caching import _ttl_cache
from backend.infrastructure.collectors.board import get_board
from backend.infrastructure.collectors.board import get_memory_modules
from backend.infrastructure.collectors.cputemp import _parse_cpu_temp
from backend.infrastructure.collectors.cputemp import _parse_mb_temp
from backend.infrastructure.collectors.cputemp import _parse_sensors_all
from backend.infrastructure.collectors.cputemp import _temp_snapshot_read
from backend.infrastructure.collectors.gpu import _clean_gpu_name
from backend.infrastructure.collectors.metrics import _METRICS_LOCK
from backend.infrastructure.collectors.metrics import _metrics_cur
from backend.infrastructure.fans.control import get_fan_status
from backend.infrastructure.fans.enumerate import _enumerate_fans
from backend.infrastructure.fans.labels import _fan_label_for
from backend.infrastructure.command.cmd_runner import read_file
import json
import os
import platform
import re
import socket
import threading as _threading
import time



# ===================== RAPL 功耗 =====================
# Intel RAPL（Running Average Power Limit）通过 MSR 提供 CPU 封装/核心/非核心/内存的真实功耗。
# Linux 以 powercap 子系统暴露在 /sys/class/powercap/intel-rapl:*/ 下：
#   energy_uj           当前能量计数（微焦耳，单调递增，到 max_energy_range_uj 回绕）
#   两次采样差值 / 时间间隔 = 平均功耗（瓦）
# 支持：Intel 全系（含 G5400 等低端）+ AMD zen2+。读不到返回 None（非 Intel / 内核未挂载）。
_RAPL_BASE = "/sys/class/powercap"

def _rapl_read_energy(domain_path):
    """读某个域的 energy_uj（微焦耳）。文件缺失返回 None。"""
    try:
        with open(domain_path + "/energy_uj", "r") as f:
            return int(f.read().strip())
    except Exception:
        return None

def _rapl_energy_max(domain_path):
    """该域能量计数器的回绕上限（微焦耳）。读不到给个安全默认。"""
    try:
        with open(domain_path + "/max_energy_range_uj", "r") as f:
            return int(f.read().strip())
    except Exception:
        return 262143300000  # 默认 2^18 uj * 1e6，Intel 常见值

def get_rapl_power():
    """读取 CPU 封装/核心/非核心/内存实时功耗（瓦）。

    返回 {"package": w, "core": w, "uncore": w, "dram": w, "ok": bool, "total": w}；
    RAPL 不可用时返回 {"ok": False}。函数内两次读数差分（间隔 0.25s），
    不依赖跨请求状态，因此可安全地放在 get_system 的 60s 缓存里也能出数。
    """
    import time as _t
    base = _RAPL_BASE
    if not os.path.isdir(os.path.join(base, "intel-rapl:0")):
        return {"ok": False}
    paths = {
        "package": os.path.join(base, "intel-rapl:0"),
        "core": os.path.join(base, "intel-rapl:0:0"),
        "uncore": os.path.join(base, "intel-rapl:0:1"),
        "dram": os.path.join(base, "intel-rapl:0:2"),
    }
    def sample():
        out = {}
        for k, p in paths.items():
            e = _rapl_read_energy(p)
            if e is not None:
                out[k] = e
        return out
    e1 = sample()
    if not e1:
        return {"ok": False}
    _t.sleep(0.25)
    e2 = sample()
    watts = {}
    for k in ("package", "core", "uncore", "dram"):
        if k in e1 and k in e2:
            diff = e2[k] - e1[k]
            if diff < 0:  # 计数器回绕
                diff += _rapl_energy_max(paths[k])
            watts[k] = round(diff / 0.25 / 1e6, 2)
    if not watts:
        return {"ok": False}
    total = round(sum(watts.values()), 2)
    watts["ok"] = True
    watts["total"] = total
    return watts


@_ttl_cache(60)
def get_network_nics():
    """独立采集网卡列表（IP/MAC/速率/驱动/状态等），供 /api/network 轻量刷新，不进 get_system 的 60s 缓存。"""
    def _nic_hw_info(name):
        # 补充单张网卡的硬件信息：MTU / 双工 / 驱动 / 总线 / 厂商型号。
        # 全部失败也不影响其它采集（OVS 桥等虚拟口拿不到就留空）。
        info = {"mtu": "", "duplex": "", "driver": "", "bus_info": "", "model": "",
                "max_speed": "", "wol_support": "", "wol_current": "", "wol_supported": None, "wol_enabled": None}
        mtu = read_file(f"/sys/class/net/{name}/mtu").strip()
        if mtu.isdigit():
            info["mtu"] = mtu
        dup = read_file(f"/sys/class/net/{name}/duplex").strip()
        if dup:
            info["duplex"] = dup
        try:
            out = shell.run_cmd(["ethtool", "-i", name], 3)
            for line in out.splitlines():
                if line.startswith("driver:"):
                    info["driver"] = line.split(":", 1)[1].strip()
                elif line.startswith("bus-info:"):
                    info["bus_info"] = line.split(":", 1)[1].strip()
        except Exception:
            pass
        # 总线是 PCI 地址时，用 lspci -nn 反查厂商型号（飞牛的「网卡硬件信息」）
        if info["bus_info"] and re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.", info["bus_info"], re.I):
            try:
                out = shell.run_cmd(["lspci", "-nn", "-s", info["bus_info"]], 3)
                # 行末可能带「 (rev 10)」等后缀，故不锚定行尾；只抓「]: 」到「[厂商:设备]」之间的描述
                mh = re.search(r"\]:\s*(.+?)\s*\[[0-9a-f]{4}:[0-9a-f]{4}\]", out, re.S)
                if mh:
                    info["model"] = mh.group(1).strip()
            except Exception:
                pass
        # WOL（网络唤醒）只读检测（v2.3.0 3.2）：解析 `ethtool <iface>` 的两行——
        #   Supports Wake-on: 网卡硬件支持哪些唤醒模式（d=仅禁用，即不支持）
        #   Wake-on:          当前生效的唤醒模式（g=魔法包，最常用；d=已禁用）
        # 纯只读，绝不做 ethtool -s 修改（改 WOL 属系统管理操作）。
        try:
            _wo = shell.run_cmd(["ethtool", name], 3)
            _sup = ""
            _cur = ""
            for _ln in _wo.splitlines():
                _t = _ln.strip()
                if _t.startswith("Supports Wake-on:"):
                    _sup = _t.split(":", 1)[1].strip()
                elif _t.startswith("Wake-on:"):
                    _cur = _t.split(":", 1)[1].strip()
            if _sup or _cur:
                info["wol_support"] = _sup
                info["wol_current"] = _cur
                # 除 'd'（禁用）外还有 p/u/m/b/g/a 任一模式 = 硬件支持 WOL
                info["wol_supported"] = any(ch in _sup for ch in "pumbga")
                info["wol_enabled"] = bool(_cur) and any(ch in _cur for ch in "pumbga")
            # 网卡支持的最大链路速率（Mbps）：从 ethtool 的 Supported link modes
            # 解析形如 2500baseT 的数字（单位 Mbps）取最大。用于前端「未跑满」提示：
            # I226-V 支持 2500 但当前只协商到 1000/100 时，提示瓶颈在对端/网线。
            try:
                _mm = re.findall(r"(\d+)base[TSRFML]", _wo)
                if _mm:
                    info["max_speed"] = str(max(int(x) for x in _mm))
            except Exception:
                pass
        except Exception:
            pass
        return info

    # 网卡（只显示物理网卡 / bond / 桥接端口，过滤 docker/虚拟网桥/容器/虚拟机等噪音接口）
    link_out = shell.run_cmd(["ip", "-o", "link", "show"], 5)
    addr_out = shell.run_cmd(["ip", "-o", "addr", "show"], 5)
    # 先直接从 addr 输出解析「接口名 -> IP」映射：
    # - ip -o link 对 veth/peer 接口会显示 name@ifN 后缀，而 ip -o addr 用原始名（也可能带 @ifN），
    #   直接按行解析 addr 可绕开「按名回查整行」的匹配失败问题；
    # - 不同版本/系统的 `ip -o addr show` 格式并不一致：有的接口名后带冒号，有的不带；
    #   有的 inet 在接口名同行，有的 inet 在续行。用状态机维护当前接口名最稳；
    # - 同一接口可能同时有 inet6/inet，这里优先取 IPv4。
    ip4_map = {}
    ip6_map = {}
    cur_iface = None
    for aline in addr_out.splitlines():
        header = re.match(r"^\d+:\s+(\S+)", aline)
        if header:
            # 去掉末尾可能存在的冒号和 @ifN 后缀，与 link 解析出的 name 对齐
            cur_iface = header.group(1).split("@")[0].rstrip(":")
        if not cur_iface:
            continue
        im4 = re.search(r"\binet\s+(\S+)", aline)
        if im4 and cur_iface not in ip4_map:
            ip4_map[cur_iface] = im4.group(1).split("/")[0]
        im6 = re.search(r"\binet6\s+(\S+)", aline)
        if im6:
            # 同一接口可能有多条 inet6（含 fe80 链路本地），用列表收集，优先全局地址
            ip6_map.setdefault(cur_iface, []).append(im6.group(1).split("/")[0])
    # 纯虚拟/容器/虚拟机接口不计入物理网卡列表，避免「无IP」噪音干扰查看
    SKIP_NIC_PREFIX = ("lo", "docker", "br-", "veth", "ovs-system", "__tmp",
                       "flannel", "cni", "tailscale", "ts", "wg", "safeline", "vnet", "virbr")
    nics = []
    for line in link_out.splitlines():
        m = re.match(r"\d+:\s+(\S+?):\s+<([^>]*)>.*?state\s+(\w+).*?link/(\S+)\s+(\S+)", line)
        if not m:
            continue
        name = m.group(1).split("@")[0]
        if name == "lo" or name.startswith(SKIP_NIC_PREFIX):
            continue
        state = m.group(3)
        mac = m.group(5)
        speed = read_file(f"/sys/class/net/{name}/speed").strip()
        if not speed.isdigit():
            speed = ""
        ip = ip4_map.get(name, "")
        nics.append({"name": name, "state": state, "mac": mac, "speed": speed,
                      "ip": ip, "ipv6": ""})
    # 附加实时网速（来自采集 daemon 的 _metrics_cur，每 2s 刷新一次）
    try:
        with _METRICS_LOCK:
            _net_rt = {n["name"]: n for n in _metrics_cur.get("net", [])}
        for nic in nics:
            rt = _net_rt.get(nic["name"])
            if rt:
                nic["rx_rate"] = rt.get("rx_rate", 0.0)
                nic["tx_rate"] = rt.get("tx_rate", 0.0)
    except Exception:
        pass
    # 补充每张网卡的硬件信息（MTU / 双工 / 驱动 / 总线 / 厂商型号）与 IPv6
    for nic in nics:
        nic.update(_nic_hw_info(nic["name"]))
        v6 = ip6_map.get(nic["name"], [])
        # 优先全局地址，没有全局地址再退而求其次显示链路本地
        nic["ipv6"] = next((a for a in v6 if not a.lower().startswith("fe80:")), (v6[0] if v6 else ""))
    # 去重：同一物理网卡与其 OVS 桥/虚拟端口常共享 MAC 地址，会被识别成两条记录
    # （如 fnOS 的 eno1 与 eno1-ovs 共享 MAC；接 USB 网卡时也可能出现「物理口 + 桥」两条）。
    # 按 MAC 归并最稳健——物理口名（不含 -ovs 后缀）作展示名，IP/速率/状态/实时流量各取所长，
    # 最终页面只显示一条完整记录。纯虚拟接口（无 MAC 或唯一 MAC）保持独立。
    by_mac = {}
    _no_mac = []
    for nic in nics:
        mac = (nic.get("mac") or "").lower().strip()
        if not mac:
            _no_mac.append(nic)
            continue
        by_mac.setdefault(mac, []).append(nic)
    merged_nics = []
    for mac, grp in by_mac.items():
        if len(grp) == 1:
            merged_nics.append(grp[0])
            continue
        # 多条 → 合并：优先用物理口名（不含 -ovs）
        phys = [n for n in grp if not n["name"].endswith("-ovs")] or grp
        m = dict(phys[0])
        for n in grp:
            if not m.get("ip") and n.get("ip"):
                m["ip"] = n["ip"]
            if not m.get("speed") and n.get("speed"):
                m["speed"] = n["speed"]
            if n.get("state", "").upper() == "UP" and m.get("state", "").upper() != "UP":
                m["state"] = n["state"]
            if n.get("rx_rate") is not None:
                m["rx_rate"] = n.get("rx_rate", 0.0)
            if n.get("tx_rate") is not None:
                m["tx_rate"] = n.get("tx_rate", 0.0)
            for k in ("ipv6", "mtu", "duplex", "driver", "bus_info", "model",
                      "wol_support", "wol_current", "max_speed"):
                if not m.get(k) and n.get(k):
                    m[k] = n[k]
            for bk in ("wol_supported", "wol_enabled"):
                if m.get(bk) is None and n.get(bk) is not None:
                    m[bk] = n[bk]
        # 显示名优先用 OVS 桥（与 fnOS 一致：真实 IP 配在桥上），硬件字段仍取自物理口
        ovs = [n for n in grp if n["name"].endswith("-ovs")]
        if ovs:
            m["name"] = ovs[0]["name"]
        # 实时流量轮询按「物理口名」匹配 /api/metrics（指标以物理口 eno1 上报，而非 eno1-ovs）
        m["phy_name"] = phys[0]["name"].replace("-ovs", "") if phys[0]["name"].endswith("-ovs") else phys[0]["name"]
        merged_nics.append(m)
    merged_nics.extend(_no_mac)
    nics = merged_nics
    return nics

_SYSTEM_CACHE = {"t": 0.0, "v": None}
_SYSTEM_TTL = 30
_SYSTEM_LOCK = _threading.Lock()

def get_system(force=False):
    """系统总览统一采集（30s TTL 缓存 + force 支持）。
    get_system 每次跑完整采集（sensors/风扇枚举/网卡/GPU/CPU/lscpu 等），
    /api/system 与 /api/all 此前各跑一遍。统一为一份快照共享；
    force=True 强制重采（检测页手动「立即刷新」）。"""
    now = time.time()
    c = _SYSTEM_CACHE
    if not force and c["v"] is not None and now - c["t"] <= _SYSTEM_TTL:
        return c["v"]
    if force:
        with _SYSTEM_LOCK:
            c["v"] = _collect_system_full()
            c["t"] = time.time()
        return c["v"]
    if _SYSTEM_LOCK.acquire(blocking=False):
        try:
            if c["v"] is None or now - c["t"] > _SYSTEM_TTL:
                c["v"] = _collect_system_full()
                c["t"] = time.time()
        finally:
            _SYSTEM_LOCK.release()
        return c["v"]
    return c["v"] or {}

def _collect_system_full():
    d = {}
    d["hostname"] = socket.gethostname()
    d["kernel"] = platform.release()
    d["os"] = "Debian 12 (bookworm) / fnOS"
    # CPU（基础字段 + 详细字段）
    lscpu = shell.run_cmd(["lscpu"], 5)
    def lscpu_field(name):
        m = re.search(rf"^{re.escape(name)}:\s*(.+)$", lscpu, re.MULTILINE)
        return m.group(1).strip() if m else None
    m = re.search(r"Model name:\s*(.+)", lscpu)
    d["cpu_model"] = m.group(1).strip() if m else "?"
    m = re.search(r"CPU\(s\):\s*(\d+)", lscpu)
    d["cpu_threads"] = int(m.group(1)) if m else 0
    m = re.search(r"Core\(s\) per socket:\s*(\d+)", lscpu)
    d["cpu_cores"] = int(m.group(1)) if m else 0
    m = re.search(r"CPU max MHz:\s*([\d.]+)", lscpu)
    # ⚠️ 语义提醒：cpu_freq 取自 lscpu 的 "CPU max MHz"，是**最大频率**（历史命名）。
    # 真正的当前频率见下方 cpu_info.current_freq_mhz，勿再拿本字段当"当前频率"显示。
    d["cpu_freq"] = m.group(1) if m else "?"

    def _int(s, default=0):
        try:
            return int(s)
        except Exception:
            return default
    def _float(s, default=None):
        try:
            return float(s)
        except Exception:
            return default

    # 扩展 CPU 详情（架构/缓存/虚拟化等），供前端可收起面板使用
    cpuinfo = read_file("/proc/cpuinfo", "")
    first_core = {}
    for line in cpuinfo.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            first_core[k.strip()] = v.strip()
        if "processor" in line and len(first_core) > 5:
            break
    flags = first_core.get("flags", first_core.get("Features", "")).split()
    # 当前频率：/proc/cpuinfo 的 "cpu MHz" 是**每个核的瞬时值**，单核采样会随负载在
    # min~max 间大幅跳动（容易被误读成"显示的是最大频率"）。取**全部核心的平均**更能
    # 代表整机当前频率（论坛反馈②）。若无该字段（部分 ARM / 虚拟机）则回退首核值。
    _core_mhz = []
    for _ln in cpuinfo.splitlines():
        if _ln[:7].lower() == "cpu mhz" and ":" in _ln:
            try:
                _core_mhz.append(float(_ln.split(":", 1)[1].strip()))
            except Exception:
                pass
    current_freq_mhz = (
        round(sum(_core_mhz) / len(_core_mhz), 1)
        if _core_mhz
        else _float(first_core.get("cpu MHz"))
    )
    virt = None
    if "vmx" in flags:
        virt = "Intel VT-x"
    elif "svm" in flags:
        virt = "AMD-V"
    d["cpu_info"] = {
        "model": d["cpu_model"],
        "arch": lscpu_field("Architecture"),
        "vendor": lscpu_field("Vendor ID"),
        "family": lscpu_field("CPU family"),
        "model_id": lscpu_field("Model"),
        "stepping": lscpu_field("Stepping"),
        "sockets": _int(lscpu_field("Socket(s)"), 1),
        "cores_per_socket": _int(lscpu_field("Core(s) per socket"), d["cpu_cores"]),
        "threads_per_core": _int(lscpu_field("Thread(s) per core"), 1),
        "byte_order": lscpu_field("Byte Order"),
        "address_sizes": lscpu_field("Address sizes"),
        "numa_nodes": _int(lscpu_field("NUMA node(s)"), 1),
        "min_freq_mhz": _float(lscpu_field("CPU min MHz")),
        "max_freq_mhz": _float(lscpu_field("CPU max MHz")) or _float(d.get("cpu_freq")),
        "current_freq_mhz": current_freq_mhz,   # 全部核心瞬时频率的平均值（见上方说明）
        "bogomips": _float(first_core.get("BogoMIPS") or first_core.get("bogomips")),
        "l1d": lscpu_field("L1d cache"),
        "l1i": lscpu_field("L1i cache"),
        "l2": lscpu_field("L2 cache"),
        "l3": lscpu_field("L3 cache"),
        "virtualization": virt,
        "features": {
            "lm": "lm" in flags,      # Long Mode / 64-bit
            "ht": "ht" in flags,      # Hyper-Threading
            "aes": "aes" in flags,
            "avx": "avx" in flags,
            "avx2": "avx2" in flags,
            "avx512": any(f.startswith("avx512") for f in flags),
        },
    }
    # 负载
    la = read_file("/proc/loadavg").split()
    d["load"] = la[:3] if len(la) >= 3 else ["0","0","0"]
    # 实时功耗（RAPL，Intel 真实测量；不可用则为 None）
    try:
        d["power"] = get_rapl_power()
    except Exception:
        d["power"] = {"ok": False}
    # uptime
    up = read_file("/proc/uptime").split()
    try:
        up_s = float(up[0])
        d["uptime"] = format_uptime(up_s)
    except:
        d["uptime"] = "?"
    # 内存
    meminfo = read_file("/proc/meminfo")
    mi = {}
    for line in meminfo.splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            mi[m.group(1)] = int(m.group(2))
    mt = mi.get("MemTotal", 0); ma = mi.get("MemAvailable", 0)
    used = mt - ma
    cached = mi.get("Cached", 0) + mi.get("Buffers", 0)
    d["memory"] = {
        "total": fmt_kb(mt), "used": fmt_kb(used), "available": fmt_kb(ma),
        "percent": round(used / mt * 100, 1) if mt else 0,
        # 缓存（文件页+缓冲）：可被内核自动回收，不占"真占用"；用于界面拆分展示
        "cached": fmt_kb(cached),
        "cached_kb": cached,
    }
    st = mi.get("SwapTotal", 0); sf = mi.get("SwapFree", 0)
    d["swap"] = {"total": fmt_kb(st), "used": fmt_kb(st - sf)}
    # 传感器分类解析（温度/风扇/电压）：温度/电压/CPU 温度统一走采集循环快照（~2s 准实时），
    # 快照未就绪（进程刚启动首拍）时回退一次实时 sensors -j，保证首屏不空。
    d["sensors"] = {"temps": [], "fans": [], "voltages": []}
    cpu_temp = None
    sens_j = None
    _snap = _temp_snapshot_read()
    if _snap.get("temps"):
        d["sensors"]["temps"] = _snap["temps"]
        d["sensors"]["voltages"] = _snap["voltages"]
        cpu_temp = _snap.get("cpu_temp")
    # 风扇控制信息：优先系统风扇服务配置，其次 sysfs（不依赖任何外部应用）
    fan_info = {}
    # 1) 系统风扇服务配置（可选）—— 提供风扇名称/模式，并借 pwm_path 推断可写路径
    fc_raw = read_file("/vol2/@appconf/FanControlServer/config.json")
    if fc_raw:
        try:
            fc = json.loads(fc_raw)
            for fan in fc.get("fans", []):
                idx = fan.get("pwm_index")
                if not idx:
                    continue
                _hw = ""
                _ix = idx
                m = re.search(r"(/sys/class/hwmon/hwmon\d+)/pwm(\d+)", fan.get("pwm_path") or "")
                if m:
                    _hw = m.group(1)
                    _ix = int(m.group(2))
                fan_info[f"fan{idx}"] = {
                    "name": fan.get("name", f"风扇{idx}"),
                    "mode": fan.get("mode", ""),
                    "hwmon": _hw,
                    "idx": _ix,
                    "controllable": bool(_hw),
                }
        except (json.JSONDecodeError, ValueError):
            pass
    # 2) sysfs hwmon —— 不依赖芯片型号（it87/nct/fintek/winbond/asus 等皆可），
    #    枚举所有 hwmon 的 pwmN_enable 可写通道；遍历所有芯片、fan1-10，避免漏掉多芯片/集线器。
    #    复用 _enumerate_fans 保证与温控循环 / 风扇状态接口看到的风扇全集一致。
    for (_hw, _fi) in _enumerate_fans():
        _fk = f"fan{_fi}"
        _pe = read_file(f"{_hw}/pwm{_fi}_enable").strip()
        _pv = read_file(f"{_hw}/pwm{_fi}").strip()
        _controllable = bool(_pe)
        if _fk in fan_info:
            # 已知的风扇（来自系统风扇服务配置）：name/mode 用配置，
            # 但 hwmon/idx 一律以「实时枚举结果」为权威(优先级最高)。
            # 配置里写死的 pwm_path(如 hwmon3)会随内核重排失效，若仍优先用它，
            # GUI 滑块会拿到错误 hwmon → 调速请求命中不到真实通道(停在自动曲线35%)。
            fan_info[_fk]["hwmon"] = _hw
            fan_info[_fk]["idx"] = _fi
            fan_info[_fk]["controllable"] = fan_info[_fk].get("controllable") or _controllable
        else:
            # 仅 sysfs 暴露的风扇，用 sysfs 模式兜底
            _mm = {"0": "off", "1": "manual", "2": "auto"}
            fan_info[_fk] = {"name": f"风扇{_fi}", "mode": _mm.get(_pe, ""),
                             "hwmon": _hw, "idx": _fi, "controllable": _controllable}
        # PWM 占空比（0-255 → 百分比），不管装没装外部风扇服务都读
        if _pv and _fk in fan_info:
            try:
                fan_info[_fk]["pwm"] = round(int(_pv) / 255 * 100)
            except ValueError:
                pass
    if sens_j:
        try:
            j = json.loads(sens_j)
            _t, _v = _parse_sensors_all(j, _parse_mb_temp(j))
            d["sensors"]["temps"] = _t
            d["sensors"]["voltages"] = _v
            if cpu_temp is None:
                cpu_temp = _parse_cpu_temp(j)
        except (json.JSONDecodeError, ValueError):
            pass
    # 风扇卡片：唯一来源 = 本机可控制风扇全集（_enumerate_fans，与温控循环 / 风扇专页同一份）。
    # 仅枚举有 pwm 的通道，故华硕双芯片主板(asus-ec 无 pwm)不会生成幽灵卡；
    # 默认名带芯片前缀（如 nct6798·风扇1），从根上杜绝重名（论坛 #1 反馈：两个 FAN1）。
    # rpm 直接从同 hwmon 目录的 fan{idx}_input 读（移植 FanControlServerApp 同思路），不依赖 sensors 解析。
    for (_hw, _fi) in _enumerate_fans():
        _chip = read_file(f"{_hw}/name").strip() or os.path.basename(_hw)
        _pe = read_file(f"{_hw}/pwm{_fi}_enable").strip()
        _pv = read_file(f"{_hw}/pwm{_fi}").strip()
        _rpm_path = f"{_hw}/fan{_fi}_input"
        _rpm = 0
        if os.path.exists(_rpm_path):
            try:
                _rpm = int(read_file(_rpm_path).strip())
            except (ValueError, Exception):
                _rpm = 0
        _lab = _fan_label_for(_hw, _fi)
        display = _lab.get("name") or f"{_chip}·风扇{_fi}"
        _mm = {"0": "off", "1": "manual", "2": "auto"}
        d["sensors"]["fans"].append({
            "name": display,
            "label": _lab.get("name", ""),
            "voltage": _lab.get("voltage", ""),
            "rpm": _rpm,
            "stopped": _rpm < 1,
            "mode": _mm.get(_pe, ""),
            "pwm": round(int(_pv) / 255 * 100) if _pv else None,
            "controllable": bool(_pe),
            "hwmon": _hw,
            "idx": _fi,
            "has_tach": _rpm > 0,
            # 用户标注：该口接的是 2/3 针无转速反馈线风扇，读不到转速属正常（非空口/非故障）
            "no_tach": bool(_lab.get("no_tach")),
            "hidden": bool(_lab.get("hidden")),
        })
    # 合并风扇控速元数据（温控规则来源 / 逻辑模式 / 计算目标 / 目标占空比），
    # 供前端首屏即正确显示「温度联动控速」来源与逻辑模式（手动 / 自动·CPU·主板·硬盘温控），
    # 而非仅按硬件寄存器位误报。否则下拉框恒显「关闭（手动/默认）」、状态标签错显（#2/#3 回归）。
    try:
        _fs = get_fan_status()
        _fs_map = {}
        for _ff in _fs:
            _fs_map["%s::%d" % (_ff.get("hwmon"), _ff.get("idx"))] = _ff
        for _f in d["sensors"]["fans"]:
            _m = _fs_map.get("%s::%d" % (_f.get("hwmon"), _f.get("idx")))
            if not _m:
                continue
            _f["mode"] = _m.get("mode", _f.get("mode"))
            # pwm_mode 只有 get_fan_status() 会读 sysfs，首屏必须合并进来，
            # 否则前端 `${f.pwm_mode?...}` 判空 → PWM/DC 下拉框与「无反馈线+PWM」告警首屏不渲染。
            _f["pwm_mode"] = _m.get("pwm_mode")
            _f["rule_source"] = _m.get("rule_source")
            _f["rule"] = _m.get("rule")
            _f["computed_pwm"] = _m.get("computed_pwm")
            _f["target_pct"] = _m.get("target_pct")
            _f["has_curve"] = _m.get("has_curve")
            _f["manual_active"] = _m.get("manual_active")
            _f["active_mode"] = _m.get("active_mode")
    except Exception:
        pass
    d["cpu_temp"] = cpu_temp
    # 兼容旧字段
    d["temps"] = {t["name"]: t["value"] for t in d["sensors"]["temps"]}
    # 显卡：lspci 基础信息 + nvidia-smi / sysfs 补充显存/时钟/温度
    def _to_int(s):
        try:
            return int(str(s).split()[0])
        except Exception:
            return None
    def _to_float1(s):
        """转浮点并保留 1 位小数（用于功耗等带小数的读数），失败返回 None。"""
        try:
            return round(float(str(s).split()[0]), 1)
        except Exception:
            return None
    # AMD 显卡设备 ID → (显存位宽, 显存类型) 查表。
    # amdgpu 驱动无标准 sysfs 暴露位宽（只有 mem_info_vram_* 容量，位宽仅出现在带宽公式注释里），
    # 用 lspci 设备号查表是实用解法。覆盖常见 RX 5000/6000 系列（RDNA1/RDNA2 桌面独显）；
    # 未在表内的仍显示「—」，并提示型号未收录。
    # 数据来源：各卡官方规格（设备 ID 取自 lspci -nn，与 macOS「系统信息」设备 ID 一致）。
    _AMD_GPU_SPECS = {
        # Navi 23 (RDNA2)
        "73ff": (128, "GDDR6"),  # RX 6600 / 6600 XT
        "73ef": (128, "GDDR6"),  # RX 6650 XT
        "73e3": (128, "GDDR6"),  # Pro W6600
        # Navi 24 (RDNA2)
        "743f": (64, "GDDR6"),   # RX 6400
        "7440": (64, "GDDR6"),   # RX 6500 XT
        # Navi 22 (RDNA2)
        "73df": (192, "GDDR6"),  # RX 6700 XT（非 XT 10GB 版为 160-bit，此处按最常见 XT）
        # Navi 21 (RDNA2)
        "73bf": (256, "GDDR6"),  # RX 6800 / 6800 XT / 6900 XT
        "73af": (256, "GDDR6"),  # RX 6900 XT (XTXH)
        "73a5": (256, "GDDR6"),  # RX 6950 XT
        "73a3": (256, "GDDR6"),  # Pro W6800
        # Navi 10 (RDNA1)
        "731f": (256, "GDDR6"),  # RX 5700 / 5700 XT
        "7312": (256, "GDDR6"),  # Pro W5700
        "7310": (256, "GDDR6"),  # RX 5700 (Lite)
        # Navi 14 (RDNA1)
        "7340": (128, "GDDR6"),  # RX 5500 XT / 5300
        "7341": (128, "GDDR6"),  # Pro W5500
    }

    def _amd_bus_width(dev):
        """AMD 独显：按 lspci 设备 ID 查显存位宽与类型。返回 (bit_width, mem_type)，查不到为 (None,None)。"""
        if not dev:
            return (None, None)
        dev = str(dev).lower()
        if dev.startswith("0x"):
            dev = dev[2:]
        spec = _AMD_GPU_SPECS.get(dev)
        return spec if spec else (None, None)

    def _short_pci(bus_id):
        if not bus_id:
            return ""
        return ":".join(bus_id.split(":")[1:])  # 00000000:01:00.0 -> 01:00.0
    def _collect_nvidia():
        """NVIDIA 专用：一次性拿到型号/显存/核心频率/显存频率/温度，位宽与显存类型从 -q 补。无驱动返回 []。"""
        out = []
        smi = shell.run_cmd(["which", "nvidia-smi"], 2).strip()
        if not smi:
            return out
        q = shell.run_cmd([smi, "--query-gpu=index,pci.bus_id,name,memory.total,"
                     "clocks.current.graphics,clocks.current.memory,temperature.gpu,"
                     "power.draw,power.limit,driver_version", "--format=csv,noheader,nounits"], 8)
        if not q.strip():
            return out
        for line in q.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 8:
                continue
            idx, bus, name, mem, gclk, mclk, temp = parts[:7]
            pdraw = parts[7] if len(parts) > 7 else None
            plimit = parts[8] if len(parts) > 8 else None
            drv = parts[9] if len(parts) > 9 else None
            info = {"pci": bus, "name": name, "driver": "nvidia", "driver_ver": drv,
                    "memory_total": _to_int(mem), "core_clock": _to_int(gclk),
                    "mem_clock": _to_int(mclk), "temp": _to_int(temp),
                    "power_draw": _to_float1(pdraw), "power_cap": _to_float1(plimit)}
            # 位宽 / 显存类型：-q 补充（部分驱动字段缺失则留空，不报错）
            try:
                qd = shell.run_cmd([smi, "-q", "-i", idx], 8)
                bw = re.search(r"Bus Width\s*:\s*(\d+)\s*bit", qd, re.I)
                if bw:
                    info["bus_width"] = int(bw.group(1))
                mt = re.search(r"Memory Type\s*:\s*(\S+)", qd)
                if mt:
                    info["mem_type"] = mt.group(1)
            except Exception:
                pass
            out.append(info)
        return out
    def _gpu_temp_from_sysfs(pci):
        """按 PCI 地址在 /sys/class/drm/cardN/device/hwmon 读 GPU 温度（独显/AMD）。"""
        try:
            base = _short_pci(pci)
            for name in os.listdir("/sys/class/drm"):
                if not re.match(r"^card\d+$", name):
                    continue
                devdir = os.path.join("/sys/class/drm", name, "device")
                uevent = os.path.join(devdir, "uevent")
                if not os.path.exists(uevent):
                    continue
                data = open(uevent).read()
                mm = re.search(r"PCI_SLOT_NAME=(\S+)", data)
                if not mm:
                    continue
                dev = mm.group(1)
                if base and (base == _short_pci(dev) or dev.endswith(base) or base.endswith(_short_pci(dev))):
                    hdir = os.path.join(devdir, "hwmon")
                    if os.path.isdir(hdir):
                        for hw in sorted(os.listdir(hdir)):
                            for tf in ("temp1_input", "temp2_input", "temp_input"):
                                p = os.path.join(hdir, hw, tf)
                                if os.path.exists(p):
                                    try:
                                        return int(open(p).read().strip()) // 1000
                                    except Exception:
                                        pass
        except Exception:
            return None
        return None

    def _amd_read_clocks(pci):
        """AMD 独显：读核心频率(SCLK)/显存频率(MCLK)。优先 pp_dpm_sclk/mclk 当前档(*)，回退 hwmon freq1/freq2_input(Hz)。
        返回 (core_clock_MHz, mem_clock_MHz)，读不到为 None。amdgpu 无标准接口暴露显存位宽，位宽由调用方保持 None。"""
        core = mem = None
        try:
            base = _short_pci(pci)
            target = None
            for name in os.listdir("/sys/class/drm"):
                if not re.match(r"^card\d+$", name):
                    continue
                devdir = os.path.join("/sys/class/drm", name, "device")
                uevent = os.path.join(devdir, "uevent")
                if not os.path.exists(uevent):
                    continue
                mm = re.search(r"PCI_SLOT_NAME=(\S+)", open(uevent).read())
                if not mm:
                    continue
                dev = mm.group(1)
                if base and (base == _short_pci(dev) or dev.endswith(base) or base.endswith(_short_pci(dev))):
                    target = devdir
                    break
            if not target:
                return (None, None)
            # SCLK / MCLK 当前档（带 * 标记）
            sclk_f = os.path.join(target, "pp_dpm_sclk")
            if os.path.exists(sclk_f):
                for line in open(sclk_f).read().splitlines():
                    if "*" in line:
                        sm = re.search(r":\s*([\d.]+)\s*Mhz", line, re.I)
                        if sm:
                            core = int(round(float(sm.group(1))))
                            break
            mclk_f = os.path.join(target, "pp_dpm_mclk")
            if os.path.exists(mclk_f):
                for line in open(mclk_f).read().splitlines():
                    if "*" in line:
                        mm2 = re.search(r":\s*([\d.]+)\s*Mhz", line, re.I)
                        if mm2:
                            mem = int(round(float(mm2.group(1))))
                            break
            # 回退：hwmon freq1_input(GPU)/freq2_input(显存)，单位 Hz（值 >1e6 视为 Hz 再换算，避免个别内核直接给 MHz）
            if core is None or mem is None:
                hdir = os.path.join(target, "hwmon")
                if os.path.isdir(hdir):
                    for hw in sorted(os.listdir(hdir)):
                        if core is None:
                            f1 = os.path.join(hdir, hw, "freq1_input")
                            if os.path.exists(f1):
                                try:
                                    v = int(open(f1).read().strip())
                                    core = int(v / 1000000) if v > 1000000 else v
                                except Exception:
                                    pass
                        if mem is None:
                            f2 = os.path.join(hdir, hw, "freq2_input")
                            if os.path.exists(f2):
                                try:
                                    v = int(open(f2).read().strip())
                                    mem = int(v / 1000000) if v > 1000000 else v
                                except Exception:
                                    pass
        except Exception:
            pass
        return (core, mem)

    def _amd_read_power(pci):
        """AMD/Intel 独显功耗：读 amdgpu hwmon 的 power1_input(微瓦) 与 power1_cap(微瓦上限)。
        返回 (power_draw_W, power_cap_W)，读不到为 (None,None)。核显与 CPU 共用电源域，不单独统计。"""
        draw = cap = None
        try:
            base = _short_pci(pci)
            target = None
            for name in os.listdir("/sys/class/drm"):
                if not re.match(r"^card\d+$", name):
                    continue
                devdir = os.path.join("/sys/class/drm", name, "device")
                uevent = os.path.join(devdir, "uevent")
                if not os.path.exists(uevent):
                    continue
                mm = re.search(r"PCI_SLOT_NAME=(\S+)", open(uevent).read())
                if not mm:
                    continue
                dev = mm.group(1)
                if base and (base == _short_pci(dev) or dev.endswith(base) or base.endswith(_short_pci(dev))):
                    target = devdir
                    break
            if not target:
                return (None, None)
            hdir = os.path.join(target, "hwmon")
            if os.path.isdir(hdir):
                for hw in sorted(os.listdir(hdir)):
                    if draw is None:
                        p1 = os.path.join(hdir, hw, "power1_input")
                        if os.path.exists(p1):
                            try:
                                v = int(open(p1).read().strip())
                                draw = round(v / 1000000.0, 1)  # 微瓦 → 瓦
                            except Exception:
                                pass
                    if cap is None:
                        p1c = os.path.join(hdir, hw, "power1_cap")
                        if os.path.exists(p1c):
                            try:
                                v = int(open(p1c).read().strip())
                                cap = round(v / 1000000.0, 1)
                            except Exception:
                                pass
        except Exception:
            pass
        return (draw, cap)

    def _igpu_core_freq():
        """Intel/AMD 核显核心频率（MHz），读 /sys/class/drm/card*/device/drm/card*/gt_cur_freq_mhz。无则 None。"""
        try:
            for name in sorted(os.listdir("/sys/class/drm")):
                if not re.match(r"^card\d+$", name):
                    continue
                devdir = os.path.join("/sys/class/drm", name, "device")
                cand = os.path.join(devdir, "drm", name, "gt_cur_freq_mhz")
                cand2 = os.path.join(devdir, "gt_cur_freq_mhz")
                p = cand if os.path.exists(cand) else (cand2 if os.path.exists(cand2) else None)
            if p:
                return _to_int(open(p).read().strip())
        except Exception:
            return None
        return None

    def _pci_dev_from_sysfs(pci):
        """UEVENT 加固：lspci 在装了 pciids 数据库时可能只打印设备全名、不打印 [vendor:device]，
        导致上面正则抓不到设备号（dev 变空），AMD 位宽查表随之失效。
        改从内核 /sys/bus/pci/devices/*:<pci>/uevent 直接读 PCI_ID，格式恒为 'vendor:device'，
        不受 pciids 影响，最稳。返回设备号(小写)或 None。"""
        if not pci:
            return None
        try:
            import glob
            for uevent in glob.glob("/sys/bus/pci/devices/*:%s/uevent" % pci):
                for line in open(uevent):
                    if line.startswith("PCI_ID="):
                        parts = line.strip().split("=", 1)[1].split(":", 1)
                        if len(parts) == 2 and parts[1]:
                            return parts[1].lower()
        except Exception:
            return None
        return None

    def _lshw_video_map():
        """第三重兜底：lspci -nn 正则 + uevent PCI_ID 都拿不到设备号时，
        用 lshw -numeric（强制打印 [vendor:dev]，不受 pciids 库影响）按 PCI 地址补设备号与名称。
        仅在前面两路都失败才调用，避免 lshw 的耗时扫描进入常规轮询路径。结果按短 PCI 地址缓存。"""
        cache = getattr(_lshw_video_map, "_cache", {})
        if cache:
            return cache
        try:
            out = shell.sudo_cmd(["lshw", "-C", "video", "-numeric"], 10)
            blocks, cur = [], None
            for line in out.splitlines():
                s = line.strip()
                if s.startswith("*-"):
                    cur = {}
                    blocks.append(cur)
                    continue
                if cur is None:
                    continue
                if s.startswith("bus info:"):
                    m = re.search(r"pci@(?:[0-9a-f]{4}:)?([0-9a-f]{2}:[0-9a-f]{2}\.\d)", s, re.I)
                    if m:
                        cur["pci"] = m.group(1)
                elif s.startswith("product:"):
                    m = re.search(r"\[([0-9a-f]{4}):([0-9a-f]{4})\]", s, re.I)
                    if m:
                        cur["vendor"] = m.group(1).lower()
                        cur["dev"] = m.group(2).lower()
                    nm = s.split(":", 1)[1].strip()
                    nm = re.sub(r"\s*\[[0-9a-f]{4}:[0-9a-f]{4}\]\s*$", "", nm, flags=re.I)
                    cur["name"] = nm
            for b in blocks:
                if b.get("pci") and "dev" in b:
                    cache[b["pci"]] = (b.get("vendor", ""), b["dev"], b.get("name", ""))
            _lshw_video_map._cache = cache
        except Exception:
            pass
        return cache

    def _parse_pcie(out):
        """从 lspci -vvv 输出解析 PCIe 协商速率/通道。返回 dict 或 None。"""
        if not out:
            return None
        def _gen(gts):
            try:
                g = float(gts)
            except Exception:
                return None
            return {2.5: "1.0", 5.0: "2.0", 8.0: "3.0", 16.0: "4.0",
                    32.0: "5.0", 64.0: "6.0"}.get(g)
        cap = re.search(r"LnkCap:[^\n]*?Speed\s*([\d.]+)\s*GT/s[^\n]*?Width\s*(x\d+)", out)
        sta = re.search(r"LnkSta:[^\n]*?Speed\s*([\d.]+)\s*GT/s[^\n]*?Width\s*(x\d+)", out)
        if not cap and not sta:
            return None
        return {"gen_cap": _gen(cap.group(1)) if cap else None,
                "width_cap": cap.group(2) if cap else None,
                "gen_sta": _gen(sta.group(1)) if sta else None,
                "width_sta": sta.group(2) if sta else None}

    # _clean_gpu_name 已提升到模块级（见下方函数定义），此处不再内联定义。

    lspci = shell.run_cmd(["lspci", "-nn"], 5)
    gpus = []
    has_igpu = False
    for line in lspci.splitlines():
        if not re.search(r"VGA compatible controller|3D controller|Display controller", line, re.I):
            continue
        # PCI 地址（行首 xx:xx.x）
        pci = ""
        pm = re.match(r"^([0-9a-f]{2}:[0-9a-f]{2}\.\d)", line)
        if pm:
            pci = pm.group(1)
        # 形如：… VGA compatible controller [0300]: Intel Corporation UHD Graphics 630 [8086:3e90] (rev 02)
        m = re.search(r"controller\s*\[[0-9a-f]{4}\]:\s*(.+?)\s*\[([0-9a-f]{4}):([0-9a-f]{4})\]", line)
        if m:
            name = m.group(1).strip()
            vendor = m.group(2).lower()
            dev = m.group(3).lower()
            # pciids 无名字时 lspci 只打印 "Device"，用厂商+设备号兜底
            if name == "Device" or name.endswith(" Corporation Device"):
                name = {"8086": "Intel 核显", "10de": "NVIDIA 显卡",
                         "1002": "AMD 显卡"}.get(vendor, "显卡") + " (设备 %s)" % dev
        else:
            vendor = ""; name = line.strip(); dev = _pci_dev_from_sysfs(pci)
            # 第三重兜底：lspci 正则 + uevent 都拿不到设备号时，用 lshw -numeric 补
            if not dev:
                lw = _lshw_video_map().get(pci)
                if lw:
                    vendor, dev, lw_name = lw
                    if lw_name and not lw_name.lower().startswith("device") and lw_name != name:
                        name = lw_name
        if vendor == "8086" or "intel" in name.lower():
            label = "核显"; has_igpu = True
        elif vendor == "1002" and re.search(r"radeon|graphics|apu|vega|renoir|cezanne|phoenix|raphael", name, re.I):
            label = "核显"; has_igpu = True
        else:
            label = "独显"
        # 驱动（Kernel driver in use）+ PCIe 协商通道（lspci -vvv 一次拿全）
        driver = ""
        pcie = None
        if pci:
            k = shell.run_cmd(["lspci", "-vvv", "-s", pci], 3)
            km = re.search(r"Kernel driver in use:\s*(\S+)", k)
            if km:
                driver = km.group(1)
            pcie = _parse_pcie(k)
        gpus.append({"type": label, "name": name, "vendor": vendor, "dev": dev,
                     "pci": pci, "driver": driver, "pcie": pcie, "vram": None,
                     "memory_total": None, "mem_type": None, "core_clock": None,
                     "mem_clock": None, "bus_width": None, "temp": None,
                     "power_draw": None, "power_cap": None})
    # NVIDIA：用 nvidia-smi 按 PCI 地址匹配补充
    for info in _collect_nvidia():
        match = None
        for g in gpus:
            if g["vendor"] == "10de" and g["pci"] and (
                info["pci"].endswith(g["pci"]) or g["pci"].endswith(_short_pci(info["pci"]))):
                match = g; break
        if not match:
            match = {"type": "独显", "name": info.get("name", "NVIDIA 显卡"),
                     "vendor": "10de", "dev": "", "pci": _short_pci(info.get("pci", "")),
                     "driver": "", "pcie": None, "vram": None, "memory_total": None,
                     "mem_type": None, "core_clock": None, "mem_clock": None,
                     "bus_width": None, "temp": None, "power_draw": None, "power_cap": None}
            gpus.append(match)
        for k in ("name", "memory_total", "mem_type", "core_clock",
                  "mem_clock", "bus_width", "temp", "driver", "driver_ver",
                  "power_draw", "power_cap"):
            if info.get(k) is not None:
                match[k] = info[k]
    # 非 NVIDIA 显卡：显存/温度补充
    cpu_temp = d.get("cpu_temp")
    for g in gpus:
        if g["vendor"] == "10de":
            continue
        # 驱动版本：开源 amdgpu/i915 随内核发布、无独立版本号。
        # 优先 modinfo 的 version/vermagic（标准 Linux 有），飞牛精简系统无 modinfo，回退内核版本。
        if g["driver"]:
            mv = shell.run_cmd(["modinfo", g["driver"]], 3)
            mver = re.search(r"^(version|vermagic):\s*(\S+)", mv, re.M)
            if mver:
                g["driver_ver"] = mver.group(2)
            else:
                kv = shell.run_cmd(["uname", "-r"], 2).strip()
                g["driver_ver"] = kv.split()[0] if kv else None
        if g["type"] == "核显":
            g["vram"] = "共享系统内存"
            # 核显核心频率（真实可读，非独显才有）
            g["core_clock"] = _igpu_core_freq()
            # 核显与 CPU 同封装，温度取 CPU 温度
            if cpu_temp is not None:
                g["temp"] = cpu_temp
        else:
            # 独显（AMD 等）：lspci 显存区间作为显存容量兜底
            if g["pci"] and g["memory_total"] is None:
                v = shell.run_cmd(["lspci", "-v", "-s", g["pci"]], 3)
                vm = re.search(r"prefetchable.*?\[size=([^\]]+)\]", v, re.I)
                if vm:
                    g["vram"] = vm.group(1).strip()
            # 温度：sysfs hwmon 按 PCI 匹配
            if g["temp"] is None and g["pci"]:
                t = _gpu_temp_from_sysfs(g["pci"])
                if t is not None:
                    g["temp"] = t
            # AMD 独显：补核心频率 / 显存频率（amdgpu sysfs）；显存位宽/类型用设备 ID 查表
            if g["vendor"] == "1002" and g["pci"]:
                cc, mc = _amd_read_clocks(g["pci"])
                if cc is not None:
                    g["core_clock"] = cc
                if mc is not None:
                    g["mem_clock"] = mc
                bw, mt = _amd_bus_width(g["dev"])
                if bw is not None:
                    g["bus_width"] = bw
                if mt is not None and g["mem_type"] is None:
                    g["mem_type"] = mt
                pd, pc = _amd_read_power(g["pci"])
                if pd is not None:
                    g["power_draw"] = pd
                if pc is not None:
                    g["power_cap"] = pc
    # 兜底：插了独显后主板 BIOS 常自动禁用核显，lspci 扫不到。
    # 区分「CPU 本就没核显」和「CPU 带核显但被 BIOS 禁用」：前者直说无核显，后者提示未启用，避免误导。
    if not has_igpu:
        cm = d.get("cpu_model", "") or ""
        m = re.search(r"i[3579]-(\d{4,5})([A-Z]*)", cm)
        amd_g = re.search(r"Ryzen \d+ \d{3,4}G\b", cm, re.I)
        if (m and "F" not in m.group(2)) or amd_g:
            gpus.append({"type": "核显", "name": "未启用（BIOS 可能已禁用）",
                          "vendor": "", "dev": "", "pci": "", "driver": "", "pcie": None, "vram": "",
                          "memory_total": None, "mem_type": None, "core_clock": None,
                          "mem_clock": None, "bus_width": None, "temp": None,
                          "power_draw": None, "power_cap": None})
        else:
            gpus.append({"type": "无核显", "name": "",
                          "vendor": "", "dev": "", "pci": "", "driver": "", "pcie": None, "vram": "",
                          "memory_total": None, "mem_type": None, "core_clock": None,
                          "mem_clock": None, "bus_width": None, "temp": None,
                          "power_draw": None, "power_cap": None})
    # 统一精简显卡显示名：主名去厂商前缀+取方括号市场名，架构代号降为副标题
    for _g in gpus:
        if _g.get("name"):
            _sn, _sa = _clean_gpu_name(_g["name"])
            _g["name_full"] = (_sn + (" (" + _sa + ")" if _sa else "")) or _g["name"]
            _g["name"] = _sn
            _g["name_arch"] = _sa
    d["gpus"] = gpus

    d["nics"] = get_network_nics()
    # 主板 / 内存品牌型号（dmidecode），失败不影响其它采集
    try:
        d["board"] = get_board()
    except Exception:
        d["board"] = {"manufacturer": "", "product": "", "version": ""}
    try:
        d["memory_modules"] = get_memory_modules()
    except Exception:
        d["memory_modules"] = {"modules": [], "total_gb": 0, "slots": 0, "brand_summary": ""}
    return d

def format_uptime(s):
    d = int(s // 86400); h = int((s % 86400) // 3600); m = int((s % 3600) // 60)
    if d > 0:
        return f"{d}天{h}小时{m}分"
    return f"{h}小时{m}分"

def fmt_kb(kb):
    if kb >= 1048576:
        return f"{kb/1048576:.1f} GB"
    if kb >= 1024:
        return f"{kb/1024:.0f} MB"
    return f"{kb} KB"
