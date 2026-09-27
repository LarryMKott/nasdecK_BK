# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend import shell
from backend.collectors.board import _temp_name_zh
from backend.collectors.disks import _list_all_disk_devs
from backend.collectors.disks import get_disk_temps_cached
from backend.collectors.raid import get_raid_card
from backend.shell import SENSORS
from backend.state import _TEMP_SNAP
from backend.state import _TEMP_SNAP_LOCK
import json
import re
import time


# ===================== 统一温度采集（一次采样、处处共享） =====================
# 历史问题：CPU/主板/温度墙/控速线程各自跑 sensors -j、各带各的缓存，导致
# ① coretemp 的 temp1_input（驱动虚拟偏高读数）被当 CPU 温度，虚高 ~10°C；
# ② 控速线程每 0.6s 跑一次 sensors；③ 温度墙 30s 才刷新，显示不及时。
# 统一方案：后台采集循环 ~2s 一次 sensors -j → 一份 _TEMP_SNAP 快照；
# 所有温度消费方（/api/fan/temps、get_system、控速线程、get_fan_status）读同一份快照。
# 硬盘(4s TTL)与阵列卡(12~60s TTL)走各自已有缓存并入快照。

def _parse_cpu_temp(j):
    """统一 CPU 封装温度解析（纯函数，不跑命令）。
    1) AMD：k10temp/zenpower 的 Tdie（真实量纲）优先，Tctl 次之；
    2) Intel coretemp：封装温度 = 各核心(Core N)测点最大值，排除 temp1_input
       （coretemp 驱动的"虚拟最高点"读数，曾致 CPU 温度虚高 ~10°C）；
    3) 兜底：任意 Package/Tdie/Tctl 标签取 max（coretemp 仍排除 temp1_input）。"""
    j = j or {}
    for chip, entries in j.items():
        c = str(chip).lower()
        if c.startswith("k10temp") or c.startswith("zenpower"):
            best = None
            for ename, fields in (entries or {}).items():
                if not isinstance(fields, dict):
                    continue
                en = str(ename).lower()
                if "tdie" in en:
                    for k, v in fields.items():
                        if k.endswith("_input") and isinstance(v, (int, float)):
                            return float(v)
                if "tctl" in en and best is None:
                    for k, v in fields.items():
                        if k.endswith("_input") and isinstance(v, (int, float)):
                            best = float(v)
            if best is not None:
                return best
    core_vals, pkg_vals = [], []
    for chip, entries in j.items():
        if not str(chip).lower().startswith("coretemp"):
            continue
        for ename, fields in (entries or {}).items():
            if not isinstance(fields, dict):
                continue
            en = str(ename)
            vals = [float(v) for k, v in fields.items()
                    if k.startswith("temp") and k.endswith("_input")
                    and k != "temp1_input" and isinstance(v, (int, float))]
            if re.match(r"^Core\s+\d+$", en):
                core_vals.extend(vals)
            elif "package" in en.lower():
                pkg_vals.extend(vals)
    if core_vals:
        return max(core_vals)
    if pkg_vals:
        return max(pkg_vals)
    fallback = []
    for chip, entries in j.items():
        for ename, fields in (entries or {}).items():
            if not isinstance(fields, dict):
                continue
            en = str(ename).lower()
            if "package" in en or "tdie" in en or "tctl" in en:
                for k, v in fields.items():
                    if k.startswith("temp") and k.endswith("_input") and isinstance(v, (int, float)):
                        if str(chip).lower().startswith("coretemp") and k == "temp1_input":
                            continue
                        fallback.append(float(v))
    return max(fallback) if fallback else None

def _parse_mb_temp(j):
    """统一主板温度解析（纯函数）。

    优先级（均带合理性护栏：0~90°C 视为有效，过滤传感器错误值）：
      1) acpitz —— ACPI 固件报的「系统环境温度」，多数主板最稳、不易虚高
                  （很多主板的 SYSTIN 是错的，论坛反馈据此优先取 acpitz）
      2) SYSTIN  —— hwmon 上的精确测点
      3) 排除 coretemp / AUXTIN 后的最高温
      4) 非 coretemp 最高温（兜底）
    任一来源无效/缺失则自动回落到下一来源，避免把传感器错误值当真。
    """
    temps = []
    for chip, entries in (j or {}).items():
        if not isinstance(entries, dict):
            continue
        for ename, fields in entries.items():
            if not isinstance(fields, dict) or ename == "Adapter":
                continue
            for k, v in fields.items():
                if k.startswith("temp") and k.endswith("_input") and isinstance(v, (int, float)):
                    temps.append((str(chip), str(ename), float(v)))
    if not temps:
        return None

    def _sane(v):
        return isinstance(v, (int, float)) and 0 <= v <= 90

    # 1) acpitz（ACPI 系统环境温度）优先
    acpitz = [t for t in temps if str(t[0]).lower().startswith("acpitz") and _sane(t[2])]
    if acpitz:
        return acpitz[0][2]

    # 2) SYSTIN 精确测点
    systin = [t for t in temps if t[1].strip().upper() == "SYSTIN" and _sane(t[2])]
    if systin:
        return systin[0][2]

    # 3) 排除 coretemp / AUXTIN 后的最高温
    mb = [t for t in temps
          if "coretemp" not in t[0].lower()
          and not t[1].strip().upper().startswith("AUXTIN")
          and _sane(t[2])]
    if mb:
        return max(t[2] for t in mb)

    # 4) 非 coretemp 最高温（兜底）
    mb2 = [t for t in temps if "coretemp" not in t[0].lower() and _sane(t[2])]
    if mb2:
        return max(t[2] for t in mb2)

    # 5) 实在没有合理值，返回全部里的最高（保持旧行为兜底）
    return max(t[2] for t in temps)

def _parse_sensors_all(j, mb_temp=None):
    """统一传感器全量解析（温度墙测点 + 电压）：与旧 get_system 内联解析同口径，
    抽成纯函数供采集循环与 get_system 共用，保证两处数据完全一致。"""
    temps, voltages = [], []
    for chip, entries in (j or {}).items():
        cs = str(chip).split("-")[0]
        for ename, fields in (entries or {}).items():
            if not isinstance(fields, dict) or ename == "Adapter":
                continue
            for fn, fv in fields.items():
                if not fn.endswith("_input") or not isinstance(fv, (int, float)):
                    continue
                prefix = fn.replace("_input", "")
                if fn.startswith("temp"):
                    if str(ename).strip().upper().startswith("AUXTIN"):
                        continue
                    # 主板监控芯片(ITE/Nuvoton/Winbond/SMSC/Fintek/VIA/ADI/LM9x 等 hwmon 驱动)的原始细分测点
                    # (temp1/temp2/SYSTIN/CPUTIN...)对普通用户是噪音，统一交给 _parse_mb_temp 收口成单条「主板温度」，
                    # 不在温度墙逐个展示（之前中文化导致出现多个「主板(CPU附近)/主板(系统)/主板」重复条目，太乱）。
                    # GPU 驱动芯片(i915/amdgpu/radeon/nouveau)同样过滤：温度页已从 gpus 实时数据渲染
                    # 「核显/独显」条目（核显=CPU 封装同 die 口径），hwmon 这条是重复且口径不一致的噪音。
                    if cs.startswith(("it", "nct", "w83", "sch", "f718", "via", "adt", "lm9")) or cs == "acpitz":
                        continue
                    if cs.startswith(("i915", "amdgpu", "radeon", "nouveau")):
                        continue
                    if fv < -50 or fv > 150:
                        continue
                    mx = fields.get(prefix + "_max")
                    cr = fields.get(prefix + "_crit")
                    if cs != "coretemp":
                        mx = cr = None
                    if mx is not None and (mx < 0 or mx > 150):
                        mx = None
                    if cr is not None and (cr < 0 or cr > 150):
                        cr = None
                    nm = _temp_name_zh(ename, cs) if cs == "coretemp" else (
                        "PCH 芯片组" if cs.startswith("pch") else _temp_name_zh(ename, cs))
                    if cs == "coretemp" and "package" in str(ename).lower():
                        # 系统温度合集归口：CPU 封装温度的权威来源只有 _parse_cpu_temp（核心 max、排除
                        # temp1_input 虚拟偏高值）。温度墙此条目直接引用该值，不再自己取 Package 字段，
                        # 否则会与 hero/三页 CPU 温度出现第二套语义差（曾差 2°C）。
                        fv = _parse_cpu_temp(j)
                        # 某些机器 coretemp 只暴露 Package 没有逐核测点，_parse_cpu_temp 会返回 None，
                        # 不拦住下面 int(float(fv)) 直接崩（/api/system 整页 500）。
                        if not isinstance(fv, (int, float)):
                            continue
                        if mx is None:
                            for _k, _v in fields.items():
                                if _k.startswith("temp") and _k.endswith("_max") and _k != "temp1_max" and isinstance(_v, (int, float)):
                                    mx = _v
                                    break
                        if cr is None:
                            for _k, _v in fields.items():
                                if _k.startswith("temp") and _k.endswith("_crit") and _k != "temp1_crit" and isinstance(_v, (int, float)):
                                    cr = _v
                                    break
                    temps.append({"name": nm, "raw": str(ename), "value": int(float(fv) + 0.5), "max": mx, "crit": cr})
                    break
                elif fn.startswith("in"):
                    v = fv / 1000 if fv > 100 else fv
                    nm = str(ename)
                    if "3.3V" in nm:
                        nm = "+3.3V"
                    elif "VSB" in nm:
                        nm = "3VSB 待机"
                    elif "bat" in nm.lower():
                        nm = "CMOS 电池"
                    voltages.append({"name": nm, "value": round(v, 2)})
                    break
    # 主板综合温度：由 _parse_mb_temp 算法统一收口成单条「主板温度」（ACPI 优先，SYSTIN 兜底），
    # 取代 hwmon 芯片一堆细分测点(temp1/temp2/SYSTIN/CPUTIN...)，避免温度墙噪音。
    if mb_temp is not None and not any(_t.get("name") == "主板温度" for _t in temps):
        temps.append({"name": "主板温度", "raw": "mb", "value": int(float(mb_temp) + 0.5), "max": None, "crit": None})
    return temps, voltages

# CPU 温度 EMA 平滑状态：核心温度热容量小，瞬时负载下 1~2 秒可跳 10°C+（如 39→47），
# 显示与风扇控速都不宜追着尖峰跑。用指数加权平均（前值 50% + 新值 50%，采集周期 2s）
# 让温度渐进变化；风扇温控同源，转速也更稳。进程内状态，重启后首个读数直接采纳。
_CPU_TEMP_EMA = {"v": None}

def _smooth_cpu_temp(raw):
    if raw is None:
        return _CPU_TEMP_EMA["v"]   # 读不到时沿用上次（避免闪 None/空）
    e = _CPU_TEMP_EMA["v"]
    if e is None:
        e = float(raw)
    else:
        e = e * 0.5 + float(raw) * 0.5
    _CPU_TEMP_EMA["v"] = e
    return int(e + 0.5)   # 显示取整（四舍五入）；EMA 内部保留 float 精度

def _temp_snapshot_read():
    with _TEMP_SNAP_LOCK:
        return dict(_TEMP_SNAP)

def _temp_collect_loop():
    """统一温度采集循环（~2s）：一次 sensors -j 解析出 CPU/主板/全量测点，
    硬盘走 4s 缓存、阵列卡走 12~60s 缓存，汇总成一份快照。
    所有温度消费方读同一份快照，消除重复采样与口径不一致。"""
    while True:
        try:
            sens_j = shell.run_cmd([SENSORS, "-j"], 8)
            j = json.loads(sens_j) if sens_j else {}
            mb = _parse_mb_temp(j)
            temps, voltages = _parse_sensors_all(j, mb)
            # CPU 封装温度：EMA 平滑（防核心瞬时尖峰秒跳）。快照 cpu_temp 与温度墙
            # "CPU 封装温度"条目写同一个平滑值，两处保持一致；风扇温控同源转速更稳。
            raw_cpu = _parse_cpu_temp(j)
            cpu = _smooth_cpu_temp(raw_cpu)
            if isinstance(cpu, (int, float)):
                for _t in temps:
                    if _t.get("name") == "CPU 封装温度":
                        _t["value"] = int(cpu + 0.5)
                        break
            devs = _list_all_disk_devs()
            states = get_disk_temps_cached(devs) if devs else {}
            raid_temp = None
            raid_controller_temp = None
            raid_drives = []
            try:
                _raid = get_raid_card()
                if isinstance(_raid, dict):
                    # 风扇控温 / 温度墙以 ROC 芯片温度(更热、更贴近过热风险)为基准；
                    # 控制器温度单独透传，供前端与对照工具(飞牛/storcli)对得上。
                    raid_temp = _raid.get("roc_temp")
                    raid_controller_temp = _raid.get("controller_temp")
                    # 阵列卡物理盘（带温度）并入快照，供温度页显示 SAS 物理盘温度。
                    # RAID5 等虚拟盘场景下，物理盘不暴露 /dev 节点、OS 盘列表看不到，
                    # 只能经 storcli 拿到 Drive Temperature；按序列号去重，OS 已可见的盘不重复列出。
                    _os_sn = {(st.get("serial") or "").strip().upper() for st in states.values() if isinstance(st, dict)}
                    for dv in (_raid.get("drives") or []):
                        _sn = (dv.get("sn") or "").strip().upper()
                        if _sn and _sn in _os_sn:
                            continue
                        if dv.get("temp") is None:
                            continue
                        raid_drives.append({
                            "slot": dv.get("slot"),
                            "model": dv.get("model") or "",
                            "serial": dv.get("sn") or "",
                            "temp": dv.get("temp"),
                            "intf": (dv.get("intf") or "").upper(),
                            "size": dv.get("size") or "",
                        })
            except Exception:
                pass
            with _TEMP_SNAP_LOCK:
                _TEMP_SNAP["t"] = time.time()
                _TEMP_SNAP["cpu_temp"] = cpu
                _TEMP_SNAP["mb_temp"] = int(mb + 0.5) if isinstance(mb, (int, float)) else None
                _TEMP_SNAP["temps"] = temps
                _TEMP_SNAP["voltages"] = voltages
                _TEMP_SNAP["disks"] = states
                _TEMP_SNAP["raid_drives"] = raid_drives
                _TEMP_SNAP["raid_temp"] = raid_temp
                _TEMP_SNAP["raid_controller_temp"] = raid_controller_temp
        except Exception:
            pass
        time.sleep(2)

def _mb_temp_from_sensors(sensors):
    temps = (sensors or {}).get("temps", []) or []
    # 优先取 acpitz 优先后的「主板温度」主值（论坛反馈：SYSTIN 很多主板错，acpitz 更稳）
    _primary = [t for t in temps if t.get("name") == "主板温度"]
    if _primary:
        try:
            return float(_primary[0]["value"])
        except (TypeError, ValueError):
            pass
    cand = []
    for t in temps:
        name = (t.get("name") or "")
        if any(k in name for k in ("主板", "PCH", "芯片组", "南桥", "PCIe", "System")):
            try:
                cand.append(float(t.get("value")))
            except (TypeError, ValueError):
                pass
    return max(cand) if cand else None
