# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.command import cmd_runner as shell
from backend.infrastructure.caching import _ttl_cache
from backend.infrastructure.command.cmd_runner import STORCLI
import os
import re


# ===================== 采集：阵列卡 =====================
def detect_storage_controllers():
    """用 lspci 检测存储控制器，区分 MegaRAID(IR) 与 HBA(IT) 直通卡"""
    out = shell.run_cmd(["lspci", "-nn"], 10)
    controllers = []
    for line in out.splitlines():
        # 只按设备类型识别（RAID/SAS/SCSI/HBA），不限制厂商白名单，
        # 换任意品牌阵列卡/HBA（LSI/Broadcom/Areca/HighPoint/Adaptec/...）都能自动纳入
        if not re.search(r"(RAID|SAS|SCSI|HBA)", line, re.I):
            continue
        m = re.search(r":\s*(.+)$", line)
        model = m.group(1).strip() if m else line.strip()
        is_megaraid = bool(re.search(r"MegaRAID", line, re.I))
        # 含 SAS/HBA 但不含 MegaRAID → 视为 HBA 直通卡（IT 模式）
        is_hba = bool(re.search(r"SAS|HBA", line, re.I)) and not is_megaraid
        controllers.append({"model": model, "is_megaraid": is_megaraid, "is_hba": is_hba})
    return controllers

def _storcli_size_to_decimal(size_str):
    """storcli 把二进制 TiB/GiB 误标成 TB/GB，这里换算回十进制显示（如 6.366 TB -> 7.0T）"""
    try:
        m = re.match(r"^([\d.]+)\s*(TB|GB|MB)$", size_str.strip(), re.I)
        if not m:
            return size_str
        num = float(m.group(1))
        unit = m.group(2).upper()
        # storcli 实际是按二进制：TB=TiB(1024^4)、GB=GiB(1024^3)
        bytes_ = num * (1024 ** 4 if unit == "TB" else 1024 ** 3 if unit == "GB" else 1024 ** 2)
        tb = bytes_ / 1e12
        if tb >= 1:
            return f"{tb:.1f}T"
        gb = bytes_ / 1e9
        return f"{gb:.0f}G"
    except Exception:
        return size_str

def _resolve_brand_model(model, inquiry_model):
    """解析阵列卡物理盘用于品牌识别的型号。

    storcli 表格的 model 列常丢厂商前缀（如 SATA 盘只给 `SV300S37A/120G`，
    而 `KINGSTON` 在上一列）。若表格型号无厂商前缀、但 Inquiry Data / Model Number
    含前缀，则改用完整型号，避免把 `KINGSTON SV300S37A/120G` 误判为三星。
    详见 v1.7.8 品牌修复。行为保持与内联逻辑完全一致。
    """
    brand_model = model
    if inquiry_model and inquiry_model != "-":
        _known = ("ST", "WD", "WDC", "TOSHIBA", "HGST", "HUH", "HUS", "INTEL",
                  "KINGSTON", "CT", "CRUCIAL", "MICRON", "SANDISK", "PNY", "HITACHI", "SAMSUNG")
        _tbl_vendor = model.upper().startswith(_known) or "SAMSUNG" in model.upper()
        _inq_vendor = inquiry_model.upper().startswith(_known) or "SAMSUNG" in inquiry_model.upper()
        if (not _tbl_vendor) and _inq_vendor:
            brand_model = inquiry_model
    return brand_model


def disk_brand_and_feature(model):
    """根据型号解析硬盘品牌与特性（如双磁臂/双执行器）。返回 (brand_cn, feature)"""
    model_u = (model or "").strip().upper()
    # 品牌识别
    if model_u.startswith("ST"):
        brand = "希捷(Seagate)"
    elif model_u.startswith(("WD", "WDC")):
        brand = "西部数据(WD)"
    elif "TOSHIBA" in model_u:
        brand = "东芝(Toshiba)"
    elif model_u.startswith(("HGST", "HUH", "HUS")):
        brand = "HGST(日立)"
    elif "SAMSUNG" in model_u:
        brand = "三星(Samsung)"
    elif model_u.startswith("INTEL"):
        brand = "英特尔(Intel)"
    elif model_u.startswith("KINGSTON"):
        brand = "金士顿(Kingston)"
    elif model_u.startswith(("CT", "CRUCIAL")):
        brand = "英睿达(Crucial)"
    elif "MICRON" in model_u:
        brand = "美光(Micron)"
    elif "SANDISK" in model_u:
        brand = "闪迪(SanDisk)"
    elif model_u.startswith("PNY"):
        brand = "PNY"
    elif "HITACHI" in model_u:
        brand = "日立(Hitachi)"
    else:
        brand = ""
    # 双磁臂（双执行器）识别：已知 Seagate Exos 2X 系列
    dual_models = {"ST14000NM0001", "ST10000NM0096", "ST18000NM000J", "ST20000NM007D"}
    feature = "双磁臂(双执行器)" if model_u in dual_models else ""
    return brand, feature

@_ttl_cache(600)
def _smart_rpm_by_serial():
    """扫描所有 /dev/sdX，建立 {序列号(大写): 转速文本} 映射。
    storcli 不提供真实转速，必须用 smartctl -i 取 Rotation Rate（7200 rpm / 固态）。
    """
    rpm_map = {}
    try:
        devs = [d for d in os.listdir("/dev") if re.match(r"^sd[a-z]+$", d)]
    except Exception:
        return rpm_map
    for d in sorted(devs):
        try:
            out = shell.sudo_cmd(["smartctl", "-n", "standby", "-i", "/dev/" + d], 8)
        except Exception:
            continue
        sn_m = re.search(r"Serial\s*(?:number|Number)\s*:\s*(\S+)", out)
        rpm_m = re.search(r"Rotation Rate:\s*(.+)", out)
        if not (sn_m and rpm_m):
            continue
        rpm = rpm_m.group(1).strip()
        if "Solid State" in rpm or "SSD" in rpm:
            rpm = "固态(SSD)"
        rpm_map[sn_m.group(1).strip().upper()] = rpm
    return rpm_map

def _parse_roc_temp(text):
    """从 storcli 输出中解析阵列卡主控芯片温度(ROC Temperature)，兼容多种格式。

    已验证兼容：
      - MegaRAID /c0 show :  "ROC temperature = 56"
      - HBA /c0 show temperature : "ROC temperature(Degree Celsius) 65" (无等号)
    注意：ROC 温度与 Controller Temperature 是两个不同的物理传感器，值通常相差几度
    （ROC 偏高）。本函数只匹配 ROC，控制器温度请用 _parse_ctrl_temp。
    """
    if not text:
        return None
    m = re.search(r"ROC\s+temperature\s*(?:\([^)]*\))?\s*=?\s*(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"ROC\s+temperature.*?(\d+)", text, re.I)  # 兜底：极宽松匹配 ROC 后第一个数字
    return int(m.group(1)) if m else None


def _parse_ctrl_temp(text):
    """从 storcli 输出中解析阵列卡控制器/板载环境温度(Controller Temperature)。

    这是飞牛界面、storcli 摘要、多数对照工具默认展示的温度；与 ROC temperature 是
    两个不同的物理传感器，值通常相差几度（Controller 偏低）。v2.3.1 起与 ROC 分开解析、分开展示。
    """
    if not text:
        return None
    m = re.search(r"Controller\s+Temperature\s*=?\s*(\d+)", text, re.I)
    return int(m.group(1)) if m else None


def _parse_cachevault(out):
    """从 storcli /c0 show 输出解析 CacheVault/BBU 状态，返回 (显示文本, normalized_status)。

    不同卡/不同 storcli 版本输出格式差异很大：
      - CVPM02 Optimal 25C
      - CVPM02 - -
      - CacheVault_Info 块里 Status = Optimal
      - CacheVault = Present / Missing
      - 老卡用 BBU Optimal 28C
    本函数尽量兼容常见格式；解析失败返回 ("未检测到", None)。
    """
    if not out:
        return "未检测到", None

    # 1. CVPMxx Status Temp 格式（CVPM02 Optimal 25C / CVPM02 Degraded -）
    m = re.search(r"(CVPM\w+)\s+(\S+)\s+(\d+C|\d+\s*°C|-+|N/A|n/a|--)", out, re.I)
    if m:
        cvpm, status, temp = m.group(1), m.group(2).strip(), m.group(3).strip()
        norm = status.lower()
        # 温度显示友好化
        temp_disp = temp if temp not in ("-", "--", "N/A", "n/a", "") else "-"
        display = f"{cvpm} {status.title()} {temp_disp}".strip()
        return display, norm

    # 2. CacheVault_Info / Status 块（Status = Optimal / Missing / Present）
    #    CacheVault 与 Status 可能不在同一行，用有限跨行窗口匹配
    m = re.search(r"CacheVault[\s\S]{0,200}?Status\s*=\s*(\S+)", out, re.I)
    if m:
        status = m.group(1).strip()
        return f"CacheVault {status}", status.lower()

    # 3. 简单的 "CacheVault = ..." 键值
    m = re.search(r"CacheVault\s*=\s*(\S+)", out, re.I)
    if m:
        status = m.group(1).strip()
        return f"CacheVault {status}", status.lower()

    # 4. 老卡 BBU 备份电池单元
    m = re.search(r"BBU\s+(\S+)\s+(\d+C|\d+\s*°C|-+|N/A|n/a|--)", out, re.I)
    if m:
        status = m.group(1).strip()
        return f"BBU {status.title()}", status.lower()

    # 5. BBU Status = ...
    m = re.search(r"BBU[\s\S]{0,200}?Status\s*=\s*(\S+)", out, re.I)
    if m:
        status = m.group(1).strip()
        return f"BBU {status}", status.lower()

    return "未检测到", None


def _parse_vds_from_topology(out):
    """从 storcli /c0 show 的 Virtual Drives 表解析 Virtual Drive（逻辑盘）元信息。

    v2.3.2 修复：旧版遇第一条 '----' 分隔线就把 in_topo 置 False，但真实 storcli
    输出里分隔线夹在表头和数据行之间（表头行 → ---- → 数据行 → ----），
    导致数据行全部被跳过、virtual_drives 恒为空 → 有硬件 RAID 逻辑盘的机器上
    「逻辑盘/一致性检查/热备盘/CopyBack」等区块被误判为"无逻辑盘"整块隐藏
    （论坛 RAID5 用户反馈的根因）。现改为：分隔线仅跳过，遇到非数据行的
    实质内容（下一段标题/图例行）才结束；表头匹配同时放宽大小写
    （不同固件有 'TYPE'/'Type' 两种写法）。"""
    vds = []
    in_topo = False
    for line in out.splitlines():
        s = line.strip()
        if 'DG/VD' in s.upper() and 'TYPE' in s.upper() and 'STATE' in s.upper():
            in_topo = True
            continue
        if not in_topo:
            continue
        if not s:
            continue
        # 分隔线：表头前后、表尾都有，一律跳过（v2.3.2 前会把 in_topo 置 False 导致漏行）
        if re.match(r'^[-=]+$', s):
            continue
        parts = s.split()
        # 数据行特征：首列必须是 'DG/VD' 形如 0/0；其余任何实质行（"Physical Drives = 3"、
        # "Cac=CacheCade|..." 图例）都视为表格结束
        if len(parts) < 6 or not re.match(r'^\d+/\d+$', parts[0]):
            in_topo = False
            continue
        dgvd, vtype, state, access = parts[0], parts[1], parts[2], parts[3]
        consist, cache_code = parts[4], parts[5]
        # JBOD 盘不是逻辑盘（部分固件把它列进 TOPOLOGY）：过滤掉，避免纯直通机器
        # 被误判"有逻辑盘"而显示「阵列卡增强」——该区块的功能（一致性检查/热备/
        # CopyBack/缓存策略）对 JBOD 盘无意义
        if vtype.upper() == "JBOD":
            continue
        # 尾部按固件差异列数不同（Cac/sCC 列可有可无），Size/Name 用"第一个数字+可选单位"定位
        rest = parts[6:]
        size, name = '', ''
        for i, tok in enumerate(rest):
            if re.match(r'^\d+(\.\d+)?$', tok):
                unit = rest[i + 1] if len(rest) > i + 1 and re.match(r'^[TGMK]B?$', rest[i + 1]) else ''
                size = tok + ((' ' + unit) if unit else '')
                name = ' '.join(rest[i + 2:]) if len(rest) > i + 2 else ''
                break
        vds.append({
            "dgvd": dgvd, "type": vtype, "state": state, "access": access,
            "consist": consist, "cache_code": cache_code, "cac": "", "scc": "",
            "size": size, "name": name,
            "write_policy": "", "read_policy": "", "read_cache": "", "wb": None, "cache_raw": ""
        })
    return vds


def _parse_vd_cache_policies(vd_out):
    """从 storcli /c0/vall show 按 DG/VD 块解析每个 VD 的 Cache Policy 文本。"""
    res = {}
    if not vd_out:
        return res
    anchors = [(m.start(), m.group(1)) for m in re.finditer(r"DG/VD:\s*(\d+/\d+)", vd_out)]
    if not anchors:
        anchors = [(m.start(), m.group(1)) for m in re.finditer(r"Virtual Drive:\s*(\d+)", vd_out)]
    for i, (pos, key) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(vd_out)
        block = vd_out[pos:end]
        cp = re.search(r"Default Cache Policy\s*:\s*(.+)", block)
        res[key] = cp.group(1).strip() if cp else ""
    return res


@_ttl_cache(3600)
def _probe_locate_support(rc_raw=""):
    """只读判断阵列卡/背板是否支持物理盘定位（locate LED 闪烁）。

    关键修正（v2.0.8）：早期版本误把 enclosure 行里出现的 "SGPIO" 字样当作
    「背板支持定位」。但 SGPIO/SES 只是控制器/expander 固件声明的**协议能力**，
    不代表机箱背板的 LED 真的接了定位信号线。实测 NAS-3 V2.5 被动背板：
    enclosure 行 SIM=1、ProdID 占位为 "SGPIO"，storcli start locate 返回
    Succeeded，但机箱盘位灯实际不闪（背板仅接 SATA 活动 LED）。

    因此本函数收紧判据：
      - 必须有 SES 带内管理接口（SIM 列 == 1）
      - 且背板上报了**真实型号**（ProdID 非空、且不是 SGPIO/SES 这类协议占位名）
    二者同时满足才认为「用户点了真能看见灯闪」，否则隐藏定位按钮，
    避免给用户一个「点了没反应」的假功能。

    本函数**绝不触发真实闪灯**。
    """
    if not rc_raw:
        try:
            rc_raw = shell.sudo_cmd([STORCLI, "/c0", "show"], 30) or ""
        except Exception:
            return False
    if not rc_raw:
        return False
    # 解析 Enclosure LIST 块：列序 EID State Slots PD PS Fans TSs Alms SIM Port# ProdID VendorSpecific
    lines = rc_raw.splitlines()
    in_list = False
    for ln in lines:
        s = ln.strip()
        if "Enclosure LIST" in ln:
            in_list = True
            continue
        if not in_list:
            continue
        if not s:
            break  # Enclosure 块结束
        if s.startswith("EID") or s.startswith("---"):
            continue
        if not s[0].isdigit():
            continue
        parts = ln.split()
        if len(parts) < 10:
            continue
        try:
            sim = parts[8]
            prod = parts[10] if len(parts) > 10 else ""
        except Exception:
            continue
        # 需要真有 SES 管理接口，且背板上报了真实型号（非 SGPIO/SES 这类协议占位）
        if sim == "1" and prod and prod not in ("-", "SGPIO", "SES", "SES2", "VendorSpecific"):
            return True
    return False


def _pd_health_from_storcli(out):
    """从 `storcli /c0/eN/sN show all` 文本里取「阵列卡侧」健康字段（v2.3.0 3.4）。

    这条命令 get_raid_card 本来就要为每块盘跑一次（取序列号 / 型号），从这里顺手
    解析健康字段 **不增加任何 storcli 调用**。纯只读，绝不写盘。
    拿不到的字段不塞进结果，调用方按「有没有」决定文案（不猜、不造假数据）。
    """
    d = {}
    if not out:
        return d
    for key, pat in (
        ("media_err", r"Media Error Count\s*=\s*(\d+)"),
        ("other_err", r"Other Error Count\s*=\s*(\d+)"),
        ("bbm_err", r"BBM Error Count\s*=\s*(\d+)"),
        ("pred_fail", r"Predictive Failure Count\s*=\s*(\d+)"),
        ("shield", r"Shield Counter\s*=\s*(\d+)"),
    ):
        m = re.search(pat, out)
        if m:
            try:
                d[key] = int(m.group(1))
            except Exception:
                pass
    m = re.search(r"Drive Temperature\s*=\s*(-?\d+)\s*C", out)
    if m:
        d["temp"] = int(m.group(1))
    m = re.search(r"S\.M\.A\.R\.T alert flagged by drive\s*=\s*(Yes|No)", out, re.I)
    if m:
        d["smart_alert"] = (m.group(1).strip().lower() == "yes")
    # SSD 寿命：不同固件字段名不一样，两种都认（"用掉的比例" / "剩余可写比例"）
    m = re.search(r"Percentage Endurance Utilized\s*=\s*([\d.]+)", out)
    if m:
        try:
            d["endurance_used"] = int(float(m.group(1)))
        except Exception:
            pass
    m = re.search(r"Percentage (?:Remaining )?Rated Write Endurance\s*(?:Remaining)?\s*=\s*([\d.]+)", out)
    if m and "endurance_used" not in d:
        try:
            d["endurance_used"] = max(0, 100 - int(float(m.group(1))))
        except Exception:
            pass
    m = re.search(r"\bWWN\s*=\s*(\S+)", out)
    if m:
        d["wwn"] = m.group(1).strip()
    return d


def _parse_pd_show_all(out):
    """解析 `storcli /c0/eall/sall show all` → {("252","0"): {...}}（v2.3.0）。

    这条命令是**每块盘一段的键值对格式**（`SN = ...` / `Model Number = ...` /
    `Drive Temperature =  38C` / `Raw size = 6.366 TB`），**不是列对齐表格** ——
    所以它天然不受 PD LIST 那种「Size 恒占 2 token、SeSz 占 1 或 2 token」造成的
    **列偏移**影响。修的就是用户报的「7 块盘型号显示成 U/D」：老代码写死
    `parts[12]` 取型号，在 SeSz="512B"（只占 1 token）的盘上整行左移一格，
    取到的其实是 Sp 列；而 158 测试机的盘 SeSz="4 KB"（2 token）恰好抵消才蒙对。

    同时它**一条命令给全部盘**，替代原先「逐盘各跑一次 show all」：
    7 盘从最坏 ~105s（7×15s 超时）降到 ~0.3s，采集线程不再被堵。

    返回 {("252","0"): {"sn","model","temp","wwn","fw","raw_size","state","dg","health"}}
    拿不到的字段一律不塞进结果（调用方按「有没有」决定文案，不猜、不造假数据）。
    """
    res = {}
    if not out:
        return res
    # 段头形如 `Drive /c0/e252/s0 :`；其后还有 `Drive /c0/e252/s0 - Detailed Information :`
    # 等同前缀段，一并归入同一块（split 后每 4 个一组：c、e、s、body）
    chunks = re.split(r"^Drive /c(\d+)/e(\d+)/s(\d+)\s*:", out, flags=re.M)
    for i in range(1, len(chunks) - 3, 4):
        eid, slot, body = chunks[i + 1], chunks[i + 2], chunks[i + 3]
        rec = res.setdefault((eid, slot), {})
        for key, pat in (
            ("sn", r"^SN\s*=\s*(\S+)"),
            ("model", r"^Model Number\s*=\s*(.+?)\s*$"),
            ("wwn", r"^WWN\s*=\s*(\S+)"),
            ("fw", r"^Firmware Revision\s*=\s*(\S+)"),
            ("raw_size", r"^Raw size\s*=\s*([\d.]+\s*\w+)"),
        ):
            m = re.search(pat, body, re.M)
            if m and m.group(1).strip() not in ("", "N/A", "NA", "-"):
                rec[key] = m.group(1).strip()
        # 健康字段（含 Drive Temperature）复用单盘解析 —— 正文就是同一份输出，零额外命令
        h = _pd_health_from_storcli(body)
        if h:
            rec.setdefault("health", {}).update(h)
            if h.get("temp") is not None:
                rec["temp"] = h["temp"]
        # State / DG：从该盘自己的 PD LIST 小表行取。前 4 列位置固定
        # （EID:Slt / DID / State / DG，Size 在 DG 之后），故列偏移不波及这里。
        m = re.search(r"^\s*(\d+:\d+)\s+\d+\s+(\S+)\s+(\S+)\s", body, re.M)
        if m:
            rec["state"] = m.group(2)
            rec["dg"] = m.group(3)
    return res


@_ttl_cache(15)
def get_raid_card():
    data = {"ok": False, "mode": "none", "model": "未检测到",
            "drives": [], "raw": "", "note": "", "controllers": []}
    out = shell.sudo_cmd([STORCLI, "/c0", "show"], 30)
    data["raw"] = out
    # ---- MegaRAID (IR 模式) ----
    if out and "Product Name" in out:
        def grab(pat, default=""):
            m = re.search(pat, out)
            return m.group(1).strip() if m else default
        data["ok"] = True
        data["mode"] = "mega"
        data["model"] = grab(r"Product Name\s*=\s*(.+)")
        data["serial"] = grab(r"Serial Number\s*=\s*(\S+)")
        data["sas_address"] = grab(r"SAS Address\s*=\s*(\S+)")
        data["fw_package"] = grab(r"FW Package Build\s*=\s*(\S+)")
        data["fw_version"] = grab(r"FW Version\s*=\s*(\S+)")
        data["bios_version"] = grab(r"BIOS Version\s*=\s*(\S+)")
        data["driver"] = grab(r"Driver Name\s*=\s*(\S+)") + " " + grab(r"Driver Version\s*=\s*(\S+)")
        data["pci"] = grab(r"PCI Address\s*=\s*(\S+)")
        data["jbod_count"] = grab(r"JBOD Drives\s*=\s*(\d+)", "0")
        # CacheVault / BBU
        cv_text, cv_status = _parse_cachevault(out)
        data["cachevault"] = cv_text
        data["cachevault_status"] = cv_status
        # 阵列卡温度：分两个独立传感器分别解析（v2.3.1）
        #  - ROC temperature        ：主控芯片温度（更高、更贴近过热风险，风扇控温以它为准）
        #  - Controller Temperature ：控制器/板载环境温度（飞牛界面、storcli 摘要、多数对照工具默认读这个）
        # 两者是不同的物理传感器，值通常相差几度（Controller 偏低）；之前只取了 ROC 导致与对照工具对不上。
        roc = _parse_roc_temp(out)
        if roc is None:
            roc = _parse_roc_temp(shell.sudo_cmd([STORCLI, "/c0", "show", "temperature"], 10))
        ctl = _parse_ctrl_temp(out)
        if ctl is None:
            ctl = _parse_ctrl_temp(shell.sudo_cmd([STORCLI, "/c0", "show", "temperature"], 10))
        data["controller_temp"] = ctl
        data["roc_temp"] = roc
        # 物理盘列表。**一条 `/c0/eall/sall show all` 拿全部盘**（v2.3.0）：
        #  ① 型号/序列号/温度走键值对，天然不受 PD LIST 的列偏移影响（用户报的 7 块盘
        #     型号显示成 U/D 就是因为老代码写死 `parts[12]`，而 Size 恒占 2 token、
        #     SeSz 只占 1（512B）或 2（4 KB）token，右侧整体错位一格）；
        #  ② 顺带拿到每块盘的 Drive Temperature，不再依赖逐盘调用；
        #  ③ 7 块盘从最坏 ~105s（7 × 15s 超时）降到 ~0.3s，温度采集线程不再被堵死
        #     （这正是「配置检测页能看到卡温 55°C、温度页/风扇页却看不到」的根因）。
        # 格式: 252:0 21 JBOD - 6.366 TB SAS HDD N N 4 KB ST14000NM0001 U -
        rpm_map = _smart_rpm_by_serial()
        pd_map = _parse_pd_show_all(shell.sudo_cmd([STORCLI, "/c0/eall/sall", "show", "all"], 30))
        drives = []
        seen_slots = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 12 and re.match(r"^\d+:\d+$", parts[0]):
                if parts[0] in seen_slots:
                    continue
                seen_slots.add(parts[0])
                e, s = parts[0].split(":")
                pd = pd_map.get((e, s)) or {}
                sn = pd.get("sn", "")
                inquiry_model = pd.get("model", "")
                pd_health = pd.get("health") or {}
                # 兜底：`/c0/eall/sall show all` 在个别老固件/卡上不可用时，退回逐盘调用
                if not pd:
                    try:
                        sn_out = shell.sudo_cmd([STORCLI, "/c0", "/e" + str(e), "/s" + str(s), "show", "all"], 15)
                        sn_m = re.search(r"SN\s*=\s*(\S+)", sn_out)
                        if sn_m:
                            sn = sn_m.group(1).strip()
                        # 同一份输出里顺带取健康字段（v2.3.0 3.4 单盘健康分级），不额外跑命令
                        pd_health = _pd_health_from_storcli(sn_out)
                        m = re.search(r"Model Number\s*=\s*(.+)", sn_out) or re.search(r"Inquiry Data\s*=\s*(.+)", sn_out)
                        if m:
                            inquiry_model = " ".join(m.group(1).strip().split())
                    except Exception:
                        pass
                # 表格行的**右侧三列（Model / Sp / Type）靠在行尾、位置恒定**，而左侧
                # Size(2 token) 与 SeSz(1 或 2 token) 会随固件变化 —— 所以型号从右往左取，
                # 不用固定下标（老代码的坑）。末列只可能是这几种类型标记，用它做安全性校验。
                tbl_model = ""
                sp = parts[-2] if len(parts) >= 2 else ""
                if len(parts) >= 3 and parts[-1] in ("-", "SSD", "HDD", "SAS", "SATA", "SSD "):
                    tbl_model = parts[-3]
                # 表格列的型号常丢厂商前缀（如 SATA 盘只给 SV300S37A/120G，KINGSTON 在上一列），
                # 直接用该型号做品牌识别会被误判（如 SV 开头误认三星）。
                # 故品牌识别优先用含厂商前缀的完整型号（Model Number / Inquiry Data）。
                brand_model = _resolve_brand_model(tbl_model, inquiry_model)
                brand, feature = disk_brand_and_feature(brand_model)
                # 展示用 model：表格列已够用则保留（与 HDD 显示风格一致），仅在表格缺失时兜底用完整型号
                model = tbl_model
                if (not model or model == "-") and inquiry_model and inquiry_model != "-":
                    model = inquiry_model
                rpm = rpm_map.get(sn.upper(), "")
                if not rpm:
                    rpm = "固态(SSD)" if parts[7].upper() == "SSD" else "—"
                size_dec = _storcli_size_to_decimal(parts[4] + " " + parts[5])
                size_note = ""
                if feature and "双磁臂" in feature:
                    # 双磁臂盘每块执行器向系统暴露一半容量，整盘为 2×；显示整盘标称容量
                    m = re.match(r"^([\d.]+)\s*([TG])$", size_dec)
                    if m:
                        full = float(m.group(1)) * 2
                        size_dec = f"{full:.1f}{m.group(2)}"
                        size_note = f"双磁臂·整盘{size_dec}（每执行器 {(full/2):.1f}{m.group(2)}）"
                drives.append({
                    "slot": parts[0], "did": parts[1], "state": parts[2],
                    "dg": parts[3], "size": size_dec, "size_note": size_note,
                    "intf": parts[6], "media": parts[7],
                    "model": model, "sp": sp,
                    "sn": sn, "rpm": rpm,
                    "brand": brand, "feature": feature,
                    "temp": pd.get("temp"),           # v2.3.0：每块盘的当前温度（show all 键值对）
                    "pd_health": pd_health,          # v2.3.0 3.4：阵列卡侧健康字段（分级用）
                })
        data["drives"] = drives
        # 热备盘（全局 GHS / 专用 DHS）
        hot = []
        for d in drives:
            st = (d.get("state") or "").upper()
            if "GHS" in st:
                d2 = dict(d); d2["hs_type"] = "global"; hot.append(d2)
            elif "DHS" in st:
                d2 = dict(d); d2["hs_type"] = "dedicated"; hot.append(d2)
        data["hotspares"] = hot
        # ---- CopyBack 自动换盘状态 ----
        # Auto CopyBack 是阵列卡固件能力：某盘故障且已配热备盘时，自动把数据复制到热备盘完成换盘。
        # 面板负责「监控状态 + 开关自动 CopyBack + 手动触发」，真正换盘由阵列卡固件执行。
        cb_out = shell.sudo_cmd([STORCLI, "/c0", "show", "copyback"], 10)
        if cb_out and "Auto CopyBack" in cb_out:
            m = re.search(r"Auto\s+CopyBack\s*:\s*(\w+)", cb_out)
            data["auto_copyback"] = m.group(1).strip().lower() if m else "unknown"
        else:
            data["auto_copyback"] = "unknown"
        for d in drives:
            st = (d.get("state") or "").upper()
            d["copyback_active"] = "COPYBACK" in st
            d["failed"] = ("FAILED" in st) or ("RBAD" in st)
        # 待 CopyBack 换盘的盘：已故障 + 存在热备盘 + 已开启自动 CopyBack
        data["copyback_needed"] = [
            {"slot": d["slot"], "model": d.get("model", "")}
            for d in drives
            if d.get("failed") and data["hotspares"] and data["auto_copyback"] == "enabled"
        ]
        # ---- Virtual Drives（逻辑盘）+ 缓存策略 ----
        try:
            vds = _parse_vds_from_topology(out)
            if vds:
                vd_out = shell.sudo_cmd([STORCLI, "/c0", "/vall", "show"], 30)
                cp_map = _parse_vd_cache_policies(vd_out)
                for v in vds:
                    raw = cp_map.get(v["dgvd"], "")
                    up = raw.upper()
                    if up:
                        v["cache_raw"] = raw
                        v["write_policy"] = "WriteBack" if "WRITEBACK" in up else ("WriteThrough" if "WRITETHROUGH" in up else "")
                        v["read_policy"] = "NoReadAhead" if "NOREADAHEAD" in up else ("ReadAhead" if "READAHEAD" in up else "")
                        v["read_cache"] = "Cached" if "CACHED" in up else ("Direct" if "DIRECT" in up else "")
                        v["wb"] = (v["write_policy"] == "WriteBack")
                    else:
                        code = (v.get("cache_code") or "").upper()
                        v["cache_raw"] = code
                        v["write_policy"] = "WriteBack" if len(code) > 1 and code[1] == "W" else ("WriteThrough" if len(code) > 1 and code[1] == "T" else "")
                        v["wb"] = (v["write_policy"] == "WriteBack")
            data["virtual_drives"] = vds
        except Exception as e:
            data["virtual_drives"] = []
            shell.log("parse VD cache failed: " + str(e))
        data["locate_supported"] = _probe_locate_support(data.get("raw", ""))
        return data
    # ---- 非 MegaRAID：判断是否为 HBA 直通卡 / 纯 SATA ----
    controllers = detect_storage_controllers()
    data["controllers"] = controllers
    hba = [c for c in controllers if c["is_hba"]]
    megaraid = [c for c in controllers if c["is_megaraid"]]
    if hba:
        data["ok"] = True
        data["mode"] = "hba"
        data["model"] = hba[0]["model"]
        # HBA 直通卡只有 ROC 芯片温度（无 Controller Temperature 概念），两字段同值
        try:
            _hba_t = _parse_roc_temp(shell.sudo_cmd([STORCLI, "/c0", "show", "temperature"], 10)) \
                or _parse_roc_temp(shell.sudo_cmd([STORCLI, "/c0", "show"], 30))
        except Exception:
            _hba_t = None
        data["controller_temp"] = _hba_t
        data["roc_temp"] = _hba_t
        data["note"] = ("HBA 直通卡（IT 模式）：磁盘由系统内核直接管理，不经阵列卡固件。"
                        "HBA 芯片本身无独立温度传感器，本页不显示阵列卡温度（属正常现象，并非面板异常）。"
                        "每张物理盘的温度与 SMART 信息请见「硬盘 SMART」标签页。")
        return data
    if megaraid:
        data["ok"] = False
        data["mode"] = "mega_error"
        data["model"] = megaraid[0]["model"]
        data["note"] = "检测到 MegaRAID 卡，但 storcli 读取失败，请确认已安装 storcli 且本应用具备 sudo 权限。"
        return data
    data["ok"] = False
    data["mode"] = "none"
    data["note"] = "未检测到独立阵列卡 / HBA（纯 SATA 主板，磁盘由南桥直接管理）。"
    return data
