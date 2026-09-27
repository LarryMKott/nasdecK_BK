# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend import shell
from backend.caching import _ttl_cache
from backend.collectors.cputemp import _temp_snapshot_read
from backend.collectors.raid import get_raid_card
from backend.shell import read_file
import json
import re


# ===================== 采集：存储卷 =====================
CLOUD_MOUNT_INFO = "/etc/mountmgr/mount_info.json"

def _cloud_mount_map():
    """飞牛「远程挂载」的**云盘**归属：mountPoint -> {type, name, proto}。

    数据源 /etc/mountmgr/mount_info.json（root 专属，本进程以 root 运行可直接读）。

    ⚠️ 该文件是所有「远程挂载」共用的（SMB / NFS / FTP / SFTP / WebDAV 也在里面），
    只有云盘（夸克/百度/阿里/123 等）才带 cloudStorageTypeStr 品牌字段；非云端远程挂载
    该字段为空。故只认「品牌名非空」的条目，避免把 SMB/NFS 当云盘、误把它们的真实容量抹掉。

    返回值语义：
      None  → 文件读不到/格式异常（降级模式，调用方回退按 fuse.rclone 识别）
      {}    → 读取成功，但当前没有云盘挂载
      {...} → mountPoint -> 云盘信息
    例：{"cloudStorageTypeStr": "夸克网盘", "comment": "兴奋的柚子"}（comment 是网盘账号名）。
    """
    try:
        raw = read_file(CLOUD_MOUNT_INFO, "")
        if not raw.strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
    except Exception:
        return None
    out = {}
    for _uid, mounts in data.items():
        if not isinstance(mounts, dict):
            continue
        for _mid, info in mounts.items():
            if not isinstance(info, dict):
                continue
            brand = (info.get("cloudStorageTypeStr") or "").strip()
            if not brand:
                continue          # 非云端远程挂载（SMB/NFS/FTP…），不当云盘处理
            name = (info.get("comment") or "").strip()
            # proto 是底层传输（云盘统一经本机 WebDAV 桥 15244 + rclone 挂载），
            # http/https/webdav* 一律展示为「rclone WebDAV」更好读；异常值原样带出。
            p = (info.get("proto") or "").strip()
            proto = "rclone WebDAV" if p.lower() in ("", "http", "https", "webdav", "webdavnative", "webdavproxy") else f"rclone {p}"
            # mountPoint 单数；防御性兼容 mountPoints 数组
            mps = []
            if info.get("mountPoint"):
                mps.append(str(info["mountPoint"]).strip())
            if isinstance(info.get("mountPoints"), list):
                mps += [str(x).strip() for x in info["mountPoints"] if x]
            for mp in mps:
                if mp:
                    out[mp] = {"type": brand, "name": name, "proto": proto}
    return out

@_ttl_cache(60)
def get_storage():
    d = {"raid_arrays": [], "volumes": [], "topology": "", "cloud_mounts": []}
    # mdadm RAID
    mdstat = read_file("/proc/mdstat")
    d["mdstat"] = mdstat
    d["topology"] = shell.sudo_cmd(["lsblk", "-o", "NAME,SIZE,TYPE,ROTA,MODEL"], 5) or shell.run_cmd(["lsblk"], 5)
    cur = None
    for line in mdstat.splitlines():
        # 成员盘在首行：md2 : active raid0 sda1[0] sdb1[1]
        m = re.match(r"^(md\d+)\s*:\s*(\w+)\s+(raid\d+|linear|multipath)(.*)", line)
        if m:
            if cur:
                d["raid_arrays"].append(cur)
            cur = {"name": m.group(1), "state": m.group(2), "level": m.group(3), "disks": [], "size": ""}
            for dm in re.findall(r"(sd\w+|nvme\w+)", m.group(4)):
                cur["disks"].append(dm)
        elif cur and re.match(r"^\s+\d+\s+blocks", line):
            ms = re.search(r"(\d+)\s+blocks", line)
            if ms:
                cur["size"] = fmt_blocks(int(ms.group(1)))
    if cur:
        d["raid_arrays"].append(cur)
    # 挂载点容量（排除 docker overlay / tmpfs 等非存储卷）
    cloud_map = _cloud_mount_map()
    df = shell.run_cmd(["df", "-h", "--output=target,size,used,avail,pcent,fstype"], 5)
    skip_fs = ("overlay", "tmpfs", "devtmpfs", "squashfs")
    for line in df.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6:
            mount, size, used, avail, pcent, fstype = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
            if fstype in skip_fs:
                continue
            if "docker" in mount or "overlay" in mount:
                continue
            if mount in ("/", "/fs", "/boot", "/boot/efi") or mount.startswith("/vol"):
                row = {
                    "mount": mount, "size": size, "used": used,
                    "avail": avail, "pcent": pcent, "fstype": fstype,
                }
                # 云盘（飞牛「远程挂载」的网盘，经 rclone/FUSE 挂成本地目录）
                #  - cloud_map 读到过（非 None）→ 只认品牌字段命中的，SMB/NFS 等不受影响
                #  - cloud_map 读不到（None，降级）→ 退回按 fuse.rclone 文件系统识别
                cinfo = None if cloud_map is None else cloud_map.get(mount)
                _degraded = cloud_map is None and fstype.startswith("fuse.rclone")
                if cinfo or _degraded:
                    cinfo = cinfo or {}
                    row.update({
                        "cloud": True,
                        "cloud_type": cinfo.get("type") or "云盘",
                        "cloud_name": cinfo.get("name") or "",
                        "cloud_proto": cinfo.get("proto") or "rclone WebDAV",
                    })
                    # df 对云挂载给出的容量是 rclone 在「后端不报配额」时填的占位值
                    # （1 PB），既非真实容量也算不出已用，一律置空避免误导；
                    # 前端对这些字段显示「—」，并把该行排除出平均使用率。
                    row["size"] = row["used"] = row["avail"] = row["pcent"] = None
                    d["volumes"].append(row)
                    d["cloud_mounts"].append(row)
                    continue
                d["volumes"].append(row)
    # 存储拓扑：lsblk 只列块设备，看不到 FUSE 云挂载，单独补一段说明
    if d["cloud_mounts"]:
        lines = [d["topology"].rstrip(), "", "── 云端挂载（远程挂载 / rclone FUSE，非本地磁盘）──"]
        for c in d["cloud_mounts"]:
            # comment 字段是云盘账号名（如夸克账号「兴奋的柚子」），多账号时用于区分
            label = c["cloud_type"] + (f"（账号：{c['cloud_name']}）" if c["cloud_name"] else "")
            lines.append(f"{c['mount']}  {label}  {c['cloud_proto']}  已挂载")
        d["topology"] = "\n".join(lines)
    return d

def get_volume_map():
    """卷映射树（v2.3.0 3.6）：fnOS 卷(根) → md/LVM(枝) → 物理盘(叶)，串成一条链。

    数据源全走现成缓存/快照，不碰 smartctl 全量扫盘：
      - lsblk -J 一次拿 层级 + 序列号 + 型号（~几十 ms，纯内核查询）
      - 卷容量/使用率来自 get_storage()（60s 缓存）已解析的 df
      - md 数组来自 get_storage()（/proc/mdstat）
      - 阵列卡槽位/状态来自 get_raid_card()（60s 缓存），按序列号对上系统盘符（JBOD 直通也成立）
      - 温度来自统一温度快照 _TEMP_SNAP（~2s 刷新），无额外采样
    没进任何卷的盘归入 loose（未入卷），同样可点跳硬盘页。"""
    # 1) lsblk -J：层级树（含 SERIAL/MODEL，免 smartctl）
    tree = []
    try:
        out = shell.run_cmd(["lsblk", "-J", "-o", "NAME,TYPE,FSTYPE,MOUNTPOINT,SIZE,SERIAL,MODEL"], 10)
        tree = (json.loads(out).get("blockdevices") or []) if out else []
    except Exception as e:
        shell.log("volume_map lsblk failed: " + str(e))
    # 2) 从树里抽链：叶节点(有挂载点)一路记下 盘→分区→md→lvm。
    #    注意：md 设备的 lsblk type 是 "raid0"/"raid1"… 不是 "md"；多条盘共享同一 md/LVM 时
    #    lsblk JSON 会把共享子树在每条盘下重复输出，所以按挂载点聚合、盘并集去重。
    chains = []   # {"mount","md","lvm","disks":[{dev,serial,model}]}
    def _walk(node, disks, md, lvm):
        name = (node.get("name") or "").split("/")[-1].strip()
        ntype = node.get("type") or ""
        kids = node.get("children") or []
        if ntype == "disk":
            disks = [{"dev": name, "serial": (node.get("serial") or "").strip(),
                      "model": (node.get("model") or "").strip()}]
        elif (ntype.startswith("raid") or ntype in ("linear", "multipath")
              or re.match(r"^md\d+$", name)):
            md = name
        elif ntype == "lvm":
            lvm = name
        if not kids:
            chains.append({"mount": node.get("mountpoint") or None, "md": md,
                           "lvm": lvm, "disks": disks})
            return
        for ch in kids:
            _walk(ch, disks, md, lvm)
    for n in tree:
        _walk(n, [], None, None)
    # 3) 物理盘补充信息：温度快照 + 阵列卡槽位（按序列号）
    temps = _temp_snapshot_read().get("disks") or {}
    slot_by_sn = {}
    try:
        for drv in (get_raid_card().get("drives") or []):
            sn = (drv.get("sn") or "").strip().upper()
            if sn:
                slot_by_sn[sn] = drv
    except Exception:
        pass
    def _pd_node(dinfo):
        dev = dinfo["dev"]
        t = temps.get("/dev/" + dev) or temps.get(dev) or {}
        drv = slot_by_sn.get((dinfo.get("serial") or "").strip().upper()) or {}
        return {"dev": dev, "serial": dinfo.get("serial") or "",
                "model": drv.get("model") or dinfo.get("model") or "",
                "temp": t.get("temp"), "asleep": bool(t.get("asleep")),
                "slot": drv.get("slot") or "", "pd_state": drv.get("state") or ""}
    # 4) 按卷组装：mount → lvm → md → 物理盘
    st = get_storage()
    arrays = {a.get("name"): a for a in (st.get("raid_arrays") or [])}
    used_devs = set()
    vols = []
    for v in (st.get("volumes") or []):
        m = v.get("mount") or ""
        if v.get("cloud") or not m.startswith("/vol"):
            continue
        chain_list = [c for c in chains if c.get("mount") == m]
        pds = []
        md_info = None
        lvm_name = None
        by_dev = {}
        for chain in chain_list:
            if not md_info and chain.get("md"):
                a = arrays.get(chain["md"]) or {}
                md_info = {"name": chain["md"], "level": a.get("level") or "",
                           "state": a.get("state") or "", "size": a.get("size") or ""}
            lvm_name = lvm_name or chain.get("lvm")
            for x in chain["disks"]:
                by_dev[x["dev"]] = x   # 共享 md 的多条链并集去重
        pds = [_pd_node(x) for x in by_dev.values()]
        if pds:
            used_devs.update(by_dev.keys())
        vols.append({"mount": m, "fs": v.get("fstype") or "", "size": v.get("size"),
                     "used": v.get("used"), "pcent": v.get("pcent"),
                     "lvm": lvm_name, "md": md_info, "disks": pds})
    # 5) 未入卷的盘（如独立数据盘/休眠备盘）
    seen = set()
    loose = []
    for c in chains:
        for x in c["disks"]:
            if x["dev"] in used_devs or x["dev"] in seen:
                continue
            seen.add(x["dev"])
            loose.append(_pd_node(x))
    # 兜底：lsblk 树里顶层的盘也要进 loose（没分区没挂载的裸盘不会出现在 chains 里）
    for n in tree:
        if (n.get("type") == "disk"):
            name = (n.get("name") or "").split("/")[-1]
            if name not in used_devs and name not in seen:
                seen.add(name)
                loose.append(_pd_node({"dev": name, "serial": (n.get("serial") or "").strip(),
                                       "model": (n.get("model") or "").strip()}))
    return {"volumes": vols, "loose": loose}

def fmt_blocks(blocks):
    # blocks 是 1K 块
    kb = blocks
    if kb >= 1073741824:
        return f"{kb/1073741824:.1f} TB"
    if kb >= 1048576:
        return f"{kb/1048576:.1f} GB"
    return f"{kb/1024:.0f} MB"
