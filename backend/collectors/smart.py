# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
import re


# ===================== 采集：硬盘 SMART =====================
def parse_sas_smart(text):
    """解析 SAS/SCSI 盘 SMART"""
    d = {}
    m = re.search(r"SMART Health Status:\s*(\w+)", text)
    d["health"] = m.group(1) if m else "UNKNOWN"
    m = re.search(r"Current Drive Temperature:\s*(\d+)\s*C", text)
    d["temp"] = int(m.group(1)) if m else None
    m = re.search(r"Drive Trip Temperature:\s*(\d+)\s*C", text)
    d["temp_trip"] = int(m.group(1)) if m else 60
    m = re.search(r"Accumulated power on time, hours:minutes\s*(\d+):(\d+)", text)
    d["power_on_hours"] = int(m.group(1)) if m else None
    m = re.search(r"Elements in grown defect list:\s*(\d+)", text)
    d["defects"] = int(m.group(1)) if m else 0
    m = re.search(r"Pending defect count:\s*(\d+)", text)
    d["pending"] = int(m.group(1)) if m else 0
    m = re.search(r"Non-medium error count:\s*(\d+)", text)
    d["non_medium_errors"] = int(m.group(1)) if m else 0
    # 错误计数表（转 int：分级函数的 _int 只认整数，字符串会被静默丢掉）
    read_line = re.search(r"read:.*?(\d+)\s+(\d+)$", text, re.M)
    d["read_errors"] = int(read_line.group(2)) if read_line else 0
    write_line = re.search(r"write:.*?(\d+)\s+(\d+)$", text, re.M)
    d["write_errors"] = int(write_line.group(2)) if write_line else 0
    # 累计读写量：Error counter log 的 "Gigabytes processed" 列（10^9 字节，盘固件终身累计）。
    # SAS 盘只在这一处报总读写（已与希捷私有日志双通道核对一致）；老 SATA 机械盘固件不记，天然拿不到。
    for key, tag in (("data_units_read", "read"), ("data_units_written", "write")):
        m = re.search(rf"^{tag}:(?:\s+\d+){{5}}\s+(\d+(?:\.\d+)?)\s+\d+\s*$", text, re.M)
        if m:
            try:
                gb = float(m.group(1))
                # 与 ATA 241/242 同格式："精确值 [人话值]"，前端 dataUnitShort 取中括号
                d[key] = f"{gb:,.0f} GB [{gb/1000:.1f} TB]"
            except ValueError:
                pass
    return d

def parse_ata_smart(text):
    """解析 ATA/SATA 盘 SMART"""
    d = {}
    m = re.search(r"SMART overall-health self-assessment test result:\s*(\w+)", text)
    d["health"] = m.group(1) if m else "UNKNOWN"
    d["temp"] = None
    d["power_on_hours"] = None
    d["reallocated"] = 0
    d["pending"] = 0
    d["uncorrectable"] = 0
    d["udma_crc"] = 0
    d["raw_read_errors"] = "0"
    attrs = {}
    for line in text.splitlines():
        # 格式: ID# NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE
        m = re.match(r"^\s*(\d+)\s+(\S+)\s+0x\w+\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+?)\s*$", line)
        if m:
            aid, name = m.group(1), m.group(2)
            val, worst, thresh = m.group(3), m.group(4), m.group(5)
            raw_str = m.group(6)
            # raw_value 取第一个数字（温度等可能是 "41 (Min/Max -1/56)"；某些版本 smartctl 会对大数加逗号）
            num_m = re.match(r"\s*([\d,]+)", raw_str)
            raw_num = int(num_m.group(1).replace(",", "")) if num_m else 0
            attrs[aid] = {"name": name, "value": val, "worst": worst, "thresh": thresh, "raw": raw_str.strip()}
            if aid == "9":
                d["power_on_hours"] = raw_num
            elif aid == "194":
                d["temp"] = raw_num
            elif aid == "5":
                d["reallocated"] = raw_num
            elif aid == "197":
                d["pending"] = raw_num
            elif aid == "198":
                d["uncorrectable"] = raw_num
            elif aid == "199":
                d["udma_crc"] = raw_num
            elif aid == "1":
                d["raw_read_errors"] = raw_str.strip()
    d["attrs"] = attrs

    # 总读写量（SMART 241/242，部分盘才提供；命名随厂商：Lifetime_Writes_GiB / Total_LBAs_Written 等）
    # 统一转成与 NVMe 一致的 "原始值 [人话容量]" 格式；盘不提供就是 None，前端不显示。
    def _fmt_rw(raw, kind):
        try:
            n = int(str(raw).replace(",", "").strip())
        except Exception:
            return None
        if n <= 0:
            return None
        if kind == "gib":  # raw 值即 GiB（金士顿等盘的 Lifetime_Writes/Reads_GiB）
            tb = n * (1024 ** 3) / 1e12
        else:  # raw 值是 LBA 数（Total_LBAs_Written/Read，512B/扇区）
            tb = n * 512 / 1e12
        m = f"{n:,}"
        return f"{m} [{tb:.1f} TB]" if tb >= 0.1 else f"{m} [{tb * 1000:.0f} GB]"

    for aid, key in (("241", "data_units_written"), ("242", "data_units_read")):
        if d.get(key):
            continue
        a = attrs.get(aid)
        if not a:
            continue
        name = (a.get("name") or "")
        if "Writes" in name or "Reads" in name or "Written" in name:
            kind = "gib" if "GiB" in name else "lba"
            d[key] = _fmt_rw(a.get("raw"), kind)
    return d

def parse_nvme_smart(text):
    """解析 NVMe 盘 SMART（smartctl -a /dev/nvmeXnY）"""
    d = {}
    m = re.search(r"SMART overall-health self-assessment test result:\s*(\w+)", text)
    d["health"] = m.group(1) if m else "UNKNOWN"
    d["temp"] = None
    d["power_on_hours"] = None
    d["percentage_used"] = None
    d["available_spare"] = None
    d["critical_warning"] = "0"
    d["data_units_read"] = None
    d["data_units_written"] = None
    # 温度：NVMe 可能报 Kelvin（>200 视为开尔文转摄氏）或 Celsius
    m = re.search(r"Temperature:\s*(\d+)\s*Kelvin", text)
    if m:
        d["temp"] = int(m.group(1)) - 273
    else:
        m = re.search(r"Temperature:\s*(\d+)\s*Celsius", text)
        if m:
            d["temp"] = int(m.group(1))
    m = re.search(r"Power On Hours:\s*([\d,]+)", text)
    d["power_on_hours"] = int(m.group(1).replace(",", "")) if m else None
    m = re.search(r"Percentage Used:\s*(\d+)%", text)
    d["percentage_used"] = int(m.group(1)) if m else None
    m = re.search(r"Available Spare:\s*(\d+)%", text)
    d["available_spare"] = int(m.group(1)) if m else None
    m = re.search(r"Critical Warning:\s*0x([0-9a-fA-F]+)", text)
    d["critical_warning"] = m.group(1) if m else "0"
    m = re.search(r"Data Units Read:\s*([\d,]+)(\s*\[[^\]]*\])?", text)
    d["data_units_read"] = (m.group(1) + (m.group(2) or "")).strip() if m else None
    m = re.search(r"Data Units Written:\s*([\d,]+)(\s*\[[^\]]*\])?", text)
    d["data_units_written"] = (m.group(1) + (m.group(2) or "")).strip() if m else None
    return d
