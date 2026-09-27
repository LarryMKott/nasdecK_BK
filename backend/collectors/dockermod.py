# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend import shell
from backend.caching import _ttl_cache
import json
import os
import re
import time


# ===================== 采集：Docker =====================
def _listening_ports_in_netns(pid):
    """读取某 PID 网络命名空间内处于 LISTEN 的 TCP 端口（容器内视角）"""
    ports = set()
    for f in (f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"):
        try:
            with open(f) as fh:
                next(fh, None)
                for line in fh:
                    fld = line.split()
                    if len(fld) >= 4 and fld[3] == "0A":  # 0A = LISTEN
                        ports.add(int(fld[1].split(":")[1], 16))
        except Exception:
            continue
    return sorted(ports)

def _listening_ports_for_pids(pids):
    """汇总一组进程拥有的、处于 LISTEN 的 TCP 端口（host 网络模式按进程归属判定）"""
    inodes = set()
    for pid in pids:
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    link = os.readlink(f"/proc/{pid}/fd/{fd}")
                except Exception:
                    continue
                if link.startswith("socket:["):
                    inodes.add(link[8:-1])
        except Exception:
            continue
    ports = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(f) as fh:
                next(fh, None)
                for line in fh:
                    fld = line.split()
                    if len(fld) < 10:
                        continue
                    if fld[3] == "0A" and fld[9] in inodes:
                        ports.add(int(fld[1].split(":")[1], 16))
        except Exception:
            continue
    return sorted(ports)

def _container_pids(name):
    """用 docker top 取容器内所有进程 PID（host 网络模式端口归属用）"""
    pids = []
    out = shell.sudo_cmd(["docker", "top", name], 5, quiet=True)
    for line in out.splitlines()[1:]:  # 跳过表头
        f = line.split()
        if len(f) >= 2 and f[1].isdigit():
            pids.append(int(f[1]))
    return pids

def _detect_ports(meta, running=False):
    """根据 docker inspect 信息自动探测端口号（兼容 bridge 发布端口 / host 模式真实监听端口）"""
    netmode = (meta.get("netmode") or "bridge")
    ports_map = meta.get("ports") or {}
    pid = meta.get("pid") or 0
    parts = []
    # 1) 已发布端口映射（bridge / 自定义网络，-p 映射），去重（IPv4/IPv6 双绑定）
    if ports_map:
        seen = set()
        for cport, bindings in ports_map.items():
            if bindings:
                for b in bindings:
                    hip = (b.get("HostIp") or "").strip()
                    hport = b.get("HostPort", "")
                    if hip and hip not in ("0.0.0.0", "::", "::/0"):
                        s = f"{hip}:{hport}→{cport}"
                    else:
                        s = f"{hport}→{cport}"
                    if s not in seen:
                        seen.add(s)
                        parts.append(s)
            else:
                s = f"{cport} (未发布)"
                if s not in seen:
                    seen.add(s)
                    parts.append(s)
    # 2) host 网络模式：端口即主机端口，按进程归属探测真实监听端口
    if netmode.startswith("host"):
        if not running:
            parts.append("容器停止不检测端口")
        else:
            pids = _container_pids(meta.get("name", ""))
            if pids:
                for p in _listening_ports_for_pids(pids):
                    parts.append(f"{p}/tcp")
            else:
                parts.append("host 网络")
    # 3) 非 host 且无发布端口：探测容器内部监听端口（提示性）
    if not ports_map and not netmode.startswith("host") and pid:
        for p in _listening_ports_in_netns(pid):
            parts.append(f"{p}/tcp (容器内部)")
    if not parts:
        return "-"
    return "  ".join(parts)

def _cn_status(status):
    """把 docker 的英文状态串转换为中文（含运行时长）"""
    s = (status or "").strip()
    low = s.lower()
    if low.startswith("up"):
        # 运行中：Up 3 days / Up 5 hours / Up 30 seconds / Up About a minute
        body = s[2:].strip()
        body = re.split(r"[\(（]", body)[0].strip()  # 去掉 (health: ...) 等括号
        repl = [("about a minute", "约1分钟"), ("about an hour", "约1小时"),
                ("days", "天"), ("day", "天"), ("hours", "小时"), ("hour", "小时"),
                ("minutes", "分钟"), ("minute", "分钟"), ("seconds", "秒"), ("second", "秒")]
        cn = body
        for a, b in repl:
            cn = re.sub(r"\b" + re.escape(a) + r"\b", b, cn, flags=re.I)
        cn = re.sub(r"\ba\b", "1", cn)  # 兜底 a day / a hour
        return "已运行 " + cn
    m = re.search(r"exited.*?(\d+)\s*(day|days|hour|hours|minute|minutes|second|seconds)", low)
    if m:
        num = m.group(1)
        unit = m.group(2)
        ucn = {"day": "天", "days": "天", "hour": "小时", "hours": "小时",
               "minute": "分钟", "minutes": "分钟", "second": "秒", "seconds": "秒"}[unit]
        return "已停止 (停于 %s%s前)" % (num, ucn)
    if low.startswith("created"):
        return "已创建未启动"
    return s  # 兜底原串

def _parse_docker_pct(s):
    """'12.34%' -> 12.34 ; 其它(None/空/非数字) -> None"""
    if not s or not isinstance(s, str):
        return None
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else None

def _host_cpu_threads():
    """逻辑核数（含超线程），用于把 docker 单核% 归一化为整机%所占比例。
    读取 /proc/cpuinfo 的 processor 行数，失败兜底返回 1。"""
    try:
        n = 0
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("processor"):
                    n += 1
        return n or 1
    except Exception:
        return 1

def _docker_size_to_bytes(s):
    """'120MiB' / '1.2kB' -> int 字节数 ; 无效 -> None"""
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"^\s*([\d.]+)\s*([kKmMgGtT]?)i?B\s*$", s.strip())
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").upper()
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return int(num * mult.get(unit, 1))

def _split_netio(s):
    """'1.2kB / 3.4kB' -> (rx_bytes, tx_bytes)；无效 -> (None, None)"""
    if not s or not isinstance(s, str):
        return (None, None)
    parts = [p.strip() for p in s.split("/") if p.strip()]
    rx = _docker_size_to_bytes(parts[0]) if len(parts) >= 1 else None
    tx = _docker_size_to_bytes(parts[1]) if len(parts) >= 2 else None
    return (rx, tx)

# `docker stats --no-stream` 给出的是「容器启动以来累计收/发字节」，并非实时速率。
# 若直接把累计字节当速率显示，会冒出「14 GB/s」这种假数字（远超 1Gbps 网卡上限）。
# 这里用两次采集的差值 / 时间差算出真实速率(B/s)；首采样或容器重启(累计值回落)时返回 (None, None)。
_DOCKER_NET_PREV = {}

def _docker_net_rate(name, rx, tx):
    """返回 (rx_rate_Bps, tx_rate_Bps)；首采样或容器重启(累计值回落)时返回 (None, None)。"""
    now = time.time()
    prev = _DOCKER_NET_PREV.get(name)
    rate = (None, None)
    if prev and prev["rx"] is not None and prev["tx"] is not None \
            and rx is not None and tx is not None:
        dt = now - prev["ts"]
        if 0 < dt < 600 and rx >= prev["rx"] and tx >= prev["tx"]:
            rate = ((rx - prev["rx"]) / dt, (tx - prev["tx"]) / dt)
    _DOCKER_NET_PREV[name] = {"rx": rx, "tx": tx, "ts": now}
    return rate

@_ttl_cache(5)
def get_docker():
    """统计 Docker 容器数（运行中/总数），并自动探测每个容器真实监听端口与资源占用"""
    try:
        out = shell.sudo_cmd(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"], 8)
        containers = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            name = parts[0].strip() if len(parts) > 0 else ""
            status = parts[1].strip() if len(parts) > 1 else ""
            image = parts[2].strip() if len(parts) > 2 else ""
            running = status.lower().startswith("up") or "running" in status.lower()
            containers.append({"name": name, "status": status, "image": image, "ports": "-", "running": running,
                               "mem": None, "cpu": None, "mem_pct": None, "mem_bytes": None,
                               "net_rx": None, "net_tx": None, "runtime": _cn_status(status)})
        # 批量 inspect 取端口 / pid / 网络模式，自动探测端口
        try:
            ids = shell.sudo_cmd(["docker", "ps", "-a", "-q"], 8).split()
            if ids:
                raw = shell.sudo_cmd(["docker", "inspect"] + ids, 15)
                data = json.loads(raw) if raw.strip() else []
                info = {}
                for c in data:
                    nm = (c.get("Name") or "").lstrip("/")
                    info[nm] = {
                        "netmode": (c.get("HostConfig", {}).get("NetworkMode") or "bridge"),
                        "pid": c.get("State", {}).get("Pid", 0),
                        "ports": c.get("NetworkSettings", {}).get("Ports") or {},
                        "running": c.get("State", {}).get("Running", False),
                        "name": nm,
                    }
                for c in containers:
                    meta = info.get(c["name"])
                    if meta:
                        c["ports"] = _detect_ports(meta, meta.get("running", False))
        except Exception:
            # 兜底：用 docker ps 的 Ports 字段
            try:
                out2 = shell.sudo_cmd(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Ports}}"], 8)
                pm = {}
                for line in out2.splitlines():
                    line = line.strip()
                    if not line or "|" not in line:
                        continue
                    n, p = line.split("|", 1)
                    pm[n.strip()] = p.strip()
                for c in containers:
                    if not c["ports"] or c["ports"] == "-":
                        c["ports"] = pm.get(c["name"], "-")
            except Exception:
                pass
        # 运行中容器的资源占用（docker stats 仅对运行中容器有数据）：CPU% / 内存% / 内存字节 / 网络 RX-TX
        try:
            # fnOS 的 docker stats {{.CPUPerc}} 返回的是「单逻辑核百分比」（未除总核数），
            # 而飞牛原生 Docker 面板显示的是「整机占比百分比」。
            # 例：G5400 双核四线程，容器占满 1 核时 {{.CPUPerc}}≈100%，飞牛原生≈25%。
            # 为与飞牛原生一致，这里把单核%除以逻辑核数做归一化（_threads）。
            _threads = _host_cpu_threads()
            stat = shell.sudo_cmd(["docker", "stats", "--no-stream", "--format",
                             "{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}|{{.NetIO}}"], 12)
            for line in stat.splitlines():
                line = line.strip()
                if not line or "|" not in line:
                    continue
                parts = line.split("|")
                if len(parts) < 5:
                    continue
                cname, cpu_s, memp_s, mem_s, net_s = parts[0], parts[1], parts[2], parts[3], parts[4]
                cname = cname.strip()
                rx, tx = _split_netio(net_s)
                rrx, rtx = _docker_net_rate(cname, rx, tx)
                for c in containers:
                    if c["name"] == cname:
                        _raw = _parse_docker_pct(cpu_s)
                        c["cpu"] = _raw / _threads if (_raw is not None and _threads > 1) else _raw
                        c["cpu_raw"] = _raw  # 保留归一化前的单核%，便于排查
                        c["mem_pct"] = _parse_docker_pct(memp_s)
                        c["mem"] = mem_s.strip()
                        c["mem_bytes"] = _docker_size_to_bytes(mem_s.split("/")[0].strip()) if "/" in mem_s else _docker_size_to_bytes(mem_s.strip())
                        c["net_rx"] = rx
                        c["net_tx"] = tx
                        c["net_rx_rate"] = rrx
                        c["net_tx_rate"] = rtx
                        break
        except Exception:
            pass
        # 停止的容器且无端口配置 → 标注「容器停止不检测端口」，避免与「运行中但无端口」的 "-" 混淆
        for c in containers:
            if c["ports"] in ("-", "") and not c["running"]:
                c["ports"] = "容器停止不检测端口"
        running = sum(1 for c in containers if c["running"])
        return {"running": running, "total": len(containers), "containers": containers, "ok": True}
    except Exception:
        return {"running": 0, "total": 0, "containers": [], "ok": False}
