"""端口释放安全规则（纯数据 + 纯判定）。

忠实副本来源：backend/infrastructure/collectors/ports.py::_PORT_PROTECTED（v2.3.3）。
安全策略：禁止终止系统关键进程（nasdeck 自身 / PID1 / systemd / dockerd / sshd /
飞牛核心服务等）；其余进程由用户在前端二次确认后承担后果。
"""


# 系统关键进程名单（comm 短名）：进程由用户在前端二次确认后承担后果，此名单不可绕过
PROTECTED_PROCESSES = ("systemd", "init", "dockerd", "containerd", "containerd-shim",
                       "sshd", "dbus-daemon", "udevd", "trimenv", "trim-cli",
                       "nginx", "cron", "rsyslogd", "login", "agetty")


def is_protected(comm) -> bool:
    """该进程短名是否属于系统关键进程（禁止释放）。"""
    return comm in PROTECTED_PROCESSES
