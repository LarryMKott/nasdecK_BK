# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend import shell
from backend.collectors.cputemp import _temp_snapshot_read
import json
import os
import re
import time


# ---------------------------------------------------------------------------
# GPU 实时占用（温度 + 使用率）：供系统资源页折线图。
# 与 get_gpu（重采集，含 lspci -vvv/modinfo/显存识别）分离——这里只取"会秒级跳动"的
# 两个值，尽量走 sysfs / nvidia-smi，不在 1s 轮询里重跑重型命令。
# 身份列表(lspci)缓存 5 分钟；temp/util 采样 2 秒 memo，api_metrics 每秒读缓存即可。
# 核显使用率依赖 intel_gpu_top（fnOS 未必预装）：取不到就 util=None，前端显示"暂不可用"，不造假数据。
# ---------------------------------------------------------------------------
_GPU_IDENT_CACHE = {"t": 0.0, "data": None}
_GPU_LIVE_CACHE = {"t": 0.0, "data": []}

def _clean_gpu_name(raw):
    """把 lspci/nvidia-smi 给的长设备名精简成「主市场名 + 架构代号」。
    兼容多种真实形态：
      '00:02.0 VGA compatible controller [0300]: Intel Corporation UHD Graphics 610 (Coffee Lake-S GT1) [8086:3e90] (rev 02)'
          -> ('UHD Graphics 610', 'Coffee Lake-S GT1')
      'Intel Corporation CoffeeLake-S GT1 [UHD Graphics 610]'
          -> ('UHD Graphics 610', 'CoffeeLake-S GT1')
      'NVIDIA GeForce RTX 3080' -> ('GeForce RTX 3080', '')
      'Advanced Micro Devices, Inc. [AMD/ATI] Navi 23 [Radeon RX 6600 XT]'
          -> ('Radeon RX 6600 XT', 'Navi 23')
    中文兜底名（如 '未启用（BIOS 可能已禁用）'）原样返回。"""
    if not raw or not str(raw).strip():
        return raw, ""
    n = str(raw).strip()
    # 0) 去掉 lspci 整行前缀：'00:02.0 VGA compatible controller [0300]: '
    n = re.sub(r'^\s*[0-9a-f]{2}:[0-9a-f]{2}\.\d\s+(?:VGA compatible controller|3D controller|Display controller)\s*\[[0-9a-f]{4}\]:\s*', '', n, flags=re.I)
    # 1) 去掉厂商前缀（含 APU 的 [AMD/ATI] 标记）
    n = re.sub(r'^(Intel Corporation|Intel|NVIDIA Corporation|NVIDIA|'
               r'Advanced Micro Devices,?\s*Inc\.?\s*(\[AMD/ATI\])?|AMD/ATI|AMD)\s*',
               '', n, flags=re.I)
    n = n.strip()
    # 2) 去掉结尾的 (rev 02) / [8086:3e90] 这类尾巴
    n = re.sub(r'\s*\(rev[^)]*\)\s*$', '', n, flags=re.I)
    n = re.sub(r'\s*\[[0-9a-f]{4}:[0-9a-f]{4}\]\s*$', '', n, flags=re.I)
    # 3) 方括号里的市场名优先（旧格式：[UHD Graphics 610]）
    mkt = None
    for mm in re.finditer(r'\[([^\]]+)\]', n):
        cand = mm.group(1).strip()
        if cand.lower() in ('amd/ati',):
            continue
        mkt = cand
        break
    arch = re.sub(r'\[[^\]]+\]', '', n).strip()
    arch = re.sub(r'\s{2,}', ' ', arch).strip()
    if mkt and mkt.lower() not in ('device',):
        return mkt, arch
    # 4) 没有方括号：把结尾的架构代号括号当作 arch，如 'UHD Graphics 610 (Coffee Lake-S GT1)'
    m2 = re.match(r'^(.*?)\s*\(([^()]+)\)\s*$', n)
    if m2:
        base = m2.group(1).strip()
        suffix = m2.group(2).strip()
        if base and not re.search(r'(laptop|notebook|rev|version|tm|oc|super|max-?q)', suffix, re.I):
            return base, suffix
    # 5) 连括号都没有、但含已知核显市场名 + 后缀代号，如 'UHD Graphics 610 CoffeeLake-S GT'
    m3 = re.match(r'^(UHD Graphics\s+\d+|HD Graphics\s+\d+)', n, re.I)
    if m3:
        base = m3.group(1).strip()
        suffix = n[m3.end():].strip(" -")
        if suffix and not re.search(r'laptop|notebook', suffix, re.I):
            return base, suffix
    return (n or str(raw)), ''


def _gpu_ident_list():
    """返回 [{vendor, dev, pci, name, type}]，缓存 5 分钟，避免每 2s 跑 lspci。"""
    now = time.time()
    if _GPU_IDENT_CACHE["data"] is not None and now - _GPU_IDENT_CACHE["t"] < 300:
        return _GPU_IDENT_CACHE["data"]
    out = []
    seen = set()
    try:
        lspci = shell.run_cmd(["lspci"], 3)
        for line in lspci.splitlines():
            if not re.search(r"VGA compatible controller|3D controller|Display controller", line, re.I):
                continue
            pci = ""
            pm = re.match(r"^([0-9a-f]{2}:[0-9a-f]{2}\.\d)", line)
            if pm:
                pci = pm.group(1)
            m = re.search(r"controller\s*\[[0-9a-f]{4}\]:\s*(.+?)\s*\[([0-9a-f]{4}):([0-9a-f]{4})\]", line)
            if m:
                name = m.group(1).strip(); vendor = m.group(2).lower(); dev = m.group(3).lower()
            else:
                vendor = ""; name = line.strip(); dev = ""
            if vendor == "8086" or "intel" in name.lower():
                gtype = "核显"
            elif vendor == "1002" and re.search(r"radeon|graphics|apu|vega|renoir|cezanne|phoenix|raphael", name, re.I):
                gtype = "核显"
            else:
                gtype = "独显"
            # 去重：同一 PCI 地址（或同名无地址）只计一次，避免显卡被重复识别成多张
            key = pci if pci else ("name:" + name)
            if key in seen:
                continue
            seen.add(key)
            # 清理名称：短市场名 + 架构代号，原长名留作悬停全名
            sn, sa = _clean_gpu_name(name)
            name_full = (sn + (" (" + sa + ")" if sa else "")) or name
            out.append({"vendor": vendor, "dev": dev, "pci": pci,
                        "name": sn, "name_arch": sa, "name_full": name_full, "type": gtype})
    except Exception:
        out = []
    if not out:
        out = [{"vendor": "", "dev": "", "pci": "", "name": "", "type": "无核显"}]
    _GPU_IDENT_CACHE["data"] = out
    _GPU_IDENT_CACHE["t"] = now
    return out

def _gpu_temp_from_sysfs_live(pci):
    """模块级 GPU 温度读取（按 PCI 在 /sys/class/drm/cardN/device/hwmon 找），复用与 get_gpu 一致的匹配逻辑。"""
    try:
        base = (pci or "").strip()
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
            if base and (base == dev or dev.endswith(base) or base.endswith(dev)):
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

def _amd_gpu_busy(pci):
    """AMD 独显使用率：/sys/class/drm/cardN/device/gpu_busy_percent（0-100）。按 PCI 匹配。"""
    try:
        base = (pci or "").strip()
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
            if base and (base == dev or dev.endswith(base) or base.endswith(dev)):
                bp = os.path.join(devdir, "gpu_busy_percent")
                if os.path.exists(bp):
                    return float(open(bp).read().strip())
    except Exception:
        return None
    return None

def _read_drm_attr(fname):
    """读第一个存在的 /sys/class/drm/card*/device/<fname>，取不到返回 None。"""
    try:
        for name in sorted(os.listdir("/sys/class/drm")):
            if not re.match(r"^card\d+$", name):
                continue
            p = os.path.join("/sys/class/drm", name, "device", fname)
            if os.path.exists(p):
                return int(open(p).read().strip())
    except Exception:
        return None
    return None

# intel_gpu_top 采样结果短缓存（秒）。
# _sample_gpu_live 里会连着取两次（一次拿 util/render/video，util 为空时
# _intel_igpu_util 再取一次做频率兜底），没这层缓存就会启两遍子进程、耗时翻倍。
_IGPU_TOP_CACHE = {"t": 0.0, "d": {}}
_IGPU_TOP_TTL = 1.5

def _intel_igpu_top_sample():
    """Intel 核显：用 intel_gpu_top -J 取 busy/render/video/频率/功耗/rc6。
    返回 dict；取不到则返回空 dict。

    ⚠️ 坑（2026-09-05 踩过）：intel_gpu_top -J 是**流式**输出，每 -s 毫秒吐一个
    JSON 对象且**进程永不退出**。所以：
      · json.loads(整段) 在多个对象拼接时永远解析失败；
      · 更不能用 communicate(timeout=N) —— 进程不退出就必然死等满 N 秒再 kill，
        /api/metrics 被拖到 6 秒一次（一次采样还被调两次），前端 5s 轮询看起来
        就是"数据不跳了"。
    正确做法：os.read 有多少读多少，边读边用 raw_decode 试解析，
    拿到第一个完整 JSON 对象立刻返回并杀进程（实测 0.01s 出数）。
    """
    import subprocess as _sp
    _now = time.time()
    if _now - _IGPU_TOP_CACHE["t"] < _IGPU_TOP_TTL:
        return _IGPU_TOP_CACHE["d"]
    proc = None
    out = {}
    try:
        proc = _sp.Popen(["intel_gpu_top", "-J", "-s", "200", "-o", "-"],
                         stdout=_sp.PIPE, stderr=_sp.DEVNULL)
        buf = b""
        dec = json.JSONDecoder()
        j = None
        deadline = _now + 2.0           # 硬兜底：最多等 2 秒，绝不拖垮请求
        fd = proc.stdout.fileno()
        while time.time() < deadline:
            try:
                chunk = os.read(fd, 65536)
            except Exception:
                break
            if not chunk:               # 进程退出 / EOF
                break
            buf += chunk
            try:
                j, _ = dec.raw_decode(buf.decode("utf-8", "ignore").lstrip())
                break                   # 第一个完整对象就够了
            except ValueError:
                continue                # 还没读完整，继续读
        if isinstance(j, dict):
            engines = j.get("engines") or {}
            busy = []
            render = None
            video = None
            for k, v in engines.items():
                b = (v or {}).get("busy")
                if isinstance(b, (int, float)):
                    busy.append(b)
                    if k.startswith("Render"):
                        render = b
                    elif k.startswith("Video"):
                        video = b
            freq = (j.get("frequency") or {}).get("actual")
            power = (j.get("power") or {}).get("GPU")
            rc6 = (j.get("rc6") or {}).get("value")
            if busy:
                out["util"] = round(sum(busy) / len(busy), 1)
            if render is not None:
                out["render"] = round(render, 1)
            if video is not None:
                out["video"] = round(video, 1)
            if isinstance(freq, (int, float)):
                out["freq_mhz"] = round(freq, 1)
            if isinstance(power, (int, float)):
                out["power_w"] = round(power, 2)
            if isinstance(rc6, (int, float)):
                out["rc6"] = round(rc6, 1)
    except Exception:
        out = {}
    finally:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
    _IGPU_TOP_CACHE["t"] = time.time()
    _IGPU_TOP_CACHE["d"] = out
    return out


def _intel_igpu_util():
    """Intel 核显使用率/活动度。返回 (值, 是否代理)。
    真·使用率优先（intel_gpu_top -J 各 engine busy 平均值）；无该工具则退化为频率活动度。
    都没有返回 (None, False)。"""
    sample = _intel_igpu_top_sample()
    if sample.get("util") is not None:
        return sample["util"], False
    try:
        cur = _read_drm_attr("gt_cur_freq_mhz")
        mn = _read_drm_attr("gt_min_freq_mhz")
        mx = _read_drm_attr("gt_boost_freq_mhz") or _read_drm_attr("gt_max_freq_mhz")
        if cur is not None and mn is not None and mx is not None and mx > mn:
            return round((cur - mn) / (mx - mn) * 100, 1), True
    except Exception:
        pass
    return None, False

def _gpu_memory_bytes():
    """核显共享系统内存：返回 (used_bytes, total_bytes, percent)。
    无 psutil 依赖，直接读 /proc/meminfo；取不到返回 (None,None,None)。"""
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if ':' in line:
                    k, v = line.split(':', 1)
                    mem[k.strip()] = int(v.split()[0])  # KB
        total = mem.get("MemTotal", 0) * 1024
        avail = mem.get("MemAvailable", 0) * 1024
        if total <= 0:
            return None, None, None
        used = total - avail
        return used, total, round(used / total * 100, 1)
    except Exception:
        return None, None, None


def _intel_igpu_vram_bytes():
    """Intel 核显真实显存占用：读 i915 debugfs 的 GEM 对象统计。

    核显没有独立显存，图形缓冲实际占用的是系统内存（共享内存 + BIOS 预留 stolen），
    sysfs 无标准接口（i915 不暴露 mem_info_vram_*），只能读 debugfs 的 i915_gem_objects。
    返回 (used_bytes, total_bytes, percent, basis)；读不到返回 (None, None, None, None)。

    - used  = 当前 GEM 对象实际占用的图形缓冲（首行 shrinkable 的 bytes）
    - total = 共享上限，取系统内存总量（核显没有固定显存上限，故为口径参考值而非硬上限）
    - basis = 'gem'，供前端区分于独显的真实显存口径
    """
    try:
        used = None
        dri_root = "/sys/kernel/debug/dri"
        for name in sorted(os.listdir(dri_root)):
            # 只要真实设备目录（0000:00:02.0 或数字编号），跳过 bridges / client-* / by-path 等
            if not re.match(r"^(\d{4}:[\da-fA-F:.]+|\d+)$", name):
                continue
            p = os.path.join(dri_root, name, "i915_gem_objects")
            if not os.path.exists(p):
                continue
            with open(p) as f:
                txt = f.read(8192)
            # 首行形如：358 shrinkable [0 free] objects, 536887296 bytes
            m = re.search(r"^\s*\d+\s+\S+[^,]*,\s*(\d+)\s+bytes", txt, re.M)
            if m:
                used = int(m.group(1))
                break
        if not used:
            return None, None, None, None
        total_kb = None
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    break
        if not total_kb:
            return used, None, None, "gem"
        total = total_kb * 1024
        return used, total, round(used / total * 100, 1), "gem"
    except Exception:
        return None, None, None, None


def _sample_gpu_live():
    idents = _gpu_ident_list()
    res = []
    for g in idents:
        temp = None; util = None; util_avail = False; util_proxy = False
        render = None; video = None; freq_mhz = None; power_w = None; rc6 = None
        mem_used = None; mem_total = None; mem_pct = None
        # 显存口径：vram=独显驱动真实显存 / gem=核显 GEM 图形缓冲（共享内存）/ sysmem=回退按系统内存
        mem_basis = None
        try:
            if g["vendor"] == "10de":  # NVIDIA
                s = shell.run_cmd(["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,temperature.gpu,memory.used,memory.total",
                             "--format=csv,noheader,nounits"], 3)
                parts = [x.strip() for x in s.split(",")]
                if len(parts) >= 2:
                    try:
                        util = float(parts[0]); util_avail = True
                    except Exception:
                        pass
                    try:
                        mem_pct = float(parts[1])
                    except Exception:
                        pass
                    try:
                        temp = float(parts[2])
                    except Exception:
                        pass
                    try:
                        mem_used = int(parts[3]) * 1024 * 1024  # MiB -> bytes
                        mem_total = int(parts[4]) * 1024 * 1024
                        mem_basis = "vram"
                    except Exception:
                        pass
            elif g["vendor"] == "1002":  # AMD
                b = _amd_gpu_busy(g["pci"])
                if b is not None:
                    util = b; util_avail = True
                t = _gpu_temp_from_sysfs_live(g["pci"])
                if t is not None:
                    temp = t
                # AMDGPU VRAM: /sys/class/drm/cardN/device/mem_info_vram_{used,total}
                try:
                    base = (g["pci"] or "").strip()
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
                        if base and not (base == dev or dev.endswith(base) or base.endswith(dev)):
                            continue
                        used_path = os.path.join(devdir, "mem_info_vram_used")
                        total_path = os.path.join(devdir, "mem_info_vram_total")
                        if os.path.exists(used_path) and os.path.exists(total_path):
                            mem_used = int(open(used_path).read().strip())
                            mem_total = int(open(total_path).read().strip())
                            if mem_total > 0:
                                mem_pct = round(mem_used / mem_total * 100, 1)
                                mem_basis = "vram"
                            break
                except Exception:
                    pass
            else:  # Intel 核显 / 无核显
                temp = _temp_snapshot_read().get("cpu_temp")
                sample = _intel_igpu_top_sample()
                if sample.get("util") is not None:
                    util = sample["util"]; util_avail = True; util_proxy = False
                if sample.get("render") is not None:
                    render = sample["render"]
                if sample.get("video") is not None:
                    video = sample["video"]
                if sample.get("freq_mhz") is not None:
                    freq_mhz = sample["freq_mhz"]
                if sample.get("power_w") is not None:
                    power_w = sample["power_w"]
                if sample.get("rc6") is not None:
                    rc6 = sample["rc6"]
                if util is None:
                    u, u_proxy = _intel_igpu_util()
                    if u is not None:
                        util = u; util_avail = True; util_proxy = u_proxy
                # 核显共享系统内存：优先取 i915 GEM 图形缓冲的真实占用（与系统内存占用不是一回事，
                # 不会和「内存占用」重复）；i915 debugfs 不可读时再回退到系统内存口径并标注 basis。
                mu, mt, mp, mb = _intel_igpu_vram_bytes()
                if mu is None:
                    mu, mt, mp = _gpu_memory_bytes()
                    mb = "sysmem" if mt else None
                if mu is not None:
                    mem_used, mem_total, mem_pct, mem_basis = mu, mt, mp, mb
        except Exception as _e:
            pass
        res.append({"vendor": g["vendor"], "type": g["type"], "name": g["name"], "pci": g["pci"],
                    "name_full": g.get("name_full", g["name"]), "name_arch": g.get("name_arch", ""),
                    "temp": (round(temp, 1) if isinstance(temp, (int, float)) else None),
                    "util": (round(util, 1) if isinstance(util, (int, float)) else None),
                    "render": (round(render, 1) if isinstance(render, (int, float)) else None),
                    "video": (round(video, 1) if isinstance(video, (int, float)) else None),
                    "freq_mhz": (round(freq_mhz, 1) if isinstance(freq_mhz, (int, float)) else None),
                    "power_w": (round(power_w, 2) if isinstance(power_w, (int, float)) else None),
                    "rc6": (round(rc6, 1) if isinstance(rc6, (int, float)) else None),
                    "mem_used": mem_used, "mem_total": mem_total, "mem_pct": mem_pct,
                    "mem_basis": mem_basis,
                    "util_avail": util_avail, "util_proxy": util_proxy})
    return res

def _get_gpu_live():
    now = time.time()
    if now - _GPU_LIVE_CACHE["t"] < 2:
        return _GPU_LIVE_CACHE["data"]
    try:
        data = _sample_gpu_live()
    except Exception:
        data = _GPU_LIVE_CACHE["data"]
    _GPU_LIVE_CACHE["t"] = now
    _GPU_LIVE_CACHE["data"] = data
    return data
