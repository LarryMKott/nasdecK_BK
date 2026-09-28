"""端口释放安全规则单测（纯判定）。"""
from backend.domain.rules.port_rule import PROTECTED_PROCESSES, is_protected


def test_protected_list_matches_legacy_copy():
    # 与 ports.py::_PORT_PROTECTED（v2.3.3）逐项一致——名单即安全边界，防复制漂移
    assert PROTECTED_PROCESSES == (
        "systemd", "init", "dockerd", "containerd", "containerd-shim",
        "sshd", "dbus-daemon", "udevd", "trimenv", "trim-cli",
        "nginx", "cron", "rsyslogd", "login", "agetty")


def test_is_protected():
    assert is_protected("systemd") and is_protected("nginx") and is_protected("trim-cli")
    assert not is_protected("my-app")
    assert not is_protected(None)
