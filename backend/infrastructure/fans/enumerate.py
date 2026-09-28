# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
import glob as _glob
import os
import re
import threading as _threading
import time

# 本机真实风扇全集缓存（拓扑基本静态，30s 刷新；见 _enumerate_fans）
_FAN_ENUM_CACHE = {"t": 0.0, "v": []}

# 接管总开关刚打开时打一次「立即到位」标志：下一 tick 把接管中的风扇直接写到温度目标，
# 不再一格一格缓变，让用户在面板点一下就能立刻看到转速变化（关闭接管本身是瞬间降到待机，无需 snap）。
_FAN_SNAP = {"v": False, "lock": _threading.Lock()}


def _enumerate_fans(force=False):
    """枚举本机所有「可控制风扇通道」(hwmon_path, idx)。

    自动检测设计（换硬件不失效）：
    - 不依赖芯片型号白名单（it87 / nct / fintek / winbond / asus / AMD 等皆可），
      只要某个 hwmon 暴露 pwm<N>_enable 且存在对应 pwm<N> / fan<N>_input，
      就视为一个可控制风扇通道；
    - 遍历所有 /sys/class/hwmon/hwmon*（多风扇芯片主板不漏）；
    - 通道号 1..10（覆盖主板直连 + 集线器 / 分线器扩展）。
    作为温控循环的控制全集；FAN_TARGETS 仅作「每风扇手动/自动覆盖映射」。
    修复 Bug A：此前温控循环只遍历 FAN_TARGETS（仅用户手动调过的风扇才填充），
    导致 sys_temp/disk_temp 设 controlled_fans:"all" 时启动即空转、一个风扇都不控。
    拓扑基本静态，缓存 30s 刷新一次（支持热插拔风机后自动纳入）。"""
    now = time.time()
    if not force and now - _FAN_ENUM_CACHE["t"] < 30:
        return _FAN_ENUM_CACHE["v"]
    fans = []
    try:
        for _hp in sorted(_glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                _pes = _glob.glob(f"{_hp}/pwm*_enable")
            except Exception:
                continue
            for _pe_path in _pes:
                _m = re.search(r"pwm(\d+)_enable$", _pe_path)
                if not _m:
                    continue
                _fi = int(_m.group(1))
                if _fi > 10:
                    continue
                # 佐证文件存在：pwm<N> 或 fan<N>_input，排除非风扇 pwm（如 RGB 灯效）
                if not (os.path.exists(f"{_hp}/pwm{_fi}") or os.path.exists(f"{_hp}/fan{_fi}_input")):
                    continue
                fans.append((_hp, _fi))
    except Exception:
        pass
    _FAN_ENUM_CACHE["t"] = now
    _FAN_ENUM_CACHE["v"] = fans
    return fans

def _select_temp_fans(all_fans, sys_cfg, disk_cfg):
    """按 controlled_fans 配置从 all_fans 中选出被 sys_temp / disk_temp 接管的风扇集合。
    选择依据「真实风扇全集 all_fans」而非 FAN_TARGETS —— 这是 Bug A 修复的核心。"""
    sys_claimed = set()
    disk_claimed = set()
    if sys_cfg.get("enabled"):
        cf = sys_cfg.get("controlled_fans", "all")
        for (hwmon, idx) in all_fans:
            if cf != "all" and [hwmon, idx] not in cf:
                continue
            sys_claimed.add((hwmon, idx))
    if disk_cfg.get("enabled") and disk_cfg.get("disks"):
        cf = disk_cfg.get("controlled_fans", "all")
        for (hwmon, idx) in all_fans:
            if cf != "all" and [hwmon, idx] not in cf:
                continue
            disk_claimed.add((hwmon, idx))
    # 解耦互斥：sys 与 disk 各自独立选风扇组（不再「sys 先全拿、disk 拿剩余」）。
    # 重叠时 disk 优先（硬盘温控更敏感，是用户明确诉求），从 sys 移除 disk 已选的风扇。
    sys_claimed -= disk_claimed
    return sys_claimed, disk_claimed
