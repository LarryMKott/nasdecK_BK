"""单盘健康分级（纯计算、无 I/O）。

忠实副本来源：backend/health/diskhealth.py::_grade_disk_health（v2.3.3）。
判定逻辑逐行等价；差异仅：返回 DiskHealthGrade 实体（to_dict() 与旧 dict 逐字节一致）。
"""
from backend.domain.entities.disk import DiskHealthGrade

_PD_BAD_STATES = ("FAILED", "RBAD", "UBAD", "OFFLN", "OFFL", "MISSING", "MISSOFF")

_HEALTH_META = {
    "red": {"label": "异常", "order": 3},
    "yellow": {"label": "注意", "order": 2},
    "green": {"label": "正常", "order": 1},
    "unknown": {"label": "未知", "order": 0},
}


def grade_disk_health(disk, pd=None) -> DiskHealthGrade:
    """给单块盘分级：red（异常）/ yellow（注意）/ green（正常）/ unknown（未知）。"""
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
        return DiskHealthGrade(level="unknown", label="未知",
                               reasons=("硬盘休眠中，暂时读不到 SMART 数据",),
                               counters={}, source="休眠")

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
    return DiskHealthGrade(level=level, label=_HEALTH_META[level]["label"], reasons=tuple(reasons),
                           counters=counters, source=" + ".join(src) or "—")
