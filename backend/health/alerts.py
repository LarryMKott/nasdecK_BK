# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.collectors.cputemp import _mb_temp_from_sensors
from backend.collectors.raid import get_raid_card
from backend.jsonstore import _load_json_file
from backend.jsonstore import _save_json_file
from backend.paths import _config_dir
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
    """扫描当前状态，返回活动告警列表（每项 {level, title, detail}）。纯内存计算，无命令执行。"""
    alerts = []
    if not system or not isinstance(system, dict):
        return alerts
    cfg = _load_alerts()
    if not cfg.get("enabled"):
        return alerts
    tcfg = cfg.get("temp", {}) or {}
    if tcfg.get("enabled") and tcfg.get("cpu_max"):
        ct = system.get("cpu_temp")
        try:
            if ct is not None and float(ct) >= float(tcfg["cpu_max"]):
                alerts.append({"level": "danger", "title": "CPU 温度过高", "detail": "CPU 封装温度 %s°C，超过阈值 %s°C" % (ct, tcfg["cpu_max"])})
        except (TypeError, ValueError):
            pass
    if tcfg.get("enabled") and tcfg.get("mb_max"):
        mt = _mb_temp_from_sensors(system.get("sensors"))
        try:
            if mt is not None and mt >= float(tcfg["mb_max"]):
                alerts.append({"level": "danger", "title": "主板/芯片组温度过高", "detail": "温度 %s°C，超过阈值 %s°C" % (mt, tcfg["mb_max"])})
        except (TypeError, ValueError):
            pass
    if tcfg.get("enabled") and tcfg.get("disk_max"):
        for d in (disks or []):
            if not isinstance(d, dict):
                continue
            dt = d.get("temp")
            try:
                if dt is not None and float(dt) >= float(tcfg["disk_max"]):
                    alerts.append({"level": "warn", "title": "硬盘温度过高", "detail": "%s 温度 %s°C，超过阈值 %s°C" % (d.get("dev", "?"), dt, tcfg["disk_max"])})
            except (TypeError, ValueError):
                pass
    if cfg.get("disk_health"):
        for d in (disks or []):
            if not isinstance(d, dict):
                continue
            h = d.get("health")
            if d.get("health_ok") is False and h not in (None, "", "N/A", "UNKNOWN"):
                alerts.append({"level": "danger", "title": "硬盘健康异常", "detail": "%s SMART 健康状态：%s" % (d.get("dev", "?"), h)})
    mm = cfg.get("memory_max")
    mem = system.get("memory") or {}
    try:
        if mm and mem.get("percent") is not None and float(mem["percent"]) >= float(mm):
            alerts.append({"level": "warn", "title": "内存占用过高", "detail": "内存使用率 %s%%，超过阈值 %s%%" % (mem["percent"], mm)})
    except (TypeError, ValueError):
        pass
    # 阵列卡告警（无硬件 VD 时 virtual_drives/drives 为空，自然不产生告警；本机 JBOD 直通降级安全）
    try:
        raid = get_raid_card()
    except Exception:
        raid = {}
    if raid and raid.get("mode") in ("mega", "hba"):
        # CacheVault 告警只看解析后的 normalized 状态；"未检测到"/无 CacheVault 时不应告警
        cv_status = (raid.get("cachevault_status") or "").lower()
        if not cv_status:
            cv_status = (raid.get("cachevault") or "").lower()
        if cv_status and cv_status not in ("optimal", "ok", "", "-", "none", "present", "未检测到") and "optimal" not in cv_status:
            alerts.append({"level": "warn", "title": "CacheVault 状态异常", "detail": "阵列卡掉电保护缓存状态：%s（断电时缓存数据可能丢失）" % raid.get("cachevault")})
        for v in (raid.get("virtual_drives") or []):
            st = (v.get("state") or "").lower()
            if st and st not in ("optl", "optimal", "ok", ""):
                alerts.append({"level": "danger", "title": "逻辑盘状态异常", "detail": "%s 状态：%s" % (v.get("name") or v.get("dgvd"), v.get("state"))})
        # CopyBack 自动换盘监控：故障盘时提示换盘进度 / 待处理
        for d in (raid.get("drives") or []):
            if d.get("copyback_active"):
                alerts.append({"level": "danger", "title": "正在执行 CopyBack 换盘", "detail": "盘 %s 已故障并正在自动复制到热备盘（CopyBack 换盘中）" % d.get("slot")})
            elif d.get("failed"):
                hs = [h.get("slot") for h in (raid.get("hotspares") or [])]
                if hs:
                    tip = "开启自动 CopyBack 后将自动换盘" if raid.get("auto_copyback") == "enabled" else "需手动触发 CopyBack 换盘"
                    alerts.append({"level": "warn", "title": "硬盘故障待换盘", "detail": "盘 %s 已故障，已配置热备盘 %s，%s" % (d.get("slot"), "、".join(hs), tip)})
    return alerts

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
