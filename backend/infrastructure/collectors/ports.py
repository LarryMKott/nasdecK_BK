# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.command import cmd_runner as shell
import ipaddress
import json
import os
import re
import shutil
import time


# ===================== 采集：端口占用 =====================
def _docker_port_map():
    """返回 {宿主端口(str): 容器名}：仅用 docker ps 解析端口映射，轻量。"""
    m = {}
    try:
        out = shell.sudo_cmd(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Ports}}"], 8)
        for line in out.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            name, ports = line.split("|", 1)
            name = name.strip()
            ports = ports.strip()
            if not name or not ports:
                continue
            for token in ports.split(","):
                token = token.strip()
                if not token:
                    continue
                mm = re.match(r'(?:\d+\.\d+\.\d+\.\d+:|:\[[^\]]+\]:|^)(\d+)(?:->\d+/(?:tcp|udp))?', token)
                if mm:
                    m[mm.group(1)] = name
    except Exception:
        pass
    return m

# 监听地址的「可达范围」三级（比原先「是不是 0.0.0.0」准得多）：
#   lan    = 局域网里任何机器都能连：绑所有网卡(0.0.0.0/[::]/*)、绑物理网卡 IP、
#            网段广播(192.168.50.255)、组播(239.255.255.250 SSDP / mDNS)
#   docker = 只有宿主机与容器网络能连：docker 网桥地址及其网段、网桥广播
#   lo     = 仅本机：127.0.0.1 / ::1，出了这台 NAS 谁都连不上
# 判定**不写死任何 IP 段**——读一遍 `ip -o addr show` 把每张网卡归类：
# 飞牛上 lo=回环、docker0/br-*=容器网桥、其余(含 OVS 桥 eno1-ovs)=物理口。
# 这样 DHCP 换 IP、新加网桥/网段都能自动跟上。
_DOCKER_IFACE_RE = re.compile(r'^(docker|br-|veth|virbr|podman|cni|flannel|cali|tunl)', re.I)

def _local_net_zones():
    """读 ip addr，返回 (addr2zone, nets)：

    - addr2zone = {本机地址(str): "lo"|"docker"|"lan"}
    - nets      = [(ip_network, zone)] 按前缀长度**降序**（先匹配更具体的网段），
                  用于判定广播 / 组播 / 网段内其它不在本机地址表里的地址。
    读不到（命令缺失/解析失败）就返回空表，调用方 _addr_zone 仍有 loopback 硬兜底。
    """
    addr2zone, nets = {}, []
    binp = shutil.which("ip") or "/usr/sbin/ip"
    out = shell.run_cmd([binp, "-o", "addr", "show"], 8)
    if not out.strip() and binp != "/sbin/ip":
        out = shell.run_cmd(["/sbin/ip", "-o", "addr", "show"], 8)
    for line in out.splitlines():
        # 形如：2: eno1-ovs    inet 192.168.50.158/24 brd 192.168.50.255 scope global ...
        # 网卡名可能带对端后缀（veth123@if4），故 @ 后一律丢掉。
        m = re.match(r'^\d+:\s+([^:\s@]+)(?:@\S+)?\s+(inet6?)\s+(\S+)', line)
        if not m:
            continue
        iface, cidr = m.group(1), m.group(3).split("%", 1)[0]
        if "/" not in cidr:
            continue
        if _DOCKER_IFACE_RE.match(iface):
            zone = "docker"
        elif iface == "lo":
            zone = "lo"
        else:
            zone = "lan"
        try:
            ip_s, plen = cidr.split("/", 1)
            ip = ipaddress.ip_address(ip_s)
            net = ipaddress.ip_network(cidr, strict=False)
            plen = int(plen)
        except Exception:
            continue
        addr2zone[str(ip)] = zone
        if zone != "lo":          # 回环段(127/8)太大，别让它参与网段匹配
            nets.append((plen, net, zone))
    nets.sort(key=lambda t: -t[0])
    return addr2zone, [(n, z) for _, n, z in nets]

def _addr_zone(addr, zones):
    """给一个监听地址判可达范围，返回 "lan"/"docker"/"lo"；判不出返回 ""（前端不标徽章）。"""
    a = (addr or "").strip()
    if a.startswith("[") and a.endswith("]"):
        a = a[1:-1]                       # ss 的 IPv6 写法 [::]:80 → ::
    if a in ("0.0.0.0", "::", "*"):
        return "lan"                      # 绑所有网卡 = 全通
    a = a.split("%", 1)[0]                # 去 IPv6 的 %iface 后缀
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return ""
    # loopback 硬编码在前：即便 ip addr 没读出来也能判准（这是最要紧的一类）。
    if ip.is_loopback:
        return "lo"
    addr2zone, nets = zones
    z = addr2zone.get(str(ip))
    if z:
        return z
    for net, zone in nets:
        try:
            if ip in net:
                return zone
        except Exception:
            continue
    if ip.is_multicast:
        return "lan"                      # 239.x / ff0x:: 组播：本网段内可收到
    return ""

# 「释放端口」禁止 kill 的系统关键进程（短进程名 /proc/<pid>/comm）
_PORT_PROTECTED = ("systemd", "init", "dockerd", "containerd", "containerd-shim",
                   "sshd", "dbus-daemon", "udevd", "trimenv", "trim-cli",
                   "nginx", "cron", "rsyslogd", "login", "agetty")

# 系统服务中文说明：process(comm 短名) -> 说明（让用户一眼看懂「这是飞牛的什么服务」，
# 而不是只看一个看不懂的进程名）。应用中心的 app 默认走飞牛网关 unix socket，不占 TCP/UDP
# 端口，故不在此列；此处列出的端口绝大多数是飞牛系统自带服务。
_SERVICE_DESC = {
    "smbftpd": "飞牛文件共享 (SMB)",
    "smbd": "Samba 文件共享",
    "nmbd": "NetBIOS 名称服务 (SMB 网络浏览)",
    "wsdd": "SMB 网络发现 (WS-Discovery)",
    "sshd": "SSH 远程登录",
    "nginx": "飞牛 Web 管理后台",
    "rpcbind": "RPC 端口映射服务",
    "rpc.statd": "NFS 文件锁服务",
    "mountd": "NFS 挂载服务",
    "nfsd": "NFS 文件服务",
    "avahi-daemon": "mDNS / Bonjour 服务发现",
    "chronyd": "系统时间同步 (NTP)",
    "ntpd": "系统时间同步 (NTP)",
    "dnsmasq": "DNS / DHCP 服务",
    "systemd-resolved": "系统 DNS 解析",
    "dockerd": "Docker 引擎",
    "containerd": "Docker 容器运行时",
    "docker-proxy": "Docker 端口映射代理",
    "redis-server": "Redis 缓存服务",
    "mysqld": "MySQL 数据库",
    "postgres": "PostgreSQL 数据库",
    "mongod": "MongoDB 数据库",
    "memcached": "Memcached 缓存",
    "openvpn": "VPN (OpenVPN)",
    "wireguard": "VPN (WireGuard)",
    # 飞牛系统自带服务（进程名与上面不同的派生写法）
    "rpc.mountd": "NFS 挂载服务",
    "minidlnad": "DLNA 媒体服务",
    "wsdd2": "SMB 网络发现 (WS-Discovery)",
    "upnp_service": "UPnP 即插即用服务",
    "trim-media": "飞牛媒体服务",
    "fnpackup": "飞牛数据备份服务",
    "webdav": "WebDAV 文件服务",
    "go2rtc": "监控 / 媒体流服务",
    "pxy": "飞牛网关反向代理",
    "NetworkManager": "网络管理服务",
    # 实测 158 上仍会出现、但 ss 报的进程名与真正服务名有出入的几条
    "cloud_storage_dav": "飞牛云端存储 (WebDAV)",
    "cloud_storage_d": "飞牛云端存储 (WebDAV)",
    "minidlna": "DLNA 媒体服务",
    "rpcbind": "RPC 端口映射服务",
    "trim_connect": "飞牛内网穿透 / 远程访问",
    "docker-proxy": "Docker 端口映射代理",
}

# 内核态 socket 的端口反查。`ss -p` 对**内核线程**持有的 socket 不给 users 段
# （nfsd / lockd 是内核线程 [nfsd]，没有 cmdline/exe），于是拿不到 PID 与进程名，
# 界面上只剩一个「?」。这类端口只能靠**端口号**反查身份——`rpcinfo -p` 能给出
# 2049→nfs、41750→nlockmgr（158 实测全机只有这两个端口如此）。
_RPC_PORT_CACHE = {"t": 0.0, "v": None}

def _rpc_port_map():
    """{端口(str): RPC 服务名}，带缓存；命令缺失/超时/解析失败一律返回 {}。"""
    now = time.time()
    c = _RPC_PORT_CACHE
    if c["v"] is not None and now - c["t"] <= _APP_IDS_TTL:
        return c["v"]
    m = {}
    try:
        exe = shutil.which("rpcinfo") or "/usr/sbin/rpcinfo"
        out = shell.run_cmd([exe, "-p"], 8)
        for line in out.splitlines():
            # 形如：100003    4   tcp    2049  nfs
            parts = line.split()
            if len(parts) < 5 or not parts[0].isdigit() or not parts[3].isdigit():
                continue
            # 同端口多服务时保留首个（2049 会先报 nfs 再报 nfs_acl，nfs 才是主服务）
            m.setdefault(parts[3], parts[4])
    except Exception:
        pass
    if m or c["v"] is None:
        c["t"] = now
        c["v"] = m
    return m

# RPC 服务名 → 中文说明（飞牛上常见的就是 NFS 家族）
_RPC_DESC = {
    "nfs": "NFS 文件共享（内核服务）",
    "nfs_acl": "NFS 访问控制（内核服务）",
    "nlockmgr": "NFS 文件锁（内核服务）",
    "mountd": "NFS 挂载服务",
    "portmapper": "RPC 端口映射服务",
    "status": "NFS 状态监控",
    "rquotad": "NFS 磁盘配额",
}

_APP_IDS_CACHE = {"t": 0.0, "v": None}
_APP_IDS_TTL = 300

# appid → 应用中心显示名（flymail → 飞邮）。飞牛把这份清单存在**应用中心守护进程内部**，
# 盘上没有任何可读的 json/db 文件（实测 158：@appcenter 各目录里没有 manifest、没有 app.json，
# 显示名只出现在 `trim-cli app list` 的 JSON 里），所以只能从那个命令取。
_APP_NAMES_CACHE = {"t": 0.0, "v": None}

# 飞牛运行时依赖包（node / python / bun 等）：它们也装在 @appcenter 下、也自带 ui/images，
# 但**不是用户安装的应用**。若算进 appid 集合，会把「用 node 跑的应用」误标成 nodejs_v24
# （实测 158：deepseek.harness 的 node 子进程 exe 落在 @appcenter/nodejs_v24/bin/node）。
# 一律从 appid 集合剔除，让归属继续沿父链找到真正的应用。
_RUNTIME_APP_IDS = ("nodejs_v16", "nodejs_v18", "nodejs_v20", "nodejs_v22", "nodejs_v24",
                    "python39", "python310", "python311", "python312", "python313",
                    "bunjs", "golang", "java", "jdk", "ffmpeg")

def _app_ids():
    """返回飞牛应用中心已安装 app 的 id 集合（@appcenter / @appshare 目录名即 id，也是运行用户）。

    带 300s 缓存：本函数被「端口归属反查」「app 图标接口」共调用，
    无缓存时每个图标请求都要跑一次 sudo ls。
    """
    now = time.time()
    c = _APP_IDS_CACHE
    if c["v"] is not None and now - c["t"] <= _APP_IDS_TTL:
        return c["v"]
    ids = set()
    try:
        out = shell.sudo_cmd(["bash", "-c",
            "ls -1 /vol1/@appcenter/ /vol1/@appshare/ 2>/dev/null"], 8)
        for line in out.splitlines():
            line = line.strip()
            if not line or line.endswith(":"):
                continue
            if line in _RUNTIME_APP_IDS:
                continue
            ids.add(line)
    except Exception:
        pass
    if ids or c["v"] is None:
        c["t"] = now
        c["v"] = ids
    return ids

def _app_display_names():
    """返回 {appid: 应用中心里显示的名字}，取自 `trim-cli app list`。

    飞牛没有把应用名落盘（@appcenter/<appid>/ 下既无 manifest 也无 app.json），
    只有这个命令能给出「用户实际看到的名字」——如 flymail→飞邮、
    com.buddy.credit→WorkBuddy 积分助手。带 300s 缓存。

    ⚠ 容错优先：命令不存在 / 超时 / 输出夹了日志导致 JSON 解析失败，都返回空 dict，
    调用方（_app_friendly）自动退化成 appid 美化——端口页其它信息不受影响。
    """
    now = time.time()
    c = _APP_NAMES_CACHE
    if c["v"] is not None and now - c["t"] <= _APP_IDS_TTL:
        return c["v"]
    m = {}
    try:
        out = shell.sudo_cmd(["trim-cli", "app", "list"], 10)
        i = out.find("{")          # 前面可能有 warning 行，从第一个 { 开始解析
        if i >= 0:
            data = json.loads(out[i:])
            for a in (data.get("list") or []):
                aid = (a.get("appName") or "").strip()
                nm = (a.get("name") or "").strip()
                if aid and nm:
                    m[aid] = nm
    except Exception:
        m = {}
    if m or c["v"] is None:
        c["t"] = now
        c["v"] = m
    return m

def _docker_id_map():
    """返回 {容器ID(全 64 位 + 前 12 位): 容器名}，供 cgroup 反查容器用。"""
    m = {}
    try:
        out = shell.sudo_cmd(["docker", "ps", "--no-trunc", "--format", "{{.ID}}|{{.Names}}"], 8)
        for line in out.splitlines():
            if "|" not in line:
                continue
            cid, name = line.split("|", 1)
            cid, name = cid.strip(), name.strip()
            if cid and name:
                m[cid] = name
                m[cid[:12]] = name
    except Exception:
        pass
    return m

# 容器内进程的 cgroup 形如：
#   0::/system.slice/docker-<64hex>.scope
#   0::/kubepods/.../cri-containerd-<64hex>.scope
_CGROUP_CID_RE = re.compile(r'(?:docker|cri-containerd|crio|containerd)[-_]([0-9a-f]{12,64})')

def _cgroup_container(cgroup_text):
    """从 /proc/<pid>/cgroup 内容里解析容器 ID 前 12 位，非容器返回 ""。"""
    m = _CGROUP_CID_RE.search(cgroup_text or "")
    return m.group(1)[:12] if m else ""

# 应用自带图标的文件名偏好顺序（**大小写不敏感**！飞牛包里并存三种流派，实测 158：
#   miproxy=ICON.PNG / trim.openclaw=icon.PNG（全大写）· com.buddy.credit=icon-256.png（连字符）
#   app-cleaner=icon_256.png（下划线））。早先按小写精确匹配会漏掉大写那批。
# 顺序 = 先高分辨率，避免兜底扫目录时按字母序挑到 icon_0.png 这种小图。
_APP_ICON_PREF = ("icon_256", "icon-256", "icon_128", "icon-128",
                  "icon_64", "icon-64", "icon", "256", "64", "icon_0", "icon_1")
_APP_ICON_EXTS = (".png", ".webp", ".jpg", ".jpeg")
_ICON_MIME = {".png": "image/png", ".webp": "image/webp",
              ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _app_icon_file(appid):
    """按 appid 定位应用自带图标文件，找不到返回 ""（前端据此回退为文字）。

    只认「已安装应用」（appid 由路由用 _app_ids() 白名单校验过）+ 只拼固定子路径，
    杜绝目录穿越。大小写不敏感：飞牛包里 ICON.PNG / icon.PNG 与小写混用。
    """
    if not appid or appid in (".", "..") or not re.match(r'^[A-Za-z0-9._-]+$', appid):
        return ""
    for root in ("/vol1/@appcenter", "/vol1/@appshare"):
        d = os.path.join(root, appid, "ui", "images")
        try:
            names = os.listdir(d)
        except Exception:
            continue
        low = {}
        for n in names:
            low.setdefault(n.lower(), n)
        for key in _APP_ICON_PREF:
            for ext in _APP_ICON_EXTS:
                real = low.get(key + ext)
                if not real:
                    continue
                p = os.path.join(d, real)
                if os.path.isfile(p):
                    return p
        # 兜底：目录里任意图片（含 icon 字样 / 以 appid 开头）——icon_0/icon_1 这类小图已在
        # 上面的偏好表末位兜过，这里只收偏好表没覆盖到的名字
        cands = [n for n in sorted(names)
                 if n.lower().endswith(_APP_ICON_EXTS)
                 and ("icon" in n.lower() or n.lower().startswith(appid.lower()))]
        for n in cands:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return ""

# 分隔符用 \x1f（Unit Separator）：进程名按惯例不会含它，避免 cmdline 里的 | 把字段切错
_PID_FS = "\x1f"

def _pid_info(pids):
    """批量读 /proc/<pid> 的归属线索（一次 bash 读完，避免 N 次进程开销）。

    owner 用户 = appid（飞牛应用的运行用户就是 appid）；exe/cwd/cmd 用来兜底
    那些以 root 或通用解释器身份运行的 app 进程；cgroup 用来把「容器内的进程」
    归属到对应容器（容器里的服务没走 -p 端口映射时，宿主机只能靠 cgroup 认出它）。
    返回 {pid: {"user":str, "cmd":str, "exe":str, "cwd":str, "ppid":int, "cid":str}}
    """
    if not pids:
        return {}
    plist = " ".join(str(p) for p in pids)
    script = (
        "for p in " + plist + "; do "
        "u=$(stat -c '%U' /proc/$p 2>/dev/null); "
        "cm=$(tr '\\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-300); "
        "ex=$(readlink /proc/$p/exe 2>/dev/null); "
        "cw=$(readlink /proc/$p/cwd 2>/dev/null); "
        "pp=$(awk '{print $4}' /proc/$p/stat 2>/dev/null); "
        "cg=$(cat /proc/$p/cgroup 2>/dev/null | head -1); "
        "printf '%s\\x1f%s\\x1f%s\\x1f%s\\x1f%s\\x1f%s\\x1f%s\\n' "
        "\"$p\" \"$u\" \"$cm\" \"$ex\" \"$cw\" \"$pp\" \"$cg\"; "
        "done"
    )
    out = shell.sudo_cmd(["bash", "-c", script], 10)
    res = {}
    for line in out.splitlines():
        f = line.split(_PID_FS)
        if len(f) < 7:
            continue
        try:
            pid = int(f[0].strip())
        except ValueError:
            continue
        ppid = int(f[5].strip()) if f[5].strip().isdigit() else 0
        res[pid] = {"user": f[1].strip(), "cmd": f[2], "exe": f[3].strip(),
                    "cwd": f[4].strip(), "ppid": ppid,
                    "cid": _cgroup_container(f[6])}
    return res

def _app_of_pid(pid, pinfo, appids, max_hops=4):
    """反查「这个监听进程属于哪个应用中心 app」，返回 appid 或 ""。

    线索按强度排序逐层判定：
      ① 运行用户 = appid（飞牛应用的运行用户就是 appid，最可靠）
      ② cmdline → cwd → exe 里出现 @appcenter|@appshare|@appdata/<appid>
         （exe 放最后：它常指向共用运行时，如 @appcenter/nodejs_v24/bin/node）
    单层无线索就沿父进程链向上最多 max_hops 层——应用常用通用解释器
    （python3 / node）拉起监听服务，子进程自身看不出归属，父进程才是应用主进程。
    """
    cur = pid
    seen = set()
    for _ in range(max_hops + 1):
        if not cur or cur in seen:
            break
        seen.add(cur)
        rec = pinfo.get(cur)
        if not rec:
            break
        u = rec.get("user") or ""
        if u in appids:
            return u
        for field in ("cmd", "cwd", "exe"):
            blob = rec.get(field) or ""
            if not blob:
                continue
            for aid in appids:
                if ("@appcenter/" + aid) in blob or ("@appshare/" + aid) in blob \
                        or ("@appdata/" + aid) in blob:
                    return aid
        cur = rec.get("ppid") or 0
    return ""

def _expand_pid_chain(pids, rounds=3):
    """把监听 pid 集合沿父链补齐，最多补 rounds 层（供 _app_of_pid 逐层判归属）。"""
    pinfo = _pid_info(pids)
    for _ in range(rounds):
        need = set()
        for p, rec in pinfo.items():
            pp = rec.get("ppid") or 0
            if pp > 1 and pp not in pinfo:
                need.add(pp)
        if not need:
            break
        pinfo.update(_pid_info(need))
    return pinfo

def _app_friendly(appid):
    """appid → 应用中心里展示的名字（flymail→飞邮、dsh.sync→DeepSeek Harness (Sync)）。

    优先取 `trim-cli app list` 的显示名；取不到（命令缺失/超时）才退化成
    「去掉包名前缀的 appid」——早期版本只有这条退化路径，界面上就会看到
    deepseek.harness / com.buddy.credit 这种包名。
    """
    if not appid:
        return ""
    nm = _app_display_names().get(appid)
    if nm:
        return nm
    for p in ("com.dashboard.", "com.", "trim."):
        if appid.startswith(p):
            return appid[len(p):]
    return appid

def _proc_comm(pid):
    """读 /proc/<pid>/comm 取短进程名。"""
    try:
        with open("/proc/%d/comm" % int(pid)) as f:
            return f.read().strip()
    except Exception:
        return ""

def get_listening_ports():
    """列出宿主机所有监听端口（TCP/UDP）+ 占用进程 + 来源 + 归属应用。

    - 用 ss -tulnp（需 root 才能看其它进程 PID，故走 sudo_cmd）。
    - 来源 scope：docker:容器名 / docker / app:<appid> / host
    - app：若进程 owner 或 cmdline 落在 @appcenter/@appshare/<appid> 目录，标注应用 id
    - desc：系统服务的中文说明（飞牛自带服务，非应用中心 app）
    """
    raw_ports = []
    try:
        raw_tcp = shell.sudo_cmd(["ss", "-tlnpH"], 8)
        raw_udp = shell.sudo_cmd(["ss", "-ulnpH"], 8)
    except Exception:
        raw_tcp = raw_udp = ""
    dmap = _docker_port_map()
    zones = _local_net_zones()
    pids = set()
    for proto, raw in (("tcp", raw_tcp), ("udp", raw_udp)):
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[3]
            mo = re.match(r'(\[[^\]]+\]|[^:]+):(\d+)$', local)
            if not mo:
                continue
            addr = mo.group(1)
            try:
                port = int(mo.group(2))
            except ValueError:
                continue
            proc = ""
            pid = None
            pm = re.search(r'users?:\(\("([^"]+)"(?:,pid=(\d+))?', line)
            if pm:
                proc = pm.group(1)
                pid = int(pm.group(2)) if pm.group(2) else None
            scope = "host"
            cname = dmap.get(str(port))
            if cname:
                scope = "docker:" + cname
            elif proc in ("docker-proxy",):
                scope = "docker"
            # 可达范围三级（lan/docker/lo），public 保留＝「局域网可达」，
            # 旧前端(只认 public)也能继续工作。
            reach = _addr_zone(addr, zones)
            raw_ports.append({
                "proto": proto,
                "local": addr,
                "port": port,
                "state": (parts[0] if proto == "tcp" else "UNCONN"),
                "pid": pid,
                # 拿不到进程名时留空（内核态 socket，见下方 kernel 判定），由端口反查补说明；
                # 早先这里填 "?"，界面上就会出现一行看不懂的「?」（用户实测 2049 反馈）
                "process": proc,
                "scope": scope,
                "reach": reach,
                "public": reach == "lan",
            })
            if pid is not None:
                pids.add(pid)
    # 反查进程归属应用（应用中心 app 的运行用户即 appid，目录 owner 也是 appid；
    # 部分 app 以 root / 通用解释器（python3、node）运行 ⟶ 用 exe/cwd/cmdline +
    # 父进程链兜底，把「app 拉起的子进程」也归到该 app 名下）。
    # 容器内进程则用 cgroup 反查容器名（容器服务没走 -p 端口映射时，宿主机只能这样认出来）。
    appids = _app_ids()
    pinfo = _expand_pid_chain(pids) if pids else {}
    cid_map = _docker_id_map() if pids else {}
    ports = []
    for rp in raw_ports:
        pid = rp.get("pid")
        app = _app_of_pid(pid, pinfo, appids) if (pid is not None and appids) else ""
        scope = rp["scope"]
        # 容器名两条来源，**不能互相覆盖**：① 解析阶段 docker ps 端口映射已写成 docker:<name>
        # （走这条时 pid 常是 docker-proxy 这种宿主进程，cgroup 查不到）② cgroup 反查（容器
        # 没做 -p 映射时）。早先在第 ② 步用 `or ""` 无条件覆盖，把 ① 查到的好名字清成了空，
        # 直接后果＝容器图标取不到（158 实测 homebox）。
        cname = scope[7:] if scope.startswith("docker:") else ""
        if app:
            scope = "app:" + app
            cname = ""
        else:
            if not cname and pid is not None and cid_map:
                cname = cid_map.get((pinfo.get(pid) or {}).get("cid") or "") or ""
            if cname:
                scope = "docker:" + cname
        # 内核态 socket：`ss` 没有 users 段（内核线程持有，如 NFS 的 nfsd/lockd），
        # 既没有 PID 也没有进程名，只能按端口号反查 RPC 服务来给说明。
        kernel = (pid is None and not app and not scope.startswith("docker:"))
        if app or scope.startswith("docker:"):
            desc = ""
        else:
            desc = _SERVICE_DESC.get(rp["process"], "")
            if kernel and not desc:
                svc = _rpc_port_map().get(str(rp["port"]), "")
                desc = _RPC_DESC.get(svc) or ((svc + "（内核服务）") if svc else "")
        rp["kernel"] = kernel
        # 图标来源（实测 158 画像）：应用中心装的东西才有图标，纯 docker run 起的容器没有。
        # ① 应用＝用它自己的图标；② 容器名若**同时也是应用中心的应用**（商店装的 docker 应用会
        # 生成同名 @appcenter 目录，实测 homebox / cf-dns-select）→ 借用该应用的图标；
        # ③ 其余（飞牛系统服务、纯 docker 容器）没有任何图标文件 → 留空，前端显示纯文字。
        icon = app or (cname if cname in appids else "")
        rp["app"] = app
        rp["appname"] = _app_friendly(app) if app else ""
        rp["icon"] = icon
        # 容器行若借用了某个应用的图标（容器名＝appid，商店装的 docker 应用），
        # 展示名也用那个应用的显示名（cf-dns-select → CF DNS 优选）；原始容器名留在 scope 里。
        rp["iconname"] = _app_friendly(icon) if (icon and not app) else ""
        rp["desc"] = desc
        rp["scope"] = scope
        ports.append(rp)
    ports.sort(key=lambda x: (x["port"], x["proto"]))
    return ports
