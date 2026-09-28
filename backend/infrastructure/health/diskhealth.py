# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.command import cmd_runner as shell

from backend.infrastructure.collectors.disks import get_disks
from backend.infrastructure.collectors.raid import get_raid_card
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
import os
import re
import threading as _threading
import time


# ============ 单盘健康分级 + 缺陷增长趋势（v2.3.0 3.4） ============
# 只给「到手就能给用户解释」的三个档 + 一条大白话原因，不堆原始属性表。
#   红（现在就该处理：备份数据、准备换盘）
#     · SMART 自检判定 FAILED
#     · 阵列卡 S.M.A.R.T 告警（S.M.A.R.T alert flagged by drive = Yes）
#     · 阵列卡预测性失败计数 > 0
#     · 阵列卡报盘不在线（Failed/UBad/Offline…）
#     · 待处理扇区 > 0（有扇区读不稳，等着被替换）
#     · 不可纠正扇区 > 0
#   黄（已有损伤或接近上限：盯住，不用马上换）
#     · 重映射扇区（含 SAS grown defect）> 0
#     · 阵列卡介质错误 / 其它错误 / 坏块管理错误 > 0
#     · 温度 ≥ 该盘高温线（硬盘自报值优先，默认 55℃）
#     · SSD 寿命已用 > 80% / NVMe 剩余备用块 < 10% / NVMe 临界告警非 0
#   绿：以上都没有。未知：盘休眠或一点可判断的数据都读不到。
# 判据只读现有数据：系统侧 smartctl 已解析字段 + 阵列卡已解析字段，不新增任何命令。

# 缺陷类计数（越涨越坏，用于「较上次 ↑N」趋势）
_DEFECT_COUNTERS = ("media_err", "other_err", "bbm_err", "pred_fail",
                    "reallocated", "pending", "uncorrectable",
                    "udma_crc", "defects", "non_medium_errors", "read_errors", "write_errors")
# 寿命类计数（也会涨，但含义是「消耗」不是「缺陷」，单独拼文案）
_WEAR_COUNTERS = ("percentage_used", "endurance_used")
_TREND_COUNTERS = _DEFECT_COUNTERS + _WEAR_COUNTERS
# 阵列卡报这些状态 = 盘不在线（红）
_PD_BAD_STATES = ("FAILED", "RBAD", "UBAD", "OFFLN", "OFFL", "MISSING", "MISSOFF")

_HEALTH_META = {
    "red": {"label": "异常", "order": 3},
    "yellow": {"label": "注意", "order": 2},
    "green": {"label": "正常", "order": 1},
    "unknown": {"label": "未知", "order": 0},
}


def _grade_disk_health(disk, pd=None):
    """给单块盘分级：返回 {level, label, reasons[], counters{}, source}。纯计算、无 I/O。"""
    d = disk if isinstance(disk, dict) else {}
    pd = pd if isinstance(pd, dict) else {}
    # 阵列卡侧字段挂在物理盘的 pd_health 里（state 在盘对象上），统一取出来用
    ph = pd.get("pd_health")
    ph = ph if isinstance(ph, dict) else {}
    red, yellow = [], []
    counters = {}

    def _int(v):
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    if bool(d.get("asleep")):
        return {"level": "unknown", "label": "未知", "reasons": ["硬盘休眠中，暂时读不到 SMART 数据"],
                "counters": {}, "source": "休眠"}

    # ---- 系统 SMART 侧 ----
    verdict = (d.get("health") or "").strip().upper()
    if verdict == "FAILED":
        red.append("SMART 自检判定为故障（FAILED）")

    # ---- 阵列卡侧 ----
    if ph or pd:
        if ph.get("smart_alert") is True:
            red.append("阵列卡报 S.M.A.R.T 告警")
        st = (pd.get("state") or "").strip().upper()
        if st and any(b in st for b in _PD_BAD_STATES):
            red.append(f"阵列卡报盘已不在线（{pd.get('state')}）")

    for key, name in (("pred_fail", "预测性失败"), ("bbm_err", "坏块管理错误"),
                      ("media_err", "介质错误"), ("other_err", "其它错误")):
        v = _int(ph.get(key))
        if v is None:
            continue
        counters[key] = v
        if v <= 0:
            continue
        if key == "pred_fail":
            red.append(f"阵列卡报预测性失败 {v} 次")
        elif key == "bbm_err":
            yellow.append(f"阵列卡记录坏块管理错误 {v} 次")
        else:
            yellow.append(f"阵列卡记录{name} {v} 次")

    # ---- 扇区级缺陷（ATA/ SATA 与 SAS 同名同义，取到哪个用哪个）----
    pn = _int(d.get("pending"))
    if pn is not None:
        counters["pending"] = pn
        if pn > 0:
            red.append(f"待处理扇区 {pn} 个（有扇区读不稳，随时可能变坏）")
    un = _int(d.get("uncorrectable"))
    if un is not None:
        counters["uncorrectable"] = un
        if un > 0:
            red.append(f"不可纠正扇区 {un} 个")
    ra = _int(d.get("reallocated"))
    if ra is not None:
        counters["reallocated"] = ra
        if ra > 0:
            yellow.append(f"重映射扇区 {ra} 个（已用备用块顶过坏扇区）")
    df = _int(d.get("defects"))
    if df is not None:
        # SAS 缺陷表计数用原名进 counters（机械盘无此键，不会重复显示）
        counters["defects"] = df
        if df > 0 and ra is None:
            yellow.append(f"已发现缺陷扇区 {df} 个（SAS 缺陷表）")
    # 以下三项不参与判级（接口层/传输层问题，不等于盘体坏了），但 0 值也进 counters 供弹窗完整展示
    uc = _int(d.get("udma_crc"))
    if uc is not None:
        counters["udma_crc"] = uc
    nme = _int(d.get("non_medium_errors"))
    if nme is not None:
        counters["non_medium_errors"] = nme
    re_ = _int(d.get("read_errors"))
    if re_ is not None:
        counters["read_errors"] = re_
    we_ = _int(d.get("write_errors"))
    if we_ is not None:
        counters["write_errors"] = we_

    # ---- 温度 ----
    t = _int(ph.get("temp"))
    if t is None:
        t = _int(d.get("temp"))
    if t is not None:
        counters["temp"] = t
        trip = _int(d.get("temp_trip")) or 55
        if t >= trip:
            yellow.append(f"温度 {t}℃ 已达到高温线 {trip}℃")

    # ---- SSD / NVMe 寿命 ----
    pu = _int(d.get("percentage_used"))
    if pu is not None:
        counters["percentage_used"] = pu
        if pu > 80:
            yellow.append(f"SSD 寿命已用 {pu}%")
    eu = _int(ph.get("endurance_used"))
    if eu is None:
        eu = _int(d.get("endurance_used"))
    if eu is not None:
        counters["endurance_used"] = eu
        if eu > 80:
            yellow.append(f"SSD 寿命已用 {eu}%（阵列卡报）")
    sp = _int(d.get("available_spare"))
    if sp is not None:
        counters["available_spare"] = sp
        if sp < 10:
            yellow.append(f"NVMe 剩余备用块仅 {sp}%")
    cw = (d.get("critical_warning") or "").strip().lower()
    if cw and cw not in ("0", "00", "0x0"):
        yellow.append(f"NVMe 报了临界告警（0x{cw.lstrip('0x')}）")

    src = []
    if ph or pd:
        src.append("阵列卡")
    if verdict:
        src.append("系统 SMART")

    if red:
        level = "red"
    elif yellow:
        level = "yellow"
    elif counters or verdict:
        level = "green"
    else:
        level = "unknown"

    reasons = red + yellow
    if not reasons and level == "green":
        reasons = []
    if level == "unknown" and not reasons:
        reasons = ["读不到这块盘的健康数据（可能已休眠或无法访问）"]
    return {"level": level, "label": _HEALTH_META[level]["label"], "reasons": reasons,
            "counters": counters, "source": " + ".join(src) or "—"}


# 缺陷快照历史：每 6h 一条、保留 180 天、最多 500 条，落盘 @appdata/disk_health_hist.json
DISK_HEALTH_HIST_FILE = os.path.join(_config_dir(), "disk_health_hist.json")
DISK_HEALTH_HIST_MAX = 500
DISK_HEALTH_KEEP_DAYS = 180
DISK_HEALTH_INTERVAL = 6 * 3600
_DISK_HEALTH_LOCK = _threading.RLock()
_DISK_HEALTH_CACHE = {"loaded": False, "rows": []}


def _health_hist_rows():
    """读全部快照（内存缓存 + 文件兜底）。"""
    with _DISK_HEALTH_LOCK:
        if not _DISK_HEALTH_CACHE["loaded"]:
            rows = _load_json_file(DISK_HEALTH_HIST_FILE, [])
            _DISK_HEALTH_CACHE["rows"] = rows if isinstance(rows, list) else []
            _DISK_HEALTH_CACHE["loaded"] = True
        return _DISK_HEALTH_CACHE["rows"]


def _health_hist_last_ts():
    rows = _health_hist_rows()
    if rows and isinstance(rows[-1], dict):
        t = rows[-1].get("t")
        if isinstance(t, (int, float)):
            return float(t)
    return 0.0


def _health_hist_prev(before_ts):
    """取「上一次采样」那条（早于 before_ts 的最近一条）。"""
    for r in reversed(_health_hist_rows()):
        if isinstance(r, dict):
            t = r.get("t")
            if isinstance(t, (int, float)) and float(t) <= before_ts:
                return r
    return None


def _health_hist_append(row):
    """追加一条快照：按天裁剪 + 条数上限，然后落盘。"""
    if not row or not row.get("disks"):
        return False
    with _DISK_HEALTH_LOCK:
        _health_hist_rows()          # 保证缓存已加载
        rows = list(_DISK_HEALTH_CACHE["rows"])
        rows.append(row)
        cut = time.time() - DISK_HEALTH_KEEP_DAYS * 86400
        rows = [r for r in rows if isinstance(r, dict) and float(r.get("t") or 0) >= cut]
        if len(rows) > DISK_HEALTH_HIST_MAX:
            rows = rows[-DISK_HEALTH_HIST_MAX:]
        _DISK_HEALTH_CACHE["rows"] = rows
        _DISK_HEALTH_CACHE["loaded"] = True
    try:
        _save_json_file(DISK_HEALTH_HIST_FILE, rows)
    except Exception:
        pass
    return True


def _raid_pd_by_sn(raid):
    """阵列卡物理盘按序列号索引（大小写不敏感）。"""
    m = {}
    if isinstance(raid, dict) and raid.get("mode") == "mega":
        for dv in raid.get("drives", []):
            sn = (dv.get("sn") or "").strip().upper()
            if sn:
                m[sn] = dv
    return m


def _health_key_for(disk, pd=None):
    """趋势用的稳定 key：优先序列号（双路径/换盘符不重复），退化用 WWN，再退化用盘符。"""
    sn = (disk.get("serial") or "").strip().upper()
    if sn:
        return sn
    ph = (pd or {}).get("pd_health")
    wwn = (ph.get("wwn") if isinstance(ph, dict) else "") or (pd or {}).get("wwn") or ""
    wwn = str(wwn).strip().upper()
    if wwn:
        return wwn
    return (disk.get("dev") or "").strip()


# ---------------- 运维自定义盘名（v2.3.0 3.8） ----------------
# storcli 的 E0:S3、系统盘符 sda 这类代号人难认；允许给每块盘起「数据盘1」这类好认的名字。
# key 与缺陷趋势同源（_health_key_for：序列号优先 → WWN → 盘符），改名跟着盘走，换盘符/重启不丢。

_DISK_NAME_MAX = 40


def _disk_names_path():
    return os.path.join(_config_dir(), "disk_names.json")


def _disk_names_load():
    d = _load_json_file(_disk_names_path(), {})
    return d if isinstance(d, dict) else {}


def _disk_name_clean(name):
    """清洗自定义盘名：去掉控制字符与首尾空白、超长截断；返回空串表示「清除」。"""
    if name is None:
        return ""
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name))
    return name.strip()[:_DISK_NAME_MAX]


def _disk_health_snapshot_now():
    """采一次当前所有盘的关键计数（走缓存、不唤醒休眠盘、不额外跑命令）。"""
    try:
        disks = get_disks(False) or []
    except Exception:
        disks = []
    try:
        raid = get_raid_card()
    except Exception:
        raid = {}
    pd_map = _raid_pd_by_sn(raid)
    row = {}
    for d in disks:
        if d.get("asleep"):
            continue
        sn = (d.get("serial") or "").strip().upper()
        pd = pd_map.get(sn) or {}
        g = _grade_disk_health(d, pd)
        c = {k: v for k, v in (g.get("counters") or {}).items() if k in _TREND_COUNTERS}
        key = _health_key_for(d, pd)
        if key and c:
            row[key] = c
    return {"t": time.time(), "disks": row}


def _health_trend_for(key, counters):
    """和上一次采样比：返回 {deltas:{}, delta_total, prev_ts}；没有上一条时返回 None。"""
    if not key or not counters:
        return None
    prev = _health_hist_prev(time.time() - 300)   # 至少 5 分钟前的采样才算「上一次」
    if not prev:
        return None
    base = (prev.get("disks") or {}).get(key)
    if not isinstance(base, dict):
        return None
    deltas = {}
    for k in _TREND_COUNTERS:
        cur_v = counters.get(k)
        old_v = base.get(k)
        if isinstance(cur_v, int) and isinstance(old_v, int) and cur_v > old_v:
            deltas[k] = cur_v - old_v
    return {"deltas": deltas, "delta_total": sum(deltas.values()), "prev_ts": prev.get("t")}


def disk_health_loop():
    """daemon：每 6h 落一条缺陷快照。是否该采只看「上一条快照的时间」，
    所以进程重启不会把间隔冲成「一起就采」，趋势才有意义。"""
    time.sleep(20)          # 让预热线程先把 get_disks/get_raid_card 缓存填上
    while True:
        try:
            if time.time() - _health_hist_last_ts() >= DISK_HEALTH_INTERVAL:
                _health_hist_append(_disk_health_snapshot_now())
        except Exception:
            pass
        time.sleep(300)


# 双磁臂盘合并时「取更差的一侧」的字段：缺陷类取大值，绝不因合并而漏报
_MERGE_MAX_KEYS = ("reallocated", "pending", "uncorrectable", "defects",
                   "non_medium_errors", "read_errors", "write_errors",
                   "udma_crc", "percentage_used", "endurance_used")
_GRADE_RANK = {"green": 0, "unknown": 1, "yellow": 2, "red": 3}


def _double_size(s):
    """"7.0T" → "14.0T"（双磁臂整盘标称）。解析不出来就返回空串，前端不显示。"""
    m = re.match(r"^([\d.]+)\s*([A-Za-z]+)$", (s or "").strip())
    if not m:
        return ""
    try:
        v = float(m.group(1)) * 2
    except Exception:
        return ""
    v = int(v) if abs(v - int(v)) < 0.05 else round(v, 1)
    return f"{v}{m.group(2)}"


def _merge_dual_actuator(disks):
    """把同序列号的多个逻辑盘合并成一张卡（典型：双磁臂 SAS 盘每个执行器暴露一个 LUN）。

    本质是一块物理盘，分两张卡显示会让人以为买了两块盘、还可能对着同一块盘
    重复报警。合并规则：
      - dev 保留主臂原名（自检 / 定位 / 挂载判断等操作仍走它，零改动）；
        新增 devs（全部逻辑名）与 dev_label（"sda/sdb"）供界面显示。
      - 缺陷 / 寿命消耗类字段取「更差的一侧」（取大值；剩余备用取小值）——
        任一执行器有伤就算这块盘有伤，不夸大、也绝不漏。
      - 温度取两臂较高值；容量另给 size_total（每臂 × 臂数）。
      - 分级 / 趋势由调用方在合并后统一重算，避免两臂各报一份。
    """
    by_sn = {}
    for d in disks:
        sn = (d.get("serial") or "").strip().upper()
        if sn:
            by_sn.setdefault(sn, []).append(d)

    out, done = [], set()
    for d in disks:
        sn = (d.get("serial") or "").strip().upper()
        grp = by_sn.get(sn) or []
        if not sn or len(grp) < 2:
            out.append(d)
            continue
        if sn in done:
            continue
        done.add(sn)
        out.append(_merge_disk_group(grp))
    return out


def _merge_disk_group(grp):
    """合并同一物理盘的多条逻辑记录（见 _merge_dual_actuator 的规则）。"""
    lead = dict(grp[0])
    devs = [x.get("dev") for x in grp if x.get("dev")]
    lead["devs"] = devs
    lead["dev_label"] = "/".join(devs)
    lead["dual_actuator"] = True
    if lead.get("size"):
        lead["size_total"] = _double_size(lead["size"])
    for k in _MERGE_MAX_KEYS:
        vals = [x.get(k) for x in grp if isinstance(x.get(k), (int, float))]
        if vals:
            lead[k] = max(vals)
    temps = [x.get("temp") for x in grp if isinstance(x.get("temp"), (int, float))]
    if temps:
        lead["temp"] = max(temps)
    spares = [x.get("available_spare") for x in grp if isinstance(x.get("available_spare"), (int, float))]
    if spares:
        lead["available_spare"] = min(spares)
    bad = [x for x in grp if x.get("health_ok") is False]
    if bad:
        lead["health_ok"] = False
        lead["health"] = bad[0].get("health") or "FAILED"
    # 分级 / 趋势在合并后重算，先清掉可能已存在的旧值
    lead.pop("health_grade", None)
    lead.pop("health_trend", None)
    return lead


def _enrich_disk_channels(disks, raid):
    """给每块盘标注通道来源（阵列卡 / 主板）+ 健康分级与缺陷趋势（v2.3.0 3.4）。"""
    raid_sn = {}
    raid_pd = {}
    if raid and isinstance(raid, dict) and raid.get("mode") == "mega":
        for dv in raid.get("drives", []):
            sn = (dv.get("sn") or "").strip().upper()
            if sn:
                raid_sn[sn] = dv.get("slot", "")
                raid_pd[sn] = dv
    locate_ok = bool(raid.get("locate_supported")) if raid else False
    for d in disks:
        sn = (d.get("serial") or "").strip().upper()
        if sn and sn in raid_sn:
            d["channel"] = f"阵列卡通道 c0:{raid_sn[sn]}"
            d["channel_type"] = "raid"
            # 把阵列卡 slot 与定位支持状态挂到盘上，供前端「物理硬盘信息」卡直接显示定位按钮
            d["slot"] = raid_sn[sn]
            d["locate_supported"] = locate_ok
        else:
            # 阵列卡虚拟盘（硬件 RAID 逻辑盘经控制器暴露为 /dev/sdX，如 MR9362-8i 的 RAID5）：
            # 其 SN 是逻辑盘序列号、不会命中任何物理盘 raid_sn，旧版会误标成
            # 「主板 SAS 直连」，让用户误以为阵列卡判断有 bug。判据：块设备 model 与
            # 阵列卡型号一致（MegaRAID 把 VD 的 model 填成控制器型号），或与某条
            # 逻辑盘的自定义 name 一致 → 标为阵列卡虚拟盘（v2.3.2）。
            _hit_vd = False
            if raid and isinstance(raid, dict) and raid.get("mode") == "mega" \
                    and raid.get("virtual_drives"):
                _m = re.sub(r'[^0-9A-Za-z]', '', (d.get("model") or "")).upper()
                if _m:
                    _card = re.sub(r'[^0-9A-Za-z]', '', (raid.get("model") or "")).upper()
                    _vd_names = {
                        re.sub(r'[^0-9A-Za-z]', '', (v.get("name") or "")).upper()
                        for v in raid["virtual_drives"]
                    } - {""}
                    # 型号匹配三层：① 全等/互含；② 数字核心互含（storcli 报全名
                    # LSIMegaRAIDSAS9361-8i、块设备 inquiry 报短名 MR9361-8i，
                    # 归一化后互不包含，但数字核心 "93618" 一致；数字串 ≥4 位才比，
                    # 防止 "WD30" 这类短数字串误命中）。
                    _cd = re.sub(r'[^0-9]', '', _card)
                    _md = re.sub(r'[^0-9]', '', _m)
                    _digit_hit = bool(_cd) and len(_cd) >= 4 and (_cd in _md or _md in _cd)
                    _hit_card = bool(_card) and (_m == _card or _card in _m
                                                 or _m in _card or _digit_hit)
                    if _hit_card or _m in _vd_names:
                        _v0 = raid["virtual_drives"][0]
                        _lvl = str(_v0.get("type") or "").strip()
                        d["channel"] = "阵列卡虚拟盘" + (f"（{_lvl}）" if _lvl else "（硬件 RAID）")
                        d["channel_type"] = "raid_vd"
                        _hit_vd = True
            if not _hit_vd:
                t = (d.get("type") or "").lower()
                if t == "nvme":
                    d["channel"] = "主板 M.2"
                    d["channel_type"] = "mobo_nvme"
                elif t == "sas":
                    d["channel"] = "主板 SAS 直连"
                    d["channel_type"] = "mobo_sas"
                else:
                    d["channel"] = "主板 SATA 直连"
                    d["channel_type"] = "mobo_sata"
    # 双磁臂盘（同一物理盘拆成多个逻辑盘）先合并成一张卡，再统一算分级 / 趋势
    disks = _merge_dual_actuator(disks)
    # 阵列卡「藏起来」的物理盘：RAID 卡把组阵的盘攥在手里，OS 里没有对应的 /dev/sdX
    # 节点（只暴露合并后的虚拟盘，如 MRS9362-8i），而 get_disks() 走 /sys/block 枚举
    # → 天然看不到它们。表现＝硬盘页只有系统盘 + 阵列虚拟盘（用户 2026-09-18 报
    # 「7 块盘只看到 2 块」）。这里把仅阵列卡可见的物理盘补进列表（按 SN 去重，
    # 系统层已能看到的盘不重复添加），标 channel_type=raid。
    # dev 故意留空：没有块设备节点 → 前端据此禁用依赖 /dev 的操作（自检等）。
    if raid and isinstance(raid, dict) and raid.get("mode") == "mega":
        _known_sn = {(d.get("serial") or "").strip().upper() for d in disks}
        for dv in raid.get("drives", []):
            _sn = (dv.get("sn") or "").strip().upper()
            if _sn and _sn in _known_sn:
                continue
            _intf = (dv.get("intf") or "").upper()
            disks.append({
                "dev": "",                       # 无块设备节点（阵列卡持有）
                "dev_id": "raid:" + str(dv.get("slot", "")),  # v2.3.1：前端用唯一身份证渲染（避免 dev 空串撞 key 导致多块盘只渲染一张）
                "size": dv.get("size") or "",
                "rota": "1",                     # 阵列卡下多为机械盘；前端主要看 size/model
                "model": dv.get("model") or "",
                "serial": dv.get("sn") or "",
                "tran": _intf or "SAS",
                "vendor": dv.get("brand") or "",
                "type": "sas" if _intf == "SAS" else "ata",
                "health": "N/A",                 # 阵列卡侧 SMART 判定在 pd_health 里
                "health_ok": False,
                "asleep": False,
                "temp": dv.get("temp"),          # v2.3.0：阵列卡给的单盘温度
                "power_on_hours": None,
                "rpm": dv.get("rpm") or "",
                "brand": dv.get("brand") or "",
                "feature": dv.get("feature") or "",
                "size_note": dv.get("size_note") or "",
                "channel": "阵列卡通道 c0:" + str(dv.get("slot", "")),
                "channel_type": "raid",
                "slot": dv.get("slot", ""),
                "locate_supported": locate_ok,
                "raid_only": True,               # 前端标记：仅阵列卡可见（无 /dev 节点）
                "dg": dv.get("dg", ""),
                "pd_health": dv.get("pd_health") or {},
            })
            if _sn:
                _known_sn.add(_sn)
    names = _disk_names_load()
    for d in disks:
        pd = raid_pd.get((d.get("serial") or "").strip().upper()) or {}
        key = _health_key_for(d, pd)
        # 自定义盘名（v2.3.0 3.8）：name_key 给前端回传改名请求用；custom_name 空串=未命名
        d["name_key"] = key
        d["custom_name"] = names.get(key) or ""
        # 健康分级（阵列卡侧字段来自 storcli，系统侧来自 smartctl 已解析字段）+ 缺陷趋势
        try:
            grade = _grade_disk_health(d, pd)
            d["health_grade"] = grade
            d["health_trend"] = _health_trend_for(key, grade.get("counters") or {})
        except Exception as e:
            shell.log("grade disk health failed: " + str(e))
    return disks
