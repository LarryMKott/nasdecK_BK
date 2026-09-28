# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.fans.enumerate import _enumerate_fans
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
from backend.infrastructure.command.cmd_runner import read_file
import os


# ===================== 风扇标注（用户可编辑名称/电压，按安装实例持久化）=====================
# 标注与硬件无关：只存 (hwmon, idx) -> {name, voltage}，不写死任何机型，对所有用户（含 IT87）安全。
FAN_LABELS_FILE = os.path.join(_config_dir(), "fan_labels.json")
_FAN_VOLT_ALLOWED = ("12V", "5V", "未知", "")

def _load_fan_labels():
    d = _load_json_file(FAN_LABELS_FILE, {})
    # 自修复：hwmon 路径随重启漂移会产生同 idx 重复 key(如 hwmon4::5 与 hwmon5::5
    # 指向同一物理扇)。按 idx 归并,名字取最长(最具体)标注,hidden/no_tach/voltage 取并集,
    # 其余重复 key 删除,避免改名/隐藏在下次开机后像『没保存』。仅在有重复时写回。
    if _consolidate_fan_labels(d):
        _save_fan_labels(d)
    return d

def _save_fan_labels(d):
    return _save_json_file(FAN_LABELS_FILE, d)

def _consolidate_fan_labels(d):
    """按 idx 归并重复 key(应对 hwmon 路径漂移)。返回是否发生过修改。

    同一物理风扇通道由 idx 唯一标识,hwmon 前缀(N::4/5)仅是枚举路径、会随重启变。
    归并规则：名字取最长的非空中标注(最具体),voltage/hidden/no_tach 取并集(任一为 True 即保留),
    其余重复 key 删除,只留 canonical(首个 key)。
    """
    groups = {}
    for k in list(d.keys()):
        if "::" in k:
            idx = k.split("::", 1)[1]
            groups.setdefault(idx, []).append(k)
    changed = False
    for idx, keys in groups.items():
        if len(keys) <= 1:
            continue
        changed = True
        canonical = keys[0]
        merged_name = ""
        merged = {}
        for k in keys:
            v = d.get(k)
            if not isinstance(v, dict):
                continue
            nm = v.get("name") or ""
            if nm and len(nm) > len(merged_name):
                merged_name = nm
            if v.get("voltage"):
                merged["voltage"] = v["voltage"]
            if v.get("hidden"):
                merged["hidden"] = True
            if v.get("no_tach"):
                merged["no_tach"] = True
        if merged_name:
            merged["name"] = merged_name
        for k in keys:
            if k != canonical:
                d.pop(k, None)
        d[canonical] = merged
    return changed

def _fan_label_for(hwmon, idx):
    labels = _load_fan_labels()
    lbl = labels.get(f"{hwmon}::{idx}")
    if not lbl:
        # hwmon 路径可能跨重启变化（如 hwmon4→hwmon3），按通道序号兜底命中，
        # 避免重启后标签/隐藏全部错位（用户按界面序号标的名字仍对得上）。
        for k, v in labels.items():
            if k.endswith(f"::{idx}"):
                lbl = v
                break
    return lbl or {}

def _canon_label_key(labels, idx):
    """返回同一物理风扇通道(idx 稳定, hwmon 路径随重启漂移)的已有标注 key。

    用于保存时把改名/隐藏归并到那条既有 key,避免每次重启 hwmon 重排都新建孤立 key,
    导致改名/隐藏在下次开机后像『没保存』(精确 hwmon::{idx} 查不到→回退默认 风扇N)。
    """
    suffix = "::%d" % idx
    for k in labels:
        if k.endswith(suffix):
            return k
    return None


def _fan_alias_map():
    """别名组：以『用户自定义同名标注』连接多个不同 hwmon::idx 通道（同一物理风扇被识别成两张卡）。
    返回 {key:"hwmon::idx": [member keys...]}。仅当用户为多个通道设了相同非空 name 时才成组，
    避免把恰巧同名的不同风扇误合并（论坛 huhaibo820 反馈 #1：改名/隐藏联动、两张卡合一）。"""
    labels = _load_fan_labels()
    by_name = {}
    for k, v in (labels or {}).items():
        if not isinstance(v, dict):
            continue
        name = (v.get("name") or "").strip()
        if name:
            by_name.setdefault(name, []).append(k)
    amap = {}
    for name, keys in by_name.items():
        if len(keys) > 1:
            for k in keys:
                amap[k] = list(keys)
    return amap


def _fan_alias_members_of(hwmon, idx):
    """返回含别名的全部成员 key 列表 [(hwmon, idx), ...]（含自身）。"""
    amap = _fan_alias_map()
    ks = "%s::%d" % (hwmon, idx)
    members = amap.get(ks, [ks])
    out = []
    for m in members:
        if "::" in m:
            h, i = m.split("::", 1)
            try:
                out.append((h, int(i)))
            except (TypeError, ValueError):
                pass
    return out or [(hwmon, idx)]


def _apply_fan_aliases(fans):
    """把同名标注的多通道风扇合并为一张卡：代表取有转速的通道，改名/隐藏同步到全部成员。"""
    amap = _fan_alias_map()
    if not amap:
        return fans
    idx_of = {}
    for f in fans:
        idx_of["%s::%d" % (f["hwmon"], f["idx"])] = f
    seen = set()
    out = []
    for f in fans:
        k = "%s::%d" % (f["hwmon"], f["idx"])
        if k not in amap:
            out.append(f)
            continue
        grp = tuple(sorted(amap[k]))
        if grp in seen:
            continue  # 成员已并入代表，跳过
        seen.add(grp)
        members = [idx_of[m] for m in grp if m in idx_of]
        rep = next((m for m in members if (m.get("rpm") or 0) > 0), members[0])
        rep = dict(rep)
        rep["alias_members"] = list(grp)
        rpms = [m.get("rpm") or 0 for m in members]
        rep["rpm"] = max(rpms) if any(rpms) else (rep.get("rpm") or 0)
        rep["hidden"] = bool(any(m.get("hidden") for m in members))
        out.append(rep)
    return out


def _replicate_aliases(clean):
    """标注整体保存时，把同名组的条目同步到所有成员（改名/隐藏联动）。"""
    amap = _fan_alias_map()
    if not amap:
        return
    for ks, members in amap.items():
        entry = clean.get(ks)
        if not entry:
            continue
        for m in members:
            if m == ks:
                continue
            clean[m] = dict(entry)


def _dedup_fan_names(fans):
    """展示名去重：同名风扇补 #2/#3 后缀，便于在 UI 区分（如主板把两通道都报成 CHA_FAN1）。
    仅影响前端展示，不影响持久化 key（hwmon::idx）——改名/隐藏逻辑仍按唯一 key 工作。"""
    seen = {}
    for f in fans:
        n = f.get("name")
        if not n:
            continue
        if n in seen:
            seen[n] += 1
            f["name"] = "%s #%d" % (n, seen[n])
        else:
            seen[n] = 1
    return fans


def _read_fan_drivers():
    """只读展示风扇「谁在管风扇」：枚举本机 hwmon 风扇芯片 -> 芯片名 + 内核驱动模块 + 加载状态 + PWM 通道映射。
    纯只读，不引入任何 modprobe/主动控制逻辑（v2.3.0 计划 3.1）。"""
    import os as _os
    chips = {}
    for (hwmon, idx) in _enumerate_fans():
        if hwmon not in chips:
            info = {"hwmon": hwmon, "chip": None, "driver": None,
                    "driver_loaded": None, "pwm_map": []}
            _name = read_file(f"{hwmon}/name").strip()
            info["chip"] = _name or None
            _drv = None
            _dlink = _os.path.join(hwmon, "device", "driver")
            try:
                if _os.path.islink(_dlink):
                    _drv = _os.path.basename(_os.readlink(_dlink))
            except Exception:
                pass
            if not _drv:
                _mod = read_file(f"{hwmon}/device/modalias").strip()
                if _mod:
                    _drv = _mod.split(":", 1)[1] if ":" in _mod else _mod
            info["driver"] = _drv or None
            if _drv:
                _st = read_file(f"/sys/module/{_drv}/initstate").strip()
                info["driver_loaded"] = (_st == "live")
            chips[hwmon] = info
        _pv = read_file(f"{hwmon}/pwm{idx}").strip()
        try:
            _raw = int(_pv)
        except Exception:
            _raw = None
        _pct = round(_raw / 255 * 100) if _raw is not None else None
        chips[hwmon]["pwm_map"].append({"fan": idx, "pwm": idx, "raw": _raw, "pct": _pct})
    return list(chips.values())
