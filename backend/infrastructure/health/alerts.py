# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.domain.rules.alert_rule import evaluate_alerts as _domain_alerts
from backend.infrastructure.collectors.cputemp import _mb_temp_from_sensors
from backend.infrastructure.collectors.raid import get_raid_card
from backend.infrastructure.jsonstore import _load_json_file
from backend.infrastructure.jsonstore import _save_json_file
from backend.infrastructure.config.paths import _config_dir
import json
import os
import time
import urllib.request, urllib.error




# ===================== 控制与自动化：告警 + 健康报告 =====================
ALERTS_FILE = os.path.join(_config_dir(), "alerts.json")
_NOTIFY_LOG = os.path.join(_config_dir(), "notifications.log")

def _load_alerts():
    d = _load_json_file(ALERTS_FILE, {})
    d.setdefault("enabled", True)
    d.setdefault("temp", {"enabled": True, "cpu_max": 85, "mb_max": 75, "disk_max": 60})
    d.setdefault("disk_health", True)
    d.setdefault("memory_max", 90)
    d.setdefault("channels", {
        "system": True,
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "bark": {"enabled": False, "url": ""},
        "email": {"enabled": False, "smtp_host": "", "smtp_port": 465, "user": "", "pass": "", "to": ""},
    })
    d.setdefault("level_channels", {
        "danger": ["system", "telegram", "bark", "email"],
        "warn": ["system", "telegram"],
        "info": ["system"],
    })
    return d

def _save_alerts(cfg):
    return _save_json_file(ALERTS_FILE, cfg)

def _evaluate_alerts(system, disks, docker=None):
    """扫描当前状态，返回活动告警列表（与旧实现逐字节一致的 dict 列表）。
    v2.4 换线：评估核在 domain.rules.alert_rule（纯内存）；本函数承担 IO 取数——
    配置加载（_load_alerts）/阵列卡采集（get_raid_card，失败降级 {}）/主板温度解析，随后传参。"""
    if not system or not isinstance(system, dict):
        return []
    cfg = _load_alerts()
    if not cfg.get("enabled"):
        return []
    try:
        raid = get_raid_card()
    except Exception:
        raid = {}
    mt = _mb_temp_from_sensors(system.get("sensors"))
    return [a.to_dict() for a in _domain_alerts(system, disks, cfg, mt, raid, docker)]


def _notify_log(msg):
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_NOTIFY_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (ts, msg))
    except Exception:
        pass

def _send_telegram(token, chat_id, text):
    try:
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        req = urllib.request.Request(url, data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        return str(e)

def _send_bark(url, text):
    try:
        if not url.endswith("/"):
            url += "/"
        req = urllib.request.Request(url + "push", data=json.dumps({"title": "nasdash 告警", "body": text}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except Exception as e:
        return str(e)

def _send_email(cfg, text):
    try:
        import smtplib
        from email.mime.text import MimeText
        msg = MimeText("nasdash 告警通知\n\n%s\n\n时间：%s" % (text, time.strftime("%Y-%m-%d %H:%M:%S")), "plain", "utf-8")
        msg["Subject"] = "nasdash 告警"
        msg["From"] = cfg.get("user", "")
        msg["To"] = cfg.get("to", "")
        with smtplib.SMTP_SSL(cfg.get("smtp_host"), int(cfg.get("smtp_port", 465)), timeout=10) as s:
            s.login(cfg.get("user"), cfg.get("pass"))
            s.sendmail(cfg.get("user"), [cfg.get("to")], msg.as_string())
        return True
    except Exception as e:
        return str(e)

def _dispatch_notifications(text, cfg, level="danger"):
    """按「严重级别」把告警文本推送到该级别启用的渠道，返回 {channel: ok|error}。
    level_channels 决定每个级别走哪些渠道（danger/warn/info）；system 本地日志始终记录。"""
    res = {}
    ch = cfg.get("channels", {}) or {}
    lc = cfg.get("level_channels", {}) or {}
    allowed = lc.get(level, ["system"])
    if "telegram" in allowed and ch.get("telegram", {}).get("enabled"):
        t = ch["telegram"]
        res["telegram"] = _send_telegram(t.get("bot_token", ""), t.get("chat_id", ""), text)
    if "bark" in allowed and ch.get("bark", {}).get("enabled"):
        res["bark"] = _send_bark(ch["bark"].get("url", ""), text)
    if "email" in allowed and ch.get("email", {}).get("enabled"):
        res["email"] = _send_email(ch["email"], text)
    # system 本地日志始终记录（最低保障，不受级别过滤）
    _notify_log("通知[" + level + "]：" + text)
    res["system"] = True
    return res
