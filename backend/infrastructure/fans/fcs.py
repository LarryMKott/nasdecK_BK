# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
import json

from backend.infrastructure.command import cmd_runner as shell
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
import os
import threading as _threading
import time


def _fan_ext_service_running():
    # 检测系统风扇服务（pwm-fancontrol，fnOS 自带）是否处于 active 状态。
    # fnOS 的风扇服务是 oneshot 服务（跑完写一次 PWM 即退出，非常驻进程），
    # 用 systemctl is-active 判断其 active(exited) 状态比 pgrep 进程更可靠。
    out = shell.run_cmd(["systemctl", "is-active", "pwm-fancontrol"], 2).strip().lower()
    return out in ("active", "activating")

# 接管 / 交还系统风扇服务（FanControlServer）：
# 本应用与 fnOS 自带的 FanControlServer 都直接写 /sys/class/hwmon/.../pwmN，
# 二者同时运行会抢控 → 风扇转速抖动甚至被对方覆盖。论坛亦有用户反馈此冲突。
# 故采用「接管即停、全交还即恢复」策略：nasdash 真正在控速任意风扇时停掉 FCS，
# 全部交还自动后重启 FCS，恢复 fnOS 原生控温。全程 best-effort，失败静默，
# 绝不因停/启服务异常而中断风扇调速主流程。默认（用户未启用任何控速）不触碰 FCS。
_FCS_TAKEN = {"v": False}
# 用户可在面板「永久禁用」FanControlServer（stop+disable，重启不复活）；持久化到 @appdata。
# 为 True 时，nasdash 交还自动控温后不再把 FCS 拉起来，尊重用户选择。
FCS_STATE_FILE = os.path.join(_config_dir(), "fcs_state.json")
# systemctl start/stop pwm-fancontrol 可能慢至数秒，用锁+后台线程避免阻塞 HTTP 请求。
_FCS_OP_LOCK = _threading.Lock()

def _fcs_disabled():
    """用户是否已在面板永久禁用 FanControlServer（读持久化标志）。"""
    return bool(_load_json_file(FCS_STATE_FILE, {}).get("disabled"))

def _set_fcs_disabled(v):
    return _save_json_file(FCS_STATE_FILE, {"disabled": bool(v)})

def _fcs_installed_state():
    """systemctl is-enabled 的原始结果（enabled/disabled/masked/static/...；未安装为空串）。"""
    return shell.run_cmd(["systemctl", "is-enabled", "pwm-fancontrol"], 3).strip().lower()

def _fcs_status():
    """汇总 FanControlServer 状态供面板展示：是否安装/是否开机自启/是否在跑/是否被用户永久禁用。
    用一条 `systemctl show` 同时取 ActiveState 与 UnitFileState，比 is-active + is-enabled
    两次独立命令少一次 D-Bus 往返，降低超时/卡住的概率。"""
    raw = shell.run_cmd(["systemctl", "show", "pwm-fancontrol",
                   "-p", "ActiveState", "-p", "UnitFileState", "--no-pager"], 4).strip()
    active = ""
    unit_state = ""
    for line in raw.splitlines():
        if line.startswith("ActiveState="):
            active = line.split("=", 1)[1].strip().lower()
        elif line.startswith("UnitFileState="):
            unit_state = line.split("=", 1)[1].strip().lower()
    installed = bool(unit_state) or active in ("active", "activating")
    enabled = unit_state == "enabled"
    running = active in ("active", "activating")
    return {
        "installed": installed,
        "enabled": enabled,
        "running": running,
        "disabled_by_user": _fcs_disabled(),
        "raw": unit_state or active,
    }

# 诊断开关：置 True 时跳过 systemctl，直接返回硬编码状态，用于排查网关/请求路径问题。
_FCS_DIAG_HARD = False

def _fcs_status_hard():
    return {"installed": False, "enabled": False, "running": False,
            "disabled_by_user": False, "raw": "diag"}

# FCS 状态查询涉及 systemctl，可能耗时 1~3 秒；面板高频刷新/用户快速切页时会并发请求。
# 改为后台线程每隔 TTL 刷新一次，HTTP 接口只读缓存，永远不再因为 systemctl 慢而卡住请求。
_FCS_STATUS_CACHE = {"t": 0.0, "v": None, "lock": _threading.Lock()}
_FCS_STATUS_TTL = 15.0

def _fcs_status_refresh_loop():
    while True:
        try:
            v = _fcs_status_hard() if _FCS_DIAG_HARD else _fcs_status()
            with _FCS_STATUS_CACHE["lock"]:
                _FCS_STATUS_CACHE["v"] = v
                _FCS_STATUS_CACHE["t"] = time.time()
        except Exception:
            pass
        time.sleep(_FCS_STATUS_TTL)

def _fcs_status_cached(clear=False):
    """返回 FCS 状态缓存；首次调用会启动后台刷新线程，之后 always 立即返回。
    切换开关等操作后调用 clear=True 会先尝试同步刷新一次，让用户立刻看到最新状态。"""
    if _FCS_DIAG_HARD:
        return _fcs_status_hard()
    cache = _FCS_STATUS_CACHE
    with cache["lock"]:
        if not getattr(_fcs_status_cached, "_started", False):
            _threading.Thread(target=_fcs_status_refresh_loop, daemon=True).start()
            _fcs_status_cached._started = True
        if clear:
            cache["t"] = 0.0
            cache["v"] = None
        now = time.time()
        if cache["v"] is not None and now - cache["t"] < _FCS_STATUS_TTL:
            return cache["v"]
    # clear 时（如用户点了禁用/恢复）同步刷一次，保证 UI 即时反馈；_fcs_status 内部有 4s 超时兜底。
    if clear:
        v = _fcs_status()
        with cache["lock"]:
            cache["v"] = v
            cache["t"] = time.time()
        return v
    return {"installed": False, "enabled": False, "running": False,
            "disabled_by_user": _fcs_disabled(), "raw": ""}

def _fcs_has_board_config():
    """判断 FCS 是否真的配置了风扇参数。飞牛部分机型 /boot/board.json 为空或没有 fan 段，
    此时启动 pwm-fancontrol 只是空跑 RemainAfterExit，并不会接管风扇。"""
    try:
        with open("/boot/board.json") as _fh:
            _d = json.load(_fh)
        _fans = _d.get("fan") if isinstance(_d, dict) else None
        return isinstance(_fans, list) and len(_fans) > 0
    except Exception:
        return False

_FCS_BOARD_CFG_CACHE = {"t": 0.0, "v": None}


def _fcs_board_cfg_cached():
    """_fcs_has_board_config 的 60s 缓存版：/api/fan/status 被 1s 轮询，避免重复读 /boot/board.json。"""
    now = time.time()
    c = _FCS_BOARD_CFG_CACHE
    if c["v"] is None or (now - c["t"]) > 60:
        c["v"] = bool(_fcs_has_board_config())
        c["t"] = now
    return c["v"]


def _fcs_running_cached():
    """廉价的 FCS 在跑状态：读 15s TTL 缓存，绝不每 tick 跑 systemctl is-active。
    仅供控速循环自愈合判断使用；首次未填充时返回 False（保守：视为未接管，
    由 fan_smooth_loop 的 _FCS_TAKEN 逻辑兜底停掉），避免每轮控速被一次 systemctl 卡 1~2 秒。"""
    c = _FCS_STATUS_CACHE
    with c["lock"]:
        v = c["v"]
    return bool(v and v.get("running"))


def _fcs_installed_cached():
    """廉价的 FCS 是否安装/开机自启状态：读缓存，不每 tick 跑 systemctl is-enabled。"""
    c = _FCS_STATUS_CACHE
    with c["lock"]:
        v = c["v"]
    return bool(v and (v.get("installed") or v.get("enabled")))


def _fan_stop_ext_service():
    """临时停止系统风扇服务 FanControlServer（接管窗口内，仅 best-effort）。
    后台线程执行，避免 systemctl stop 数秒阻塞 HTTP/主控循环。"""
    def _do_stop():
        with _FCS_OP_LOCK:
            try:
                shell.sudo_cmd(["systemctl", "stop", "pwm-fancontrol"], 5)
            except Exception:
                pass
            try:
                shell.sudo_cmd(["pkill", "-f", "pwm-fancontrol"], 2)
            except Exception:
                pass
    _threading.Thread(target=_do_stop, daemon=True).start()

def _fan_start_ext_service():
    """交还自动时重启系统风扇服务 FanControlServer（仅 best-effort）。
    若用户已在面板「永久禁用」FCS，则不再拉起，尊重用户选择。
    后台线程执行，避免 systemctl start 数秒阻塞 HTTP/主控循环。"""
    if _fcs_disabled():
        return
    def _do_start():
        with _FCS_OP_LOCK:
            try:
                shell.sudo_cmd(["systemctl", "start", "pwm-fancontrol"], 5)
            except Exception:
                pass
    _threading.Thread(target=_do_start, daemon=True).start()

def _fcs_disable():
    """永久禁用 FanControlServer：stop + disable（重启不复活）+ 持久化标志。
    即便 systemctl 命令异常也会写标志，确保 nasdash 交还逻辑不再拉起 FCS。"""
    ok = False
    try:
        shell.sudo_cmd(["systemctl", "disable", "--now", "pwm-fancontrol"], 8)
        ok = True
    except Exception:
        pass
    try:
        shell.sudo_cmd(["pkill", "-f", "pwm-fancontrol"], 2)  # 兜底杀非 systemd 残留进程
    except Exception:
        pass
    _set_fcs_disabled(True)
    _FCS_TAKEN["v"] = False
    return ok

def _fcs_enable():
    """恢复 FanControlServer：清除持久化标志 + enable + start。"""
    _set_fcs_disabled(False)
    ok = False
    try:
        shell.sudo_cmd(["systemctl", "enable", "--now", "pwm-fancontrol"], 8)
        ok = True
    except Exception:
        pass
    return ok
