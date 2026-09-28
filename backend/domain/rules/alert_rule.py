"""告警阈值评估（纯内存计算）。

忠实副本来源：backend/health/alerts.py::_evaluate_alerts（v2.3.3）。
与存量实现的差异（仅参数化，见 rules/__init__.py）：
  - 告警配置不再函数内读文件（_load_alerts），由调用方传入 cfg；
  - 主板温度不再函数内解析（_mb_temp_from_sensors），由调用方传入 mb_temp（纯解析，原样前移）；
  - 阵列卡数据不再函数内采集（get_raid_card 的 try/except 由调用方承担），由调用方传入 raid。
"""
from backend.domain.entities.health import AlertItem


def evaluate_alerts(system, disks, cfg, mb_temp=None, raid=None, docker=None) -> list:
    """扫描当前状态，返回活动告警列表（AlertItem，to_dict 与旧 API 逐字节一致）。
    system/disks 为既有采集载荷；cfg 为告警配置（alert.json 内容）；raid 为阵列卡数据（无则 None）。
    """
    alerts = []
    if not system or not isinstance(system, dict):
        return alerts
    if not cfg.get("enabled"):
        return alerts
    tcfg = cfg.get("temp", {}) or {}
    if tcfg.get("enabled") and tcfg.get("cpu_max"):
        ct = system.get("cpu_temp")
        try:
            if ct is not None and float(ct) >= float(tcfg["cpu_max"]):
                alerts.append(AlertItem(
                    "danger", "CPU 温度过高", "CPU 封装温度 %s°C，超过阈值 %s°C" % (ct, tcfg["cpu_max"])))
        except (TypeError, ValueError):
            pass
    if tcfg.get("enabled") and tcfg.get("mb_max"):
        try:
            if mb_temp is not None and mb_temp >= float(tcfg["mb_max"]):
                alerts.append(AlertItem(
                    "danger", "主板/芯片组温度过高", "温度 %s°C，超过阈值 %s°C" % (mb_temp, tcfg["mb_max"])))
        except (TypeError, ValueError):
            pass
    if tcfg.get("enabled") and tcfg.get("disk_max"):
        for d in (disks or []):
            if not isinstance(d, dict):
                continue
            dt = d.get("temp")
            try:
                if dt is not None and float(dt) >= float(tcfg["disk_max"]):
                    alerts.append(AlertItem(
                        "warn", "硬盘温度过高", "%s 温度 %s°C，超过阈值 %s°C" % (d.get("dev", "?"), dt, tcfg["disk_max"])))
            except (TypeError, ValueError):
                pass
    if cfg.get("disk_health"):
        for d in (disks or []):
            if not isinstance(d, dict):
                continue
            h = d.get("health")
            if d.get("health_ok") is False and h not in (None, "", "N/A", "UNKNOWN"):
                alerts.append(AlertItem(
                    "danger", "硬盘健康异常", "%s SMART 健康状态：%s" % (d.get("dev", "?"), h)))
    mm = cfg.get("memory_max")
    mem = system.get("memory") or {}
    try:
        if mm and mem.get("percent") is not None and float(mem["percent"]) >= float(mm):
            alerts.append(AlertItem(
                "warn", "内存占用过高", "内存使用率 %s%%，超过阈值 %s%%" % (mem["percent"], mm)))
    except (TypeError, ValueError):
        pass
    # 阵列卡告警（无硬件 VD 时 virtual_drives/drives 为空，自然不产生告警；本机 JBOD 直通降级安全）
    if raid and raid.get("mode") in ("mega", "hba"):
        # CacheVault 告警只看解析后的 normalized 状态；"未检测到"/无 CacheVault 时不应告警
        cv_status = (raid.get("cachevault_status") or "").lower()
        if not cv_status:
            cv_status = (raid.get("cachevault") or "").lower()
        if cv_status and cv_status not in ("optimal", "ok", "", "-", "none", "present", "未检测到") and "optimal" not in cv_status:
            alerts.append(AlertItem(
                "warn", "CacheVault 状态异常", "阵列卡掉电保护缓存状态：%s（断电时缓存数据可能丢失）" % raid.get("cachevault")))
        for v in (raid.get("virtual_drives") or []):
            st = (v.get("state") or "").lower()
            if st and st not in ("optl", "optimal", "ok", ""):
                alerts.append(AlertItem(
                    "danger", "逻辑盘状态异常", "%s 状态：%s" % (v.get("name") or v.get("dgvd"), v.get("state"))))
        # CopyBack 自动换盘监控：故障盘时提示换盘进度 / 待处理
        for d in (raid.get("drives") or []):
            if d.get("copyback_active"):
                alerts.append(AlertItem(
                    "danger", "正在执行 CopyBack 换盘", "盘 %s 已故障并正在自动复制到热备盘（CopyBack 换盘中）" % d.get("slot")))
            elif d.get("failed"):
                hs = [h.get("slot") for h in (raid.get("hotspares") or [])]
                if hs:
                    tip = "开启自动 CopyBack 后将自动换盘" if raid.get("auto_copyback") == "enabled" else "需手动触发 CopyBack 换盘"
                    alerts.append(AlertItem(
                        "warn", "硬盘故障待换盘", "盘 %s 已故障，已配置热备盘 %s，%s" % (d.get("slot"), "、".join(hs), tip)))
    return alerts
