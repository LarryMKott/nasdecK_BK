# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）

# 【归属调整】_FAN_ENABLE_CACHE 原在 app.py 温控曲线区，但其唯一写方是本模块的
# _fan_set_enable（rules 只读），为消除 hwmon<->rules 循环 import 移入本模块。
_FAN_ENABLE_CACHE = {}   # (hwmon, idx) -> 上次写入的 pwm_enable 值，避免每个 tick 重复写 sysfs
from backend.fans.enumerate import _enumerate_fans
import errno
import os


def _fan_read_raw(hwmon, idx):
    try:
        with open(f"{hwmon}/pwm{idx}") as f:
            return int(f.read().strip())
    except Exception:
        return None

def _fan_write_raw(hwmon, idx, raw):
    raw = max(0, min(255, int(raw)))
    try:
        with open(f"{hwmon}/pwm{idx}_enable", "w") as f:
            f.write("1")
        with open(f"{hwmon}/pwm{idx}", "w") as f:
            f.write(str(raw))
        return True
    except Exception:
        return False

# 接管总开关关闭时落到的「安全待机占空比」：保持 enable=1 手动模式，不写 2。
# 原因：NCT6797 上 pwm_enable=2（交还芯片 Thermal Cruise）会把风扇拉到满速狂转；
# 这块 DIY 主板又无 fnOS 原生 FCS 可接管，故只能由 nasdash 以手动模式落一个安静待机值并停手，
# 让用户明确看到「接管已释放、风扇已放慢」，而不是冻结在高位看着像还在控。
_FAN_RELEASE_IDLE_PWM = 30

def _fan_release_to_idle():
    """接管总开关关闭时：把每个「真实在转」的风扇降到安全待机占空比并停手。
    跳过无转速反馈的口（如水泵 / 真空口 / 未接风扇，fanN_input 读 0），避免误把水泵降到低速影响散热。
    不写 pwm_enable=2（NCT6797 上 Thermal Cruise 会狂转），详见上方常量注释。"""
    for (hwmon, idx) in _enumerate_fans():
        try:
            try:
                with open(f"{hwmon}/fan{idx}_input") as _f:
                    if int((_f.read().strip() or 0)) <= 0:
                        continue
            except Exception:
                pass
            _fan_write_raw(hwmon, idx, _FAN_RELEASE_IDLE_PWM)
        except Exception:
            pass
def _fan_set_enable(hwmon, idx, val):
    """设置 pwm_enable：1=软件接管控速（nasdash 写 pwm 生效）；2=交还主板/内核自动。带缓存，值不变则不写。"""
    key = (hwmon, idx)
    if _FAN_ENABLE_CACHE.get(key) == val:
        return True
    try:
        with open(f"{hwmon}/pwm{idx}_enable", "w") as f:
            f.write(str(val))
        _FAN_ENABLE_CACHE[key] = val
        return True
    except Exception:
        return False

def _fan_release_auto(hwmon, idx):
    """把风扇交还主板/内核自动控速（pwm_enable=2）。FCS 若存在会重新接管。"""
    return _fan_set_enable(hwmon, idx, 2)

# ---- 风扇接口调速信号类型：PWM(4pin 脉宽) / DC(3pin 直流电压) ----
# sysfs 语义（hwmon 标准）：pwmN_mode  0=DC(电压调速)  1=PWM(脉宽调速)
# 实测(NCT6797/微星 B360M MORTAR)：文件对所有通道都存在且 0644 可写，但只有部分通道
# 真正支持切到 DC——不支持的通道写 0 会被内核直接拒绝(OSError EIO/EINVAL)，值保持 1。
# 因此「是否支持 DC」无法在不改动硬件状态的前提下探测（驱动对写 1 一律接受、写 0 才校验），
# 只在用户显式切换时尝试写入，失败则返回明确原因，不做启动期试探。
def _fan_read_pwm_mode(hwmon, idx):
    """读取该风扇接口的调速信号类型。返回 'pwm' / 'dc' / None(该通道无此文件)。"""
    try:
        with open(f"{hwmon}/pwm{idx}_mode") as f:
            v = f.read().strip()
        return "pwm" if v == "1" else "dc" if v == "0" else None
    except Exception:
        return None

def _fan_set_pwm_mode(hwmon, idx, mode):
    """切换调速信号类型。mode: 'pwm' | 'dc'。返回 (ok, err_msg)。
    注意：DC 模式下占空比是按电压比例输出，低占空比更容易导致风扇停转或无法启动。"""
    val = "1" if mode == "pwm" else "0"
    path = f"{hwmon}/pwm{idx}_mode"
    if not os.path.exists(path):
        return False, "该接口不支持切换调速方式（主板未提供此寄存器）"
    try:
        with open(path, "w") as f:
            f.write(val)
    except OSError as e:
        # EIO/EINVAL = 主板该接口硬件上只支持一种信号类型，无法切换
        if e.errno in (errno.EIO, errno.EINVAL):
            return False, "该接口硬件只支持 %s 模式，无法切换（主板限制，非软件问题）" % (
                "PWM" if mode == "dc" else "DC")
        return False, "写入失败：%s" % e
    except Exception as e:
        return False, "写入失败：%s" % e
    # 回读确认（部分驱动写入不报错但也不生效）
    if _fan_read_pwm_mode(hwmon, idx) != mode:
        return False, "该接口硬件不支持 %s 模式（写入未生效）" % mode.upper()
    return True, None
