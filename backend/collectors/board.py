# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend import shell
from backend.caching import _ttl_cache
from backend.shell import DMIDECODE
from backend.shell import read_file
import os
import re
import shutil


# ===================== 采集：系统资源 =====================
# ===================== 采集：主板 / 内存（dmidecode） =====================
def _mem_brand_cn(manu):
    """把内存条制造商英文字符串映射为中文品牌（未知原样返回）"""
    if not manu:
        return ""
    m = manu.strip().upper()
    table = [
        ("SAMSUNG", "三星"), ("SK HYNIX", "海力士"), ("HYNIX", "海力士"),
        ("KINGSTON", "金士顿"), ("MICRON", "美光"), ("CRUCIAL", "英睿达"),
        ("CORSAIR", "海盗船"), ("G.SKILL", "芝奇"), ("G SKILL", "芝奇"),
        ("KINGMAX", "宇瞻"), ("ADATA", "威刚"), ("APACER", "宇瞻"),
        ("TRANSCEND", "创见"), ("TEAM", "十铨"), ("WESTERN", "西数"),
        ("WD", "西数"), ("INTEL", "英特尔"), ("RAMAXEL", "记忆科技"),
        ("ELPIDA", "尔必达"), ("NANYA", "南亚"),
        ("GALAXY MICROSYSTEMS", "影驰"), ("GALAX", "影驰"),
        # —— 国产 / 工控·工规厂商（固件里常是原始英文串，此前会原样显示，见论坛反馈①）——
        # 注：POWERCHIP 须排在 PSC 之前，先命中更长的键，避免 "PSC" 抢先。
        ("UNIIC", "紫光"), ("JINHUA", "晋华"), ("JHICC", "晋华"),
        ("CXMT", "长鑫存储"), ("GIGADEVICE", "兆易创新"),
        ("POWERCHIP", "力晶"), ("PSC", "力晶"),
        ("WINBOND", "华邦"), ("ESMT", "晶豪科技"),
        ("ETRON", "钰创"), ("RENESAS", "瑞萨"),
    ]
    for key, cn in table:
        if key in m:
            return cn
    # JEDEC 十六进制厂商码（SPD 仅含厂商码、无可读品牌名时 dmidecode 输出）
    hex_table = {
        "CE": "三星", "04E8": "三星",
        "AD": "海力士", "04D5": "海力士",
        "2C": "美光", "2D": "美光", "FF": "美光",
        "98": "金士顿", "04": "金士顿",
        "8892": "影驰(GALAX)", "8922": "影驰(GALAX)",
    }
    if re.fullmatch(r"[0-9A-Fa-f]+", manu.strip()):
        code = manu.strip().upper()
        return hex_table.get(code, f"未知(厂商码0x{code})")
    return manu.strip()


def get_chipset():
    """用 lspci 的 Host bridge 设备 ID 推断芯片组系列。
    必须用 lspci -nn：plain lspci 在系统带 pciids 数据库时会打印设备中文/英文全名而不再带
    "Device XXXX"，导致正则抓不到 ID、识别成「未知」。-nn 强制输出 [vendor:device] 原始号。
    - Intel：用 [8086:xxxx] 原始号按代范围映射（不依赖 pciids 数据库，主流板不再误报未知）。
    - AMD：用 [1022:xxxx] 判定为 AMD 平台后，再用 /proc/cpuinfo 的 Ryzen 型号名推断代数
           （比易变的设备号表更稳，不会把 5600G 误标成 3000 系之类）。"""
    out = shell.sudo_cmd(["lspci", "-nn"], 5)
    intel_hid = ""
    amd_hid = ""
    for line in out.splitlines():
        if "Host bridge" in line:
            m = re.search(r"\[8086:([0-9a-fA-F]{4})\]", line)
            if not m:
                m = re.search(r"Device ([0-9a-fA-F]{4})", line)  # 兜底：个别环境 -nn 未输出 [vendor:device]
            if m:
                intel_hid = m.group(1).lower()
                break
            m = re.search(r"\[1022:([0-9a-fA-F]{4})\]", line)
            if not m:
                m = re.search(r"Device ([0-9a-fA-F]{4})", line)
            if m and not amd_hid:
                amd_hid = m.group(1).lower()
    if intel_hid:
        hid = intel_hid
        table = {
            "190f": "100/200 系列（6/7代酷睿）", "1910": "100 系列", "1900": "100 系列（如 H110/B150）",
            "590f": "200 系列（7代）", "5910": "200 系列",
            "3e0f": "300 系列（8/9代酷睿）", "3ec2": "300 系列", "3e30": "300 系列", "3e31": "300 系列", "3e35": "300 系列",
            "3e10": "300 系列（8/9代酷睿）", "3e1f": "300 系列", "3e32": "300 系列", "3e33": "300 系列",
            "9b00": "400 系列（10代）", "9b41": "400 系列",
            "4600": "600 系列（12代）", "4601": "600 系列", "4610": "600 系列",
            "7900": "700 系列（13代）", "7a00": "700 系列", "7d00": "700 系列",
            "a700": "800 系列（14代）", "a780": "800 系列",
        }
        if hid in table:
            return "Intel " + table[hid]
        # 范围兜底：覆盖同代未逐一列举的细分型号（如 Z390 的 3e30/3e35 等 Host bridge）
        try:
            h = int(hid, 16)
        except ValueError:
            return "Intel 未知芯片组（Host bridge 0x%s）" % hid
        if 0x1900 <= h <= 0x191f or 0x5900 <= h <= 0x591f:
            return "Intel 100/200 系列（6/7代酷睿）"
        if 0x3e00 <= h <= 0x3e3f or 0x3ec0 <= h <= 0x3ecf:
            return "Intel 300 系列（8/9代酷睿）"
        if 0x9b00 <= h <= 0x9bff:
            return "Intel 400 系列（10代）"
        if 0x4c00 <= h <= 0x4cff or 0x9a00 <= h <= 0x9aff:
            return "Intel 500 系列（11代）"
        if 0x4600 <= h <= 0x46ff:
            return "Intel 600 系列（12代）"
        if 0x7900 <= h <= 0x79ff or 0x7a00 <= h <= 0x7aff or 0x7d00 <= h <= 0x7dff:
            return "Intel 700 系列（13代）"
        if 0xa700 <= h <= 0xa7ff or 0xa780 <= h <= 0xa78f:
            return "Intel 800 系列（14代）"
        # 15 代及更新的未知号（Arrow Lake 等 0xa800+/0xb000+）广谱兜底，避免再掉「未知」
        if 0xa000 <= h <= 0xafff or 0xb000 <= h <= 0xbfff:
            return "Intel 较新系列芯片组（14/15代及以后）"
        return "Intel 未知芯片组（Host bridge 0x%s）" % hid
    if amd_hid:
        return _amd_chipset_label()
    return ""


def _amd_chipset_label():
    """AMD 平台：读 /proc/cpuinfo 的 Ryzen 型号名推断代数（稳定、不易错）。"""
    cpu = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    if "Ryzen" in cpu:
                        break
    except Exception:
        cpu = ""
    m = re.search(r"Ryzen\s+\d*\s*(\d{4})", cpu)
    if m:
        d = m.group(1)[0]  # 型号首数字：1→1代, 3→3000, 5→5000, 7→7000, 9→9000 ...
        return {
            "1": "AMD Ryzen 1000 系列平台（Zen）",
            "2": "AMD Ryzen 2000 系列平台（Zen+）",
            "3": "AMD Ryzen 3000 系列平台（Zen 2）",
            "4": "AMD Ryzen 4000 APU 平台（Zen 2）",
            "5": "AMD Ryzen 5000 系列平台（Zen 3）",
            "7": "AMD Ryzen 7000 系列平台（Zen 4）",
            "8": "AMD Ryzen 8000 APU 平台（Zen 4）",
            "9": "AMD Ryzen 9000 系列平台（Zen 5）",
        }.get(d, "AMD 平台（具体芯片组未知）")
    return "AMD 平台（具体芯片组未知）"


@_ttl_cache(60)
def get_board():
    """主板信息：优先 /sys/class/dmi/id（免 root），空则 dmidecode；
    DMI 全空（准系统/工控白牌板常见）时尝试读取手动标注，最后回退芯片组识别。"""
    def _read_dmi_sysfs(name):
        v = read_file(f"/sys/class/dmi/id/{name}").strip()
        # fnOS 对这些字段固定返回 "Default string" / "To be filled by O.E.M." 等占位符
        if v.lower() in ("", "default string", "to be filled by o.e.m.", "not specified", "unknown"):
            return ""
        return v

    manufacturer = _read_dmi_sysfs("board_vendor")
    product = _read_dmi_sysfs("board_name")
    version = _read_dmi_sysfs("board_version")
    bios_vendor = _read_dmi_sysfs("bios_vendor")
    bios_version = _read_dmi_sysfs("bios_version")
    bios_date = _read_dmi_sysfs("bios_date")

    # sysfs 拿不到时再退 dmidecode（需 root）
    if not manufacturer or not product:
        out = shell.sudo_cmd([DMIDECODE, "-t", "2"], 8)
        if not out:
            out = shell.sudo_cmd([DMIDECODE, "-t", "1"], 8)
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Manufacturer:") and not manufacturer:
                manufacturer = s.split(":", 1)[1].strip()
            elif s.startswith("Product Name:") and not product:
                product = s.split(":", 1)[1].strip()
            elif s.startswith("Version:") and not version:
                version = s.split(":", 1)[1].strip()
        if manufacturer.lower() in ("default string", "to be filled by o.e.m.", "not specified"):
            manufacturer = ""
        if product.lower() in ("default string", "to be filled by o.e.m.", "not specified"):
            product = ""

    b = {
        "manufacturer": manufacturer,
        "product": product,
        "version": version,
        "bios_vendor": bios_vendor,
        "bios_version": bios_version,
        "bios_date": bios_date,
        "chipset": "",
        "note": "",
    }

    # DMI 未写入厂商信息：提示用户用芯片组推断（已取消手动标注功能）
    if not b["manufacturer"] or not b["product"]:
        b["note"] = "BIOS 未写入主板厂商/型号（DMI 为空），以下为芯片组推断"
    # 芯片组：始终用 lspci 推断（大牌主板也能显示，更准确）
    b["chipset"] = get_chipset()
    return b


def _clean_mfr(s):
    """清理 decode-dimms 的厂商名（去掉 '? (Invalid parity)' 等后缀）"""
    s = s.strip()
    s = re.sub(r"\?.*$", "", s).strip()      # 去掉问号及之后
    s = re.sub(r"\(.*?\)", "", s).strip()     # 去掉括号内容
    return s


def _real_serial(s: str) -> str:
    """dmidecode 的序列号常有占位假值（00000000 / Not Specified / To Be Filled By O.E.M.），
    这类不算真序列号，返回空串让前端显示「不适用」。"""
    s = (s or "").strip()
    if not s:
        return ""
    if s.lower() in ("00000000", "not specified", "none", "n/a", "unknown",
                     "to be filled by o.e.m.", "填充"):
        return ""
    return s


def _spd_mfg_date_and_serial(dev_path: str):
    """从 SPD EEPROM 原始字节解析生产日期(字节 120-121)与序列号(122-125)。
    返回 (mfg_date, serial)；取不到或字节为非法填充(0x00/0xFF/0xC0)则返回 ('', '')。
    廉价/工控条常不写这两项（158 这根 Galaxy 8G DDR4-2133 即如此，字节是 0xC0 填充），
    属正常——前端据此显示「不适用（该内存条未写入）」。与 decode-dimms 同源（读同一份 eeprom），
    decode-dimms 4.3 对这些条也选择不显示这两项。"""
    if not dev_path:
        return "", ""
    base = dev_path.rstrip("/").split("/")[-1]
    cands = [dev_path] if dev_path.endswith("eeprom") else []
    cands.append(f"/sys/bus/i2c/devices/{base}/eeprom")
    dev = next((c for c in cands if os.path.exists(c)), None)
    if not dev:
        return "", ""
    try:
        with open(dev, "rb") as f:
            raw = f.read(320)
    except Exception:
        return "", ""
    def bcd(b):
        return (b >> 4) * 10 + (b & 0xF)
    # 生产日期：DDR3/DDR4 字节 120=年(BCD，偏移 2000)、121=周(BCD)
    mfg = ""
    if len(raw) >= 122:
        yb, wb = raw[120], raw[121]
        if yb not in (0x00, 0xFF, 0xC0) and wb not in (0x00, 0xFF, 0xC0):
            y, w = bcd(yb), bcd(wb)
            if 8 <= y <= 99 and 1 <= w <= 53:
                mfg = "20%02d 年第 %d 周" % (y, w)
    # 序列号：字节 122-125 为 ASCII（常见 4 字节，可能带尾随空格）
    ser = ""
    if len(raw) >= 126:
        sb = raw[122:126]
        if sb not in (b"\x00\x00\x00\x00", b"\xff\xff\xff\xff", b"\xc0\xc0\x00\xc2"):
            txt = "".join(chr(b) if 32 <= b < 127 else "" for b in sb).strip()
            if txt:
                ser = txt
    return mfg, ser


def _mem_ecc_flag(total_width):
    """由 Total Width 推断是否带 ECC（72/80/144/256 bit 含校验位即 ECC；64/32 为非 ECC）。"""
    tw = (total_width or "").strip()
    if not tw:
        return ""
    if re.search(r"\b(72|80|144|256)\b", tw):
        return "ECC"
    if re.search(r"\b(64|32)\b", tw):
        return "非 ECC"
    return ""


def get_memory_edac():
    """读取 EDAC 子系统内存错误计数（ce=可纠正 / ue=不可纠正）。
    消费级主板通常未启用 EDAC（/sys/devices/system/edac/mc 不存在），
    此时 available=False，前端显「本机未启用 EDAC（消费级主板常见）」。"""
    base = "/sys/devices/system/edac/mc"
    if not os.path.isdir(base):
        return {"available": False, "ce": 0, "ue": 0, "controllers": []}
    ce = ue = 0
    controllers = []
    try:
        for mc in sorted(os.listdir(base)):
            mcp = os.path.join(base, mc)

            def _rd(p):
                try:
                    with open(p) as f:
                        return f.read().strip()
                except Exception:
                    return ""

            mc_ce = _rd(os.path.join(mcp, "ce_count"))
            mc_ue = _rd(os.path.join(mcp, "ue_count"))
            if mc_ce != "" or mc_ue != "":
                c = int(mc_ce or 0)
                u = int(mc_ue or 0)
            else:
                c = u = 0
                for cs in os.listdir(mcp):
                    if cs.startswith("csrow"):
                        csp = os.path.join(mcp, cs)
                        c += int(_rd(os.path.join(csp, "ce_count")) or 0)
                        u += int(_rd(os.path.join(csp, "ue_count")) or 0)
            ce += c
            ue += u
            controllers.append({"name": mc, "ce": c, "ue": u})
    except Exception:
        pass
    return {"available": True, "ce": ce, "ue": ue, "controllers": controllers}


def get_memory_from_decodedimms():
    """用 decode-dimms 直读 SPD（JEP106 解码），拿到权威的模组厂/颗粒厂/型号/频率。
    仅在 i2c-tools 已安装且 SPD 可读时返回非空列表。"""
    dd = (shutil.which("decode-dimms") or "")
    if not dd:
        return []
    out = shell.sudo_cmd([dd], 15)
    if not out:
        return []
    mods = []
    cur = {}
    def flush(c):
        if c.get("size_gb"):
            mods.append(c)
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Guessing DIMM") or s.startswith("Decoding EEPROM") or s.startswith("Memory Serial Presence Detect"):
            flush(cur)
            cur = {}
            if s.startswith("Decoding EEPROM"):
                mm = re.match(r"Decoding EEPROM:\s*(\S+)", s)
                cur["_dev"] = mm.group(1) if mm else ""
            continue
        # decode-dimms 用「字段名<多个空格>值」的固定列格式，按 2+ 空格切分
        parts = re.split(r"\s{2,}", s, 1)
        if len(parts) != 2:
            continue
        k, v = parts[0].strip(), parts[1].strip()
        if k == "Fundamental Memory type":
            cur["type"] = v
        elif k == "Module Type":
            cur["module_type"] = v
        elif k == "Maximum module speed":
            cur["speed"] = v.split("(")[0].strip()
        elif k == "Size":
            mb = re.search(r"(\d+)\s*MB", v, re.I)
            if mb:
                cur["size"] = v
                cur["size_gb"] = int(mb.group(1)) / 1024
            else:
                gb = re.search(r"(\d+)\s*GB", v, re.I)
                if gb:
                    cur["size"] = v
                    cur["size_gb"] = int(gb.group(1))
        elif k == "Module Manufacturer":
            cur["module_mfr"] = _clean_mfr(v)
        elif k == "DRAM Manufacturer":
            cur["dram_mfr"] = _clean_mfr(v)
        elif k == "Part Number":
            cur["part"] = "" if v.lower() in ("undefined", "none", "-") else v
    flush(cur)
    # 原始 SPD 补生产日期 / 序列号（decode-dimms 对空字段不输出，这里直接读 eeprom 字节）
    for m in mods:
        dev = m.pop("_dev", "")
        mfg, ser = _spd_mfg_date_and_serial(dev)
        m["manufacture_date"] = mfg
        m["spd_serial"] = ser
    # 编号 + 品牌中文
    for i, m in enumerate(mods, 1):
        m["locator"] = f"DIMM{i}"
        mod_cn = _mem_brand_cn(m.get("module_mfr", ""))
        dram_cn = _mem_brand_cn(m.get("dram_mfr", ""))
        m["brand"] = mod_cn or dram_cn
        m["manufacturer"] = m.get("module_mfr", "")
        m["dram_manufacturer"] = dram_cn
    return mods


@_ttl_cache(60)
def get_memory_modules():
    """内存插槽信息：用 dmidecode -t 17 枚举所有物理插槽（含空），
    已安装槽再用 decode-dimms（SPD 直读）补权威品牌/颗粒厂。
    返回 slots(总插槽数)/installed(已装数)/empty(空槽数)/modules(含空槽)。"""
    def _size_gb(sz):
        mb = re.search(r"(\d+)\s*MB", sz, re.I)
        if mb:
            return int(mb.group(1)) / 1024
        gb = re.search(r"(\d+)\s*GB", sz, re.I)
        if gb:
            return int(gb.group(1))
        return 0

    # 1) decode-dimms 先拿已安装槽的权威 SPD 品牌（按 DIMM 顺序）
    spd_mods = get_memory_from_decodedimms()
    spd_by_idx = {i: m for i, m in enumerate(spd_mods)}

    # 2) dmidecode -t 17 枚举全部物理插槽（含空），尽量抓全可用字段
    out = shell.sudo_cmd([DMIDECODE, "-t", "17"], 12)
    slots_raw = []
    cur = {}
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Memory Device"):
            if cur:
                slots_raw.append(cur)
            cur = {}
            continue
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip()
        if k == "Locator":
            cur["locator"] = v
        elif k == "Bank Locator":
            cur["bank"] = v
        elif k == "Manufacturer":
            cur["manufacturer"] = v
        elif k == "Part Number":
            cur["part"] = v
        elif k == "Size":
            cur["size"] = v
        elif k == "Type":
            cur["type"] = v
        elif k == "Serial Number":
            cur["serial"] = v
        elif k == "Speed":
            cur["speed"] = v
        elif k == "Configured Memory Speed":
            cur["cfg_speed"] = v
        elif k == "Rank":
            cur["rank"] = v
        elif k == "Total Width":
            cur["total_width"] = v
        elif k == "Data Width":
            cur["data_width"] = v
        elif k == "Configured Voltage":
            cur["voltage"] = v
        elif k == "Form Factor":
            cur["form_factor"] = v
        elif k == "Type Detail":
            cur["type_detail"] = v
    if cur:
        slots_raw.append(cur)

    # 2.5) dmidecode -t 16 板级内存阵列（最大支持容量 / 插槽总数 / ECC 类型）
    board_out = shell.sudo_cmd([DMIDECODE, "-t", "16"], 12)
    board_mem = {}
    for line in board_out.splitlines():
        s = line.strip()
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip()
        if k == "Maximum Capacity":
            board_mem["max_capacity"] = v
        elif k == "Number Of Devices":
            board_mem["num_devices"] = v
        elif k == "Error Correction Type":
            board_mem["ecc_type"] = "" if v.lower() in ("none", "not specified") else v

    # 3) 组装：空槽标记 installed=False；已安装槽优先用 SPD 品牌
    modules = []
    spd_i = 0
    total_gb = 0
    if slots_raw:
        for idx, slot in enumerate(slots_raw):
            sz = (slot.get("size") or "").strip()
            installed = _size_gb(sz) > 0
            if installed:
                spd = spd_by_idx.get(spd_i)
                spd_i += 1
                mgb = _size_gb(sz)
                total_gb += mgb
                modules.append({
                    "locator": slot.get("locator") or slot.get("bank", f"DIMM{idx}"),
                    "installed": True,
                    "brand": (spd.get("brand") if spd else "") or _mem_brand_cn(slot.get("manufacturer", "")),
                    "manufacturer": (spd.get("manufacturer") if spd else slot.get("manufacturer", "")),
                    "dram_manufacturer": (spd.get("dram_manufacturer") if spd else ""),
                    "part": spd.get("part") if spd else slot.get("part", ""),
                    "size": sz,
                    "size_gb": mgb,
                    "type": (spd.get("type") if spd else slot.get("type", "")),
                    "speed": (spd.get("speed") if spd else (slot.get("cfg_speed") or slot.get("speed", ""))),
                    "cfg_speed": slot.get("cfg_speed", ""),
                    "rank": slot.get("rank", ""),
                    "total_width": slot.get("total_width", ""),
                    "data_width": slot.get("data_width", ""),
                    "voltage": slot.get("voltage", ""),
                    "form_factor": slot.get("form_factor", ""),
                    "type_detail": slot.get("type_detail", ""),
                    "ecc": _mem_ecc_flag(slot.get("total_width", "")),
                    "manufacture_date": (spd.get("manufacture_date") if spd else ""),
                    "serial": _real_serial(slot.get("serial", "")) or (spd.get("spd_serial") if spd else ""),
                    "source": "spd" if spd else "dmidecode",
                })
            else:
                modules.append({
                    "locator": slot.get("locator") or slot.get("bank", f"DIMM{idx}"),
                    "installed": False,
                    "brand": "",
                    "manufacturer": "",
                    "dram_manufacturer": "",
                    "part": "",
                    "size": "空",
                    "size_gb": 0,
                    "type": "",
                    "speed": "",
                    "serial": "",
                    "source": "empty",
                })
    else:
        # dmidecode 不可用：仅 SPD 已安装槽（无法枚举空槽）
        for idx, m in enumerate(spd_mods):
            mgb = m.get("size_gb", 0)
            total_gb += mgb
            modules.append({
                "locator": m.get("locator", f"DIMM{idx}"),
                "installed": True,
                "brand": m.get("brand", ""),
                "manufacturer": m.get("manufacturer", ""),
                "dram_manufacturer": m.get("dram_manufacturer", ""),
                "part": m.get("part", ""),
                "size": m.get("size", ""),
                "size_gb": mgb,
                "type": m.get("type", ""),
                "speed": m.get("speed", ""),
                "cfg_speed": "",
                "rank": "",
                "total_width": "",
                "data_width": "",
                "voltage": "",
                "form_factor": "",
                "type_detail": "",
                "ecc": "",
                "manufacture_date": m.get("manufacture_date", ""),
                "serial": _real_serial(m.get("spd_serial", "")),
                "source": "spd",
            })

    slots = len(modules)
    installed_n = sum(1 for m in modules if m["installed"])
    empty_n = slots - installed_n
    brands = {}
    for m in modules:
        if m["brand"]:
            brands[m["brand"]] = brands.get(m["brand"], 0) + 1
    edac = get_memory_edac()
    return {
        "modules": modules,
        "total_gb": total_gb,
        "slots": slots,
        "installed": installed_n,
        "empty": empty_n,
        "brand_summary": ", ".join(f"{k}×{v}" for k, v in brands.items()) or "未知",
        "max_capacity": board_mem.get("max_capacity", ""),
        "num_devices": board_mem.get("num_devices", ""),
        "ecc_type": board_mem.get("ecc_type", ""),
        "edac": edac,
    }


# 常见主板传感器温度测点中文名（保留原始名在 raw 字段，方便高级用户核对数据手册）
_TEMP_NAME_ZH = {
    # Nuvoton NCT67xx 常见测点
    "SYSTIN": "主板温度",
    "CPUTIN": "主板·CPU 区域",
    "AUXTIN0": "扩展温度探头 0",
    "AUXTIN1": "扩展温度探头 1",
    "AUXTIN2": "扩展温度探头 2",
    "AUXTIN3": "扩展温度探头 3",
    "AUXTIN4": "扩展温度探头 4",
    "AUXTIN5": "扩展温度探头 5",
    "PECI Agent 0": "CPU PECI 代理 0",
    "PECI Agent 1": "CPU PECI 代理 1",
    # PCH / 芯片组相关（部分主板传感器芯片会额外暴露）
    "PCH_CHIP_TEMP": "PCH 芯片组温度",
    "PCH_CHIP_CPU_MAX_TEMP": "PCH 芯片组最高温度",
    "PCH_CPU_TEMP": "PCH CPU 温度",
    "PCH_MCH_TEMP": "PCH 内存控制器温度",
    # 内存 / 代理
    "Agent0 Dimm0": "内存 DIMM0 温度",
    "Agent0 Dimm1": "内存 DIMM1 温度",
    "Agent1 Dimm0": "内存 DIMM0 温度（通道 1）",
    "Agent1 Dimm1": "内存 DIMM1 温度（通道 1）",
    # 通用 / 其他
    "Composite": "复合温度",
    "THRM": "热敏电阻",
    "NB": "北桥温度",
    "Sensor 0": "传感器 0",
    "Sensor 1": "传感器 1",
    "Sensor 2": "传感器 2",
    "SMBUSMASTER 0": "SMBus 主控 0",
    "SMBUSMASTER 1": "SMBus 主控 1",
    "TSI0_TEMP": "TSI 温度 0",
    "TSI1_TEMP": "TSI 温度 1",
    "Tctl": "CPU 温度控制",
    "Tdie": "CPU 晶粒温度",
}


def _temp_name_zh(raw_name, chip_prefix=None):
    """把传感器原始英文名翻译成中文；Core N / Package id N / TccdN 单独处理。"""
    if raw_name in _TEMP_NAME_ZH:
        return _TEMP_NAME_ZH[raw_name]
    m = re.match(r"Core\s+(\d+)", raw_name)
    if m:
        return f"CPU 核心 {m.group(1)}"
    m = re.match(r"Tccd(\d+)", raw_name)
    if m:
        return f"CPU CCD{m.group(1)} 温度"
    m = re.match(r"Package id\s+(\d+)", raw_name)
    if m:
        return "CPU 封装温度" if m.group(1) == "0" else f"CPU 封装温度 {m.group(1)}"
    return raw_name
