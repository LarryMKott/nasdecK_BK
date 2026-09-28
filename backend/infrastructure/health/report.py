# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.collectors.board import get_board
from backend.infrastructure.collectors.board import get_memory_modules
from backend.infrastructure.collectors.cpuusage import get_cpu_usage
from backend.infrastructure.collectors.disks import get_disks
from backend.infrastructure.collectors.dockermod import get_docker
from backend.infrastructure.collectors.raid import get_raid_card
from backend.infrastructure.collectors.storage import get_storage
from backend.infrastructure.collectors.system import get_system
from backend.infrastructure.logging.errring import _ERR_RING
from backend.infrastructure.fans.control import get_fan_status
from backend.infrastructure.fans.labels import _fan_alias_map
from backend.infrastructure.fans.labels import _load_fan_labels
from backend.infrastructure.fans.modes import _load_fan_modes
from backend.infrastructure.fans.rules import _load_fan_disk_temp
from backend.infrastructure.fans.rules import _load_fan_rules_raw
from backend.infrastructure.fans.rules import _load_fan_sys_temp
from backend.infrastructure.health.alerts import _evaluate_alerts
from backend.infrastructure.health.diskhealth import _enrich_disk_channels
from backend.infrastructure.config.paths import APP_DIR
from backend.infrastructure.command.cmd_runner import read_file
from backend.infrastructure.version import APP_VERSION
from backend.infrastructure.version import _fnos_version
import glob
import json
import os
import re
import time


def _debug_log_tail(n=60):
    """读取应用 debug.log 末尾 n 行（脱敏：卷路径简化为占位，避免泄露用户目录结构）。"""
    try:
        with open(os.path.join(APP_DIR, "debug.log"), "r", errors="ignore") as f:
            lines = f.readlines()
        tail = lines[-n:] if len(lines) > n else lines
        redacted = []
        for ln in tail:
            ln = re.sub(r"/vol\d+/@\S+", "<卷路径>", ln)
            redacted.append(ln.rstrip("\n"))
        return redacted
    except Exception:
        return []

def _read_app_log(max_lines=60):
    """读取应用运行日志尾部（供健康报告排查 bug 用）。找不到返回 None。"""
    candidates = [
        os.environ.get("TRIM_PKGVAR", "") + "/app.log",
        "/vol1/@appdata/com.dashboard.nasdash/app.log",
        "/var/apps/com.dashboard.nasdash/var/app.log",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-max_lines:]
                return "".join(lines)
            except Exception:
                pass
    return None


def _parse_log_entries(log_tail):
    """把运行日志解析成 [(时间戳, 级别, 内容)]，级别按内容推断，便于报告分级展示。

    ERROR：Traceback/Error/Exception/failed/失败/错误；WARN：warn/warning/警告；其余 INFO。
    无时间戳的行原样保留（时间戳为空串）。
    """
    entries = []
    for line in (log_tail or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})[ ,]*(.*)$", s)
        ts = m.group(1) if m else ""
        text = (m.group(2) if m else s)
        low = text.lower()
        # Werkzeug 访问日志（"127.0.0.1 - - [时间] "GET /api/xxx HTTP/1.1" 200"）一律算常规信息。
        # 否则 URL 里含 error 的接口（如 /api/errors/test）会被关键词误判成 ERROR，报告出现假错误。
        if re.match(r'^\S+ - - \[', s):
            lvl = "INFO"
        elif any(k in low for k in ("traceback", "error", "exception", "failed", "失败", "错误")):
            lvl = "ERROR"
        elif any(k in low for k in ("warn", "warning", "警告")):
            lvl = "WARN"
        else:
            lvl = "INFO"
        entries.append((ts, lvl, text))
    return entries


# ===================== 报告脱敏（贴论坛用） =====================
# 报告里带着内网 IP、MAC、硬盘/内存序列号、主机名、云盘账号名——留档自用没问题，
# 贴到论坛/群里就等于把这些公开了。脱敏只做「够用即可」的打码：保留厂商可辨的部分
# （MAC 前 3 段是 OUI、序列号首尾几位），既能让自己认出是哪块盘，又不足以被人冒用报修。
def _mask_serial(s):
    s = str(s or "")
    if not s:
        return s
    if len(s) <= 8:
        return s[:2] + "****"
    return s[:4] + "*" * max(4, len(s) - 6) + s[-2:]


def _mask_ip(s):
    s = str(s or "")
    if not s:
        return s
    if ":" in s:                       # IPv6：只留第一组，其余打码（仍可看出运营商前缀段）
        return s.split(":", 1)[0] + ":****:****:****:****"
    parts = s.split(".")
    if len(parts) == 4:
        return "%s.%s.*.*" % (parts[0], parts[1])
    return "***"


def _mask_mac(s):
    s = str(s or "")
    if not s:
        return s
    parts = s.split(":")
    if len(parts) == 6:
        return ":".join(parts[:3]) + ":**:**:**"
    return "**:**:**:**:**:**"


def _redact_report(rep):
    """返回脱敏后的报告副本（不改动原 rep）。作用于 /api/report 的 html 与 json 两种格式。"""
    rep = json.loads(json.dumps(rep, ensure_ascii=False, default=str))
    # 「自由文本」里也要抹掉的字面量：存储拓扑是一整段原样输出的文本树，云盘账号名、主机名
    # 会直接躺在里面（结构化字段打了码也没用）。先收集原文，字段级打码后再全文兜底替换。
    literals = []
    _h = str(rep.get("host") or "").strip()

    def _blank(v):
        return bool(str(v or "").strip())

    if _h:
        rep["host"] = "已脱敏"
        if len(_h) >= 4:            # 太短的主机名不做全文替换，避免误伤其它文本
            literals.append(_h)
    sys_ = rep.get("system") or {}
    if _blank(sys_.get("hostname")):
        sys_["hostname"] = "已脱敏"
    for n in (sys_.get("nics") or []):
        for k, fn in (("ip", _mask_ip), ("ipv6", _mask_ip), ("mac", _mask_mac)):
            if _blank(n.get(k)):
                n[k] = fn(n.get(k))
    for m in ((sys_.get("memory_modules") or {}).get("modules") or []):
        if _blank(m.get("serial")):
            m["serial"] = _mask_serial(m.get("serial"))
    for d in (rep.get("disks") or []):
        for k in ("serial", "wwn"):
            if _blank(d.get(k)):
                d[k] = _mask_serial(d.get(k))
    raid = rep.get("raid") or {}
    for k in ("serial", "sas_address"):
        if _blank(raid.get(k)):
            raid[k] = _mask_serial(raid.get(k))
    for dv in (raid.get("drives") or []):
        if _blank(dv.get("sn")):
            dv["sn"] = _mask_serial(dv.get("sn"))
    for v in ((rep.get("storage") or {}).get("volumes") or []):
        if v.get("cloud"):
            _cn = str(v.get("cloud_name") or "").strip()
            if _cn:
                literals.append(_cn)
                v["cloud_name"] = "已脱敏"
            mount = str(v.get("mount") or "")
            # 云盘挂载点常带用户目录（/vol02/1000-1-xxxx），只留前面几段
            if mount.count("/") >= 2:
                v["mount"] = "/".join(mount.split("/")[:4]) + "/…"
    rep["redacted"] = True
    if literals:
        blob = json.dumps(rep, ensure_ascii=False, default=str)
        for lit in sorted(set(literals), key=len, reverse=True):
            blob = blob.replace(lit, "已脱敏")
        rep = json.loads(blob)
    return rep


def build_health_report():
    """汇总当前全部硬件状态 + 活动告警，生成健康报告快照。"""
    try:
        board = get_board()
    except Exception:
        board = {"manufacturer": "", "product": "", "version": ""}
    try:
        memory_modules = get_memory_modules()
    except Exception:
        memory_modules = {"modules": [], "total_gb": 0, "slots": 0, "brand_summary": ""}
    raid = get_raid_card()
    # 必须走 _enrich_disk_channels（与「物理硬盘信息」页同一套）：否则双磁臂盘会被当成两块盘
    # 重复列出、且拿不到通道来源 / 健康分级 / 缺陷趋势。这里同时让上面的告警不再对同一块盘重复报。
    try:
        disks = _enrich_disk_channels(get_disks(), raid)
    except Exception:
        disks = get_disks()
    # 应用刚启动时后台预热正在做全量扫盘（本机实测约 8–10s）：get_disks() 拿不到锁会直接
    # 返回空（且不写缓存），报告就成了「0 块硬盘」。这里隔一拍重试若干次（只在为空时，
    # 不拖慢正常路径；导出本身是手动操作，接口超时 60s，多等几秒无妨）。
    for _ in range(8):
        if disks:
            break
        time.sleep(1.2)
        try:
            disks = _enrich_disk_channels(get_disks(), raid)
        except Exception:
            pass
    system = get_system()
    # 应用刚重启时温度快照还没拍第一张，get_system 会把「空传感器表」缓存 30s，
    # 报告里温度段就成了「（无）」。这里只在空的情况下强制重采一次，不每次都重采。
    if not ((system.get("sensors") or {}).get("temps") or []):
        try:
            _fresh = get_system(force=True)
            if (_fresh.get("sensors") or {}).get("temps"):
                system = _fresh
        except Exception:
            pass
    # get_system 本身不带 cpu_usage（那是 /api/all 等接口层补的），报告要单独取一次，
    # 否则报告里的「CPU 使用率」恒为空。⚠ 必须放在上面「可能整体替换 system」之后，
    # 否则刚重启那次会被 _fresh 覆盖掉（踩过）。
    try:
        system["cpu_usage"] = get_cpu_usage()
    except Exception:
        system["cpu_usage"] = None
    system_full = {**system, "board": board, "memory_modules": memory_modules}
    storage = get_storage()
    docker = get_docker()
    try:
        fans = get_fan_status()
    except Exception:
        fans = []
    alerts = _evaluate_alerts(system_full, disks, docker)
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": APP_VERSION,
        "host": system.get("hostname"),
        "uptime": system.get("uptime"),
        "fnos_version": _fnos_version(),
        "raid": raid, "disks": disks, "system": system_full,
        "storage": storage, "docker": docker, "fans": fans, "alerts": alerts,
        "log_tail": _read_app_log(),
        "error_ring": list(_ERR_RING)[-30:],
    }

def build_diagnostics():
    """生成可复制粘贴的设备诊断包（供社区排障，隐私安全、不联网）。
    重点暴露『原生态』：每风扇通道的 hwmon::idx、pwm_enable 原始值、是否命中温控规则，
    以及全部风扇配置，便于远程定位『同风扇重复』『模式标签错显』『设温控后手动失效』等问题。"""
    try:
        fans = get_fan_status()
    except Exception:
        fans = []
    fan_rows = []
    for f in fans:
        fan_rows.append({
            "key": "%s::%d" % (f.get("hwmon"), f.get("idx")),
            "name": f.get("label") or f.get("name"),
            "pwm_enable": f.get("pwm_enable"),     # 原始寄存器：0/1/2
            "pwm_mode": f.get("pwm_mode"),         # 'pwm'(4pin脉宽) / 'dc'(3pin电压) / None
            "pwm_pct": f.get("pwm"),
            "rpm": f.get("rpm"),
            "mode": f.get("mode"),
            "rule_hit": bool(f.get("rule")),
            "rule_source": f.get("rule_source"),
            "computed_pwm": f.get("computed_pwm"),
            "target_pct": f.get("target_pct"),
            "no_tach": f.get("no_tach"),
            "hidden": f.get("hidden"),
        })
    # 疑似同物理风扇（相同非空转速出现在多个通道）→ 提示「一张卡变两张」问题（huhaibo820 #1）
    _rpm_groups = {}
    for f in fan_rows:
        r = f.get("rpm") or 0
        if r > 0:
            _rpm_groups.setdefault(r, []).append(f.get("key"))
    diag_dups = [v for v in _rpm_groups.values() if len(v) > 1]
    chips = []
    try:
        for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            chips.append(read_file(d + "/name").strip())
    except Exception:
        pass
    diag = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nasdash_version": APP_VERSION,
        "fnos_version": _fnos_version(),
        "kernel": getattr(os.uname(), "release", ""),
        "sensor_chips": chips,
        "fans": fan_rows,
        "fan_rules": _load_fan_rules_raw(),
        "fan_mode": _load_fan_modes(),
        "disk_temp": _load_fan_disk_temp(),
        "sys_temp": _load_fan_sys_temp(),
        "fan_labels": _load_fan_labels(),
        "alias_groups": _fan_alias_map(),
        "suspected_duplicate_rpm": diag_dups,
        "debug_log_tail": _debug_log_tail(60),
    }
    return diag

def render_diagnostics_text(diag):
    """把诊断包渲染成可复制的纯文本。"""
    L = []
    L.append("===== nasdash 设备诊断包 =====")
    L.append("生成时间: %s" % diag.get("generated_at"))
    L.append("nasdash 版本: %s" % diag.get("nasdash_version"))
    L.append("fnOS 版本: %s" % diag.get("fnos_version"))
    L.append("内核: %s" % diag.get("kernel"))
    L.append("传感器芯片: %s" % ", ".join(diag.get("sensor_chips") or []) or "无")
    L.append("")
    L.append("--- 风扇通道（key = hwmon::idx；pwm_enable: 0=关闭 1=软件控 2=交还自动；"
             "signal: PWM=4pin脉宽 DC=3pin电压）---")
    for f in diag.get("fans") or []:
        rule = ("命中[%s]" % f.get("rule_source")) if f.get("rule_hit") else "无规则"
        _sig = (f.get("pwm_mode") or "n/a").upper()
        _rpm_s = "无反馈线" if f.get("no_tach") else f.get("rpm")
        L.append("  %s | %s | pwm_enable=%s | signal=%s | pwm=%s%% | rpm=%s | mode=%s | %s | 目标=%s | 隐藏=%s"
                 % (f.get("key"), f.get("name"), f.get("pwm_enable"), _sig,
                    f.get("pwm_pct"), _rpm_s, f.get("mode"), rule,
                    f.get("target_pct"), f.get("hidden")))
    _dc = [f.get("key") for f in (diag.get("fans") or []) if f.get("pwm_mode") == "dc"]
    if _dc:
        L.append("ℹ 以下接口为 DC(电压)调速，占空比按电压比例输出、低档更易停转: %s" % ", ".join(_dc))
    # 无信号线风扇插在 PWM 口 → 收不到调速指令、恒满速。这是「怎么调都不降速」最常见的物理原因。
    _nt_pwm = [f.get("key") for f in (diag.get("fans") or [])
               if f.get("no_tach") and f.get("pwm_mode") == "pwm"]
    if _nt_pwm:
        L.append("⚠ 以下接口标注为『无转速反馈线(2/3针)』但当前是 PWM 调速：风扇收不到 PWM 信号线指令，"
                 "占空比很可能不起作用（恒满速）。改 DC 模式 / 换 4 针风扇可解: %s" % ", ".join(_nt_pwm))
    if diag.get("suspected_duplicate_rpm"):
        L.append("⚠ 疑似同物理风扇（相同转速多通道，疑似「一张卡变两张」）: %s"
                 % json.dumps(diag.get("suspected_duplicate_rpm"), ensure_ascii=False))
    if diag.get("alias_groups"):
        L.append("已合并同名通道(别名组): %s" % json.dumps(diag.get("alias_groups"), ensure_ascii=False))
    L.append("")
    L.append("--- 风扇配置 ---")
    L.append("fan_rules: %s" % json.dumps(diag.get("fan_rules"), ensure_ascii=False))
    L.append("fan_mode: %s" % json.dumps(diag.get("fan_mode"), ensure_ascii=False))
    L.append("disk_temp: %s" % json.dumps(diag.get("disk_temp"), ensure_ascii=False))
    L.append("sys_temp: %s" % json.dumps(diag.get("sys_temp"), ensure_ascii=False))
    L.append("fan_labels: %s" % json.dumps(diag.get("fan_labels"), ensure_ascii=False))
    L.append("")
    L.append("--- debug.log 尾部（脱敏）---")
    for ln in diag.get("debug_log_tail") or []:
        L.append("  " + ln)
    L.append("=========================")
    return "\n".join(L)

def _render_report_html(rep):
    def esc(s):
        return ("" if s is None else str(s)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    def fmt(v, suffix=""):
        if v is None or v == "":
            return "—"
        return esc(v) + suffix
    def fmtna(v, na="不适用", suffix=""):
        # 空值显示「不适用（原因）」，对齐「只显示可解释数据」原则；灰显以与真实值一眼区分。
        # 返回 ("html", …) 元组——props()/data() 对元组不二次转义，故此处文案已 esc。
        if v is None or v == "":
            return ("html", "<span class='na'>不适用（" + esc(na) + "）</span>")
        return esc(v) + suffix

    # AIDA64 风样式（全部内嵌，下载后本地打开亦正常显示、可打印）
    CSS = """
    *{box-sizing:border-box}
    body{font-family:'Segoe UI',Tahoma,Arial,'Microsoft YaHei',sans-serif;color:#1a1a1a;
         max-width:1000px;margin:0 auto;padding:24px 28px;background:#fff;line-height:1.55;font-size:13px}
    .rep-title{font-size:23px;font-weight:700;color:#1f4e79;margin:0 0 2px;letter-spacing:.3px}
    .rep-sub{color:#5a6b7b;font-size:12.5px;margin:0 0 4px}
    .rep-sub b{color:#1a1a1a;font-weight:600}
    .cat{background:#1f4e79;color:#fff;font-weight:700;font-size:14px;
         padding:7px 12px;margin:20px 0 0;border-radius:3px 3px 0 0;letter-spacing:.5px}
    .cat:first-of-type{margin-top:0}
    .sub{background:#e8eff7;border:1px solid #d4dce6;border-bottom:none;border-radius:3px 3px 0 0;
         padding:5px 12px;font-weight:600;font-size:12.5px;color:#1f4e79;margin:6px 0 0}
    .sub .sub-tag{font-weight:400;color:#5a6b7b;margin-left:6px}
    table.props{width:100%;border-collapse:collapse;border:1px solid #d4dce6;border-top:none;margin-bottom:4px}
    table.props td.k{width:33%;background:#eef3f8;font-weight:600;
         padding:6px 12px;border-bottom:1px solid #d4dce6;vertical-align:top;color:#243b53}
    table.props td.v{padding:6px 12px;border-bottom:1px solid #d4dce6;vertical-align:top}
    /* 同类分块左右并排（内存条、网卡 …）：2 项及以上两列，1 项仍整幅 */
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:4px 20px;align-items:start}
    .grid2>div{min-width:0}
    .grid2 .sub{margin-top:6px}
    @media print{.grid2{grid-template-columns:1fr 1fr}}
    table.data{width:100%;border-collapse:collapse;border:1px solid #d4dce6;margin-bottom:4px}
    table.data th{background:#336699;color:#fff;font-weight:600;text-align:left;
         padding:6px 10px;font-size:12px;border:1px solid #336699;white-space:nowrap}
    table.data td{padding:5px 10px;border:1px solid #d4dce6;vertical-align:top}
    table.data tr:nth-child(even) td{background:#f4f8fb}
    .empty{color:#5a6b7b;padding:9px 12px;border:1px solid #d4dce6;border-top:none;font-style:italic}
    .note{background:#eef3f8;border:1px solid #d4dce6;border-top:none;padding:8px 12px;
         color:#243b53;font-size:12px;margin-bottom:4px}
    .alert-box{border:1px solid #d4dce6;border-top:none;padding:10px 14px}
    .alert-box ul{margin:0;padding-left:20px}
    .alert-box li{margin:3px 0}
    .ok{color:#1a7f37} .warn{color:#b45309} .danger{color:#c0392b}
    .na{color:#8a97a5}
    .redact{background:#fdf0ef;color:#a93226;border:1px solid #f0c9c4;border-radius:3px;
         padding:1px 7px;font-size:11.5px;font-weight:600}
    .logstat{padding:9px 12px;border:1px solid #d4dce6;border-top:none;background:#f4f8fb;
         font-size:12.5px;margin-bottom:4px}
    .logstat b{font-size:13px}
    .log-wrap{border:1px solid #d4dce6;border-top:none;margin-bottom:4px;max-height:380px;overflow:auto}
    .log-line{font-family:Consolas,Menlo,monospace;font-size:11.5px;padding:4px 12px;
         border-bottom:1px solid #eef1f5;white-space:pre-wrap;word-break:break-all;line-height:1.5}
    .log-line:last-child{border-bottom:none}
    .log-line .lt{color:#8a97a5;margin-right:8px}
    .log-line.lv-err{background:#fdf0ef;color:#a93226}
    .log-line.lv-warn{background:#fdf6ec;color:#9a6700}
    .log-line.lv-info{color:#3d5167}
    .footer{color:#5a6b7b;font-size:12px;margin-top:26px;border-top:1px solid #d4dce6;padding-top:10px}
    @media print{body{padding:0}
      .cat,table.data th{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
    """
    def cat(title):
        return f"<div class='cat'>{esc(title)}</div>"
    def sub(title, tag=""):
        # 小节标题（比 cat 低一级）：用于「每根内存条 / 每张网卡」这类同段内的分块
        t = f"<span class='sub-tag'>{esc(tag)}</span>" if tag else ""
        return f"<div class='sub'>{esc(title)}{t}</div>"
    def props(rows):
        if not rows:
            return "<div class='empty'>（无）</div>"
        def _pv(v):
            # ("html", "...") 元组 = 已是安全 HTML（如状态着色），其余按普通值走 fmt（空 → —）
            if isinstance(v, tuple) and len(v) == 2 and v[0] == "html":
                return v[1]
            return fmt(v)
        body = "".join(
            f"<tr><td class='k'>{esc(k)}</td><td class='v'>{_pv(v)}</td></tr>"
            for k, v in rows)
        return f"<table class='props'>{body}</table>"
    def _cell(c):
        # ("html", "...") 元组表示该单元格已是安全 HTML（如健康分级着色 span），不再转义
        if isinstance(c, tuple) and len(c) == 2 and c[0] == "html":
            return c[1]
        return esc(c)
    def data(headers, rows):
        if not rows:
            return "<div class='empty'>（无）</div>"
        th = "".join(f"<th>{esc(h)}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{_cell(c)}</td>" for c in r) + "</tr>"
            for r in rows)
        return f"<table class='data'><tr>{th}</tr>{body}</table>"
    def note(s):
        return f"<div class='note'>{esc(s)}</div>"
    def na_html(text):
        """灰色说明（与 fmtna 同一视觉口径），用于 props 值列的「无此项 / 均正常」类文案。"""
        return ("html", "<span class='na'>" + esc(text) + "</span>")
    def grid2(items):
        """同类分块（内存条 / 显卡 / 硬盘…）：2 项及以上左右两列并排，1 项整幅。"""
        if len(items) >= 2:
            return "<div class='grid2'>" + "".join(f"<div>{b}</div>" for b in items) + "</div>"
        return "".join(items)

    alerts = rep.get("alerts", []) or []
    sys_ = rep.get("system", {}) or {}
    mem = sys_.get("memory", {}) or {}
    swap = sys_.get("swap", {}) or {}
    board = sys_.get("board", {}) or {}
    mm = sys_.get("memory_modules", {}) or {}
    sens = sys_.get("sensors", {}) or {}
    nics = sys_.get("nics", []) or []
    gpus = sys_.get("gpus", []) or []
    disks = rep.get("disks", []) or []
    raid = rep.get("raid", {}) or {}
    storage = rep.get("storage", {}) or {}
    docker = rep.get("docker", {}) or {}
    fans = rep.get("fans", []) or []

    # 告警
    if alerts:
        ar = "".join(
            f"<li class='{('danger' if a.get('level')=='danger' else ('warn' if a.get('level')=='warn' else 'ok'))}'>"
            f"[{esc(a.get('level','info'))}] {esc(a.get('title',''))} — {esc(a.get('detail',''))}</li>"
            for a in alerts)
        alert_box = f"<div class='alert-box'><ul>{ar}</ul></div>"
    else:
        alert_box = "<div class='alert-box'><span class='ok'>无活动告警 ✓</span></div>"

    # 运行日志（诊断）：只保留错误 / 警告行。常规行基本都是 Werkzeug 访问日志
    # （每 2 秒一条 "GET /api/metrics 200"），对排查零价值，还会把内部接口路径全暴露给报告读者。
    _log_all = _parse_log_entries(rep.get("log_tail"))
    log_entries = [e for e in _log_all if e[1] in ("ERROR", "WARN")]
    n_err = sum(1 for _, lvl, _ in log_entries if lvl == "ERROR")
    n_warn = sum(1 for _, lvl, _ in log_entries if lvl == "WARN")
    if log_entries:
        stat = (f"运行日志共 {len(_log_all)} 行：<b class='danger'>错误 {n_err}</b> · "
                f"<b class='warn'>警告 {n_warn}</b>（常规访问日志已省略）")
        log_rows_html = "".join(
            f"<div class='log-line lv-{lvl.lower()}'><span class='lt'>{esc(ts)}</span>{esc(text)}</div>"
            for ts, lvl, text in log_entries)
        log_html = f"<div class='logstat'>{stat}</div><div class='log-wrap'>{log_rows_html}</div>"
    elif _log_all:
        log_html = (f"<div class='logstat'>运行日志共 {len(_log_all)} 行，"
                    f"<span class='ok'>无错误 / 警告 ✓</span>（常规访问日志已省略）</div>")
    else:
        log_html = "<div class='empty'>（暂无运行日志）</div>"

    # 系统：原先只有 CPU 型号/核心数/最大频率，操作系统那行还是空的（摘要里
    # 「操作系统 / 运行时长」只填了运行时长）。补上 OS / 内核 / fnOS 版本、CPU 架构与缓存、
    # 实时功耗；显卡移出本段，单独成段（见下），避免和显卡详情重复。
    ci = sys_.get("cpu_info") or {}
    powr = sys_.get("power") or {}
    # RAPL 的 package 是整颗 CPU 封装，core/uncore/dram 是它的下级域——相加会重复计一遍，
    # 所以只报封装值，下级域放括号里，不给「合计」。
    pw_txt = ""
    if powr.get("ok") and powr.get("package") is not None:
        _sub = []
        for _k, _lab in (("core", "核心"), ("uncore", "核显/非核心"), ("dram", "内存控制器")):
            if powr.get(_k) is not None:
                _sub.append("%s %s W" % (_lab, powr.get(_k)))
        pw_txt = "%s W" % powr.get("package")
        if _sub:
            pw_txt += "（其中 " + " · ".join(_sub) + "）"

    def _mhz(v):
        try:
            return "%d MHz" % float(v)
        except (TypeError, ValueError):
            return fmt(v)

    def _cache_s(v):
        """lscpu 的「128 KiB (4 instances)」→「128 KiB ×4」，报告里更短更好读。"""
        s = str(v or "")
        m = re.match(r"^(.*?)\s*\((\d+)\s+instances?\)$", s)
        return (m.group(1) + " ×" + m.group(2)) if m else s

    sys_rows = [
        ["操作系统", fmt(sys_.get("os"))],
        ["内核版本", fmt(sys_.get("kernel"))],
        ["fnOS 系统版本", fmt(rep.get("fnos_version"))],
        ["CPU 型号", fmt(sys_.get("cpu_model"))],
        ["CPU 架构", fmt(ci.get("arch"))],
        ["CPU 核心 / 线程",
         f"{fmt(sys_.get('cpu_cores'))} / {fmt(sys_.get('cpu_threads'))}"
         + (f"（每插槽 {fmt(ci.get('cores_per_socket'))} 核）" if ci.get("cores_per_socket") else "")],
        ["CPU 频率（最大 / 当前）",
         f"{_mhz(sys_.get('cpu_freq') or ci.get('max_freq_mhz'))} / {_mhz(ci.get('current_freq_mhz'))}"],
        ["CPU 缓存（L1d / L1i / L2 / L3）",
         " / ".join(_cache_s(ci.get(_k)) or "—" for _k in ("l1d", "l1i", "l2", "l3"))],
        ["CPU 功耗（RAPL 实时）", pw_txt or fmtna("", "本机未启用 RAPL（消费级/AMD 常见）")],
        ["CPU 使用率", fmt(sys_.get("cpu_usage"), " %")],
        ["负载 (1/5/15)", " / ".join(fmt(x) for x in (sys_.get("load") or []))],
        ["内存", f"{fmt(mem.get('used'))} / {fmt(mem.get('total'))}（{fmt(mem.get('percent'))}%）"],
        ["交换分区", f"{fmt(swap.get('used'))} / {fmt(swap.get('total'))}"],
    ]
    # 显卡：每张卡一块（型号全名 + 架构 + 驱动 + 显存 + 频率 + 温度 + 功耗）
    gpu_items = []
    for g in gpus:
        _gd = [x for x in [
            (g.get("driver") or ""),
            (str(g.get("driver_ver")) if g.get("driver_ver") else ""),
        ] if x]
        _clk = [x for x in [
            (("%s MHz 核心" % g.get("core_clock")) if g.get("core_clock") else ""),
            (("%s MHz 显存" % g.get("mem_clock")) if g.get("mem_clock") else ""),
        ] if x]
        gpu_items.append(sub(fmt(g.get("name_full") or g.get("name")) or "显卡",
                             fmt(g.get("type"))) + props([
            ["类型", fmt(g.get("type"))],
            ["架构代号", fmtna(g.get("name_arch"), "未能识别（PCI ID 库未收录）")],
            ["驱动", " ".join(_gd) or fmtna("", "未加载内核驱动")],
            ["PCI 地址", fmt(g.get("pci"))],
            ["显存", fmt(g.get("vram") or g.get("memory_total"))],
            ["显存类型", fmtna(g.get("mem_type"), "核显/集显无独立显存")],
            ["频率", " · ".join(_clk) or fmtna("", "该显卡未暴露频率")],
            ["温度", fmt(g.get("temp"), " ℃") if g.get("temp") is not None else fmtna("", "该显卡未暴露温度")],
            ["功耗", (f"{fmt(g.get('power_draw'))} / {fmt(g.get('power_cap'))} W"
                     if g.get("power_draw") is not None else fmtna("", "该显卡未暴露功耗"))],
        ]))
    gpu_blocks = grid2(gpu_items)
    board_rows = [
        ["制造商", fmt(board.get("manufacturer"))],
        ["型号", fmt(board.get("product"))],
        ["版本", fmt(board.get("version"))],
        ["BIOS 厂商", fmt(board.get("bios_vendor"))],
        ["BIOS 版本", fmt(board.get("bios_version"))],
        ["BIOS 日期", fmt(board.get("bios_date"))],
        ["芯片组", fmt(board.get("chipset"))],
    ]
    if board.get("note"):
        board_rows.append(["备注", fmt(board.get("note"))])
    mm_rows = []
    for m in (mm.get("modules") or []):
        mm_rows.append([
            fmt(m.get("locator")), "已装" if m.get("installed") else "空槽",
            fmt(m.get("brand")), fmt(m.get("manufacturer")), fmt(m.get("part")),
            fmt(m.get("size")), fmt(m.get("type")), fmt(m.get("speed")),
        ])
    mem_summary = "共 {s} 槽 ｜ 已装 {t} GB ｜ 品牌汇总：{b}".format(
        s=fmt(mm.get("slots")), t=fmt(mm.get("total_gb")), b=fmt(mm.get("brand_summary")))
    # 已装内存条详细参数：每根条一块「字段 → 值」纵表。
    # 原先用 9 列横表，中文说明（如「不适用（该内存条未写入 SPD）」）被挤成多行、短值列（Rank/电压）又空得浪费，
    # 排版很乱；改为纵向 props（与「主板」「阵列卡（硬件）」同款），长说明自然换行、多根条各占一块。
    mem_detail_items = []
    for m in (mm.get("modules") or []):
        if not m.get("installed"):
            continue
        tw, dw = m.get("total_width"), m.get("data_width")
        bw = ("%s / %s" % (tw, dw)) if (tw and dw) else ""
        src = "SPD 直读（decode-dimms + 原始 SPD 字节）" if m.get("source") == "spd" else "dmidecode -t 17"
        _ch = ""
        _ma = re.search(r"Channel([A-Za-z])", str(m.get("locator") or ""))
        if _ma:
            _ch = "通道 " + _ma.group(1).upper()
        mem_detail_items.append(sub(fmt(m.get("locator")) or "内存条", _ch) + props([
            ["容量", fmt(m.get("size"))],
            ["类型", fmt(m.get("type"))],
            ["额定频率", fmt(m.get("speed"))],
            ["实际运行频率", fmtna(m.get("cfg_speed"), "dmidecode 未返回")],
            ["模组厂商", fmt(m.get("manufacturer") or m.get("brand"))],
            ["颗粒厂商", fmtna(m.get("dram_manufacturer"), "SPD 未返回")],
            ["型号（料号）", fmtna(m.get("part"), "该内存条未写入 SPD")],
            ["Rank（颗粒阵列）", fmtna(m.get("rank"), "dmidecode 未返回")],
            ["位宽（总 / 数据）", fmtna(bw, "dmidecode 未返回")],
            ["电压", fmtna(m.get("voltage"), "dmidecode 未返回")],
            ["生产日期", fmtna(m.get("manufacture_date"), "该内存条未写入 SPD")],
            ["序列号", fmtna(m.get("serial"), "该内存条未写入 / 占位")],
            ["数据来源", fmt(src)],
        ]))
    # 版式：单条整幅；2 条及以上左右两列并排（列宽各半，各行顶部对齐）
    mem_detail_blocks = grid2(mem_detail_items)
    # 板级内存信息
    mem_board = []
    if mm.get("ecc_type"):
        mem_board.append("板级 ECC 类型：" + fmt(mm.get("ecc_type")))
    if mm.get("max_capacity"):
        mem_board.append("最大支持容量：" + fmt(mm.get("max_capacity")) + "；插槽总数：" + fmt(mm.get("num_devices")))
    edac = mm.get("edac") or {}
    if edac.get("available"):
        mem_board.append("EDAC 内存错误：ce(可纠正)=%s ue(不可纠正)=%s" % (edac.get("ce"), edac.get("ue")))
    else:
        mem_board.append("EDAC 内存错误：本机未启用（消费级主板常见）")
    # 温度测点：与「温度」页同口径——丢掉无效读数与非直观测点。
    # 未接传感器的 hwmon 条目（PCH_CHIP_TEMP / PCH_CPU_TEMP / PCH_MCH_TEMP / Agent0 Dimm0 等）
    # 会恒读 0 ℃，若照原样列出，会与同段真实存在的「PCH 芯片组 50 ℃」并列，明显误导。
    _EXCLUDE_TEMPS = {"CPU PSS", "CPU VRM", "PECI Agent 0 Calibration",
                      "内存温度 1", "内存温度 2", "复合温度"}
    temp_rows = []
    for t in (sens.get("temps") or []):
        try:
            _tv = float(t.get("value"))
        except (TypeError, ValueError):
            continue
        if not (0 < _tv < 150):          # 无效读数（0 / 负温 / 超量程）不展示
            continue
        if (t.get("name") or "") in _EXCLUDE_TEMPS:
            continue
        temp_rows.append([fmt(t.get("name")), fmt(t.get("value"), " ℃"),
                          fmt(t.get("max"), " ℃"), fmt(t.get("crit"), " ℃")])
    # 风扇：原先是两张表——「传感器—风扇」用 sensors 的名字（CPU_FAN1…），
    # 「风扇控制状态」用 fan/status 的名字（风扇1…），同一批风扇两套叫法、读者对不上号。
    # 现按 hwmon::idx 合并成一张：名称取 sensors 的描述名（认得出插在哪个针脚），
    # 控制信息取 fan/status（模式 / 占空比 / 目标 / 是否手动接管），状态文案与风扇页同一口径。
    _sens_fan = {}
    for _f in (sens.get("fans") or []):
        try:
            _sens_fan[(str(_f.get("hwmon") or ""), int(_f.get("idx") or 0))] = _f
        except (TypeError, ValueError):
            continue

    def _fan_src_label(v):
        v = str(v or "")
        if v.startswith("combo_max:"):
            return "两者取大"
        if v.startswith("combo_avg:"):
            return "两者平均"
        if v.startswith("disk"):
            return "硬盘温度"
        if v == "cpu":
            return "CPU 温度"
        if v == "mb":
            return "主板温度"
        return v

    fan_rows = []
    for f in fans:
        try:
            _key = (str(f.get("hwmon") or ""), int(f.get("idx") or 0))
        except (TypeError, ValueError):
            _key = ("", 0)
        sm = _sens_fan.get(_key) or {}
        _nm = sm.get("label") or sm.get("name") or f.get("label") or f.get("name")
        rpm = f.get("rpm") or 0
        # 状态与风扇页徽章同源：手动固定 / 未设温控 / 线性温控·源 / 曲线温控·源
        if f.get("mode") == "manual" or f.get("manual_active"):
            mode_s = ("html", "<span class='warn'>手动固定</span>")
        elif not f.get("rule"):
            mode_s = ("html", "<span class='na'>未设温控（交还主板自动）</span>")
        else:
            _am = f.get("active_mode") or ("curve" if f.get("mode") == "curve" else "linear")
            if _am == "curve" and not f.get("has_curve"):
                mode_s = ("html", "<span class='na'>曲线温控（待设曲线）</span>")
            else:
                _sl = _fan_src_label(f.get("rule_source"))
                mode_s = ("html", "<span class='ok'>%s</span>" % esc(
                    ("曲线温控" if _am == "curve" else "线性温控") + ("·" + _sl if _sl else "")))
        # 转速：区分「空口」「无测速线」「已停转」，别一律显示 0 RPM
        if rpm:
            rpm_s = fmt(rpm, " RPM")
        elif sm.get("hidden"):
            rpm_s = na_html("空口（未接风扇）")
        elif not (f.get("has_tach") or sm.get("has_tach")):
            rpm_s = na_html("无测速线，读不到转速")
        else:
            rpm_s = na_html("已停转（0 RPM）")
        _tgt = f.get("target_pct")
        if _tgt is not None:
            tgt_s = fmt(_tgt, " %")
        elif not f.get("rule") and f.get("mode") != "manual":
            tgt_s = na_html("交还主板自动")
        else:
            tgt_s = "—"
        fan_rows.append([
            fmt(_nm) or ("风扇 %s" % fmt(f.get("idx"))), rpm_s,
            (fmt(f.get("pwm"), " %") if f.get("pwm") is not None else "—"),
            mode_s, tgt_s,
        ])
    _fan_on = sum(1 for f in fans if (f.get("rpm") or 0) > 0)
    # 电压：只保留能识别用途的（+3.3V / 3VSB / CMOS 电池 等）。芯片原始编号 in0…inN 无厂商标签、
    # 各主板含义不同（本机 in5 读 0.15 V 即明显未接），属于「无法解释的数据」，不放进给用户/论坛看的报告。
    volt_rows = []
    for v in (sens.get("voltages") or []):
        _vn = str(v.get("name") or "")
        if not _vn or re.match(r"^in\d+$", _vn):
            continue
        volt_rows.append([fmt(_vn), fmt(v.get("value"), " V")])
    # 网卡：概览横表 + 每张网卡一块纵向详情。
    # 原先只有「名称/状态/MAC/速率/IP/实时速率」六列，型号、驱动、总线、双工、MTU、IPv6、WOL
    # 这些已采集的字段一个都没进报告。口径与「硬件配置检测」页的网卡卡片保持一致。
    def _rate(v):
        """字节速率 → 可读串，与页面 fmtSpeed 同口径（B/s / KB/s / MB/s）。
        原先直接印裸数字（如「↓0.0 ↑0.0」），既没单位、大流量时又是难读的长数字。"""
        try:
            b = float(v)
        except (TypeError, ValueError):
            return "—"
        if b < 1024:
            return "%.0f B/s" % b
        if b < 1048576:
            return "%.1f KB/s" % (b / 1024.0)
        return "%.2f MB/s" % (b / 1048576.0)

    def _bytes(v):
        """字节总量 → 可读串，与页面同口径（B / KB / MB / GB / TB）。"""
        try:
            b = float(v)
        except (TypeError, ValueError):
            return "—"
        for _u, _d in (("TB", 1024.0 ** 4), ("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2), ("KB", 1024.0)):
            if b >= _d:
                return "%.1f %s" % (b / _d, _u)
        return "%.0f B" % b

    nic_rows = []
    nic_detail_blocks = ""
    for n in nics:
        nic_rows.append([
            fmt(n.get("name")), fmt(n.get("state")), fmt(n.get("mac")), fmt(n.get("speed"), " Mbps"),
            fmt(n.get("ip")),
            (f"↓{_rate(n.get('rx_rate'))} ↑{_rate(n.get('tx_rate'))}" if n.get("rx_rate") is not None else "—"),
        ])
        _cfg = [x for x in [
            (fmt(n.get("speed")) + " Mbps" if n.get("speed") else ""),
            ((n.get("duplex") or "") + "双工" if n.get("duplex") else ""),
            ("MTU " + str(n.get("mtu")) if n.get("mtu") else ""),
        ] if x]
        _hw = [x for x in [
            (n.get("model") or ""),
            ("驱动 " + str(n.get("driver"))) if n.get("driver") else "",
            ("总线 " + str(n.get("bus_info"))) if n.get("bus_info") else "",
        ] if x]
        # WOL 只读状态（与页面同文案）：硬件是否支持 + 当前是否已启用
        _wol_known = (n.get("wol_supported") is not None) or bool(n.get("wol_support"))
        if not _wol_known:
            _wol = "未知（系统缺少 ethtool，或该口不支持查询）"
        elif not n.get("wol_supported"):
            _wol = "不支持（网卡硬件无网络唤醒能力）"
        else:
            _wol = "支持（当前已启用）" if n.get("wol_enabled") else "支持（当前未启用）"
        # OVS 场景下展示名是桥（eno1-ovs），真实物理口另标出来，便于对照交换机/主板丝印
        _phy = str(n.get("phy_name") or "")
        _tag = ("物理口 " + _phy) if (_phy and _phy != str(n.get("name") or "")) else ""
        nic_detail_blocks += sub(fmt(n.get("name")) or "网卡", _tag) + props([
            ["IPv4 地址", fmt(n.get("ip"))],
            ["IPv6 地址", fmtna(n.get("ipv6"), "该口无全局 IPv6 地址")],
            ["MAC 地址", fmt(n.get("mac"))],
            ["网络配置", " · ".join(_cfg) or "—"],
            ["硬件型号", " · ".join(_hw) or "—"],
            ["WOL 网络唤醒", _wol],
        ])
    def _short_size(v):
        """'59,042 GB [59.0 TB]' → '59.0 TB'（SMART 总读写量的原始串太长，取方括号内的整数值）"""
        if not v:
            return ""
        m = re.search(r"\[([^\]]+)\]", str(v))
        return m.group(1) if m else str(v)

    def _grade_cell(d):
        """健康分级单元格：用颜色区分红/黄/绿，与界面徽章同口径。"""
        g = d.get("health_grade") or {}
        lab = g.get("label") or d.get("health") or ""
        if not lab:
            return "—"
        cls = {"red": "danger", "warn": "warn", "ok": "ok"}.get((g.get("level") or "").lower(), "")
        txt = esc(lab)
        return ("html", f"<span class='{cls}'>{txt}</span>") if cls else ("html", txt)

    def _na_reason(d, field=""):
        _ty = str(d.get("type") or "").lower()
        if d.get("asleep"):
            return "休眠中未唤醒"
        if field in ("reallocated", "pending") and _ty == "nvme":
            return "NVMe 无此 SMART 项"
        if field in ("read_errors", "write_errors") and _ty != "sas":
            return "SAS 盘专有项"
        return "该盘未返回此字段"

    def _disk_iface(d):
        """接口 / 介质 / 转速 合并成一句，如「SAS · 机械盘 · 7200 rpm」。"""
        rota = str(d.get("rota"))
        rota_s = "机械盘" if rota == "1" else ("固态" if rota == "0" else "")
        iface = str(d.get("tran") or d.get("type") or "").lower()
        iface = {"nvme": "NVMe", "sas": "SAS", "sata": "SATA", "usb": "USB"}.get(iface, iface.upper())
        rpm = str(d.get("rpm") or "")
        return " · ".join([x for x in [iface, rota_s, (rpm if "rpm" in rpm else "")] if x])

    disk_rows = []
    disk_detail_items = []
    for d in disks:
        na = _na_reason(d)
        # 双执行器（双磁臂）盘：系统认成两块，dev_label 是合并后的显示名（sda/sdb），
        # 概览只给整盘容量，细节（每执行器容量、同一块物理盘）留在下面的详情块里说。
        _dual = bool(d.get("dual_actuator"))
        dev_s = fmt(d.get("dev_label") or d.get("dev"))
        if _dual:
            dev_s += "（双执行器）"
        size_s = fmt(d.get("size_total")) if (_dual and d.get("size_total")) else fmt(d.get("size"))
        # 重映射扇区：SAS 盘没有 ATA 的「重映射扇区」项，等价指标是 grown defect list（defects），
        # 健康分级本身也按它计数，这里兜底展示，行名相应写成「重映射 / 缺陷」。
        _realloc = d.get("reallocated")
        if _realloc is None and d.get("defects") is not None:
            _realloc = d.get("defects")
        _temp_s = (fmt(d.get("temp"), " ℃") if d.get("temp") is not None else fmtna("", na))
        disk_rows.append([
            dev_s, fmt(d.get("brand")), fmtna(d.get("model"), na), size_s,
            fmt(_disk_iface(d)), _temp_s, _grade_cell(d),
        ])
        g = d.get("health_grade") or {}
        _size_detail = size_s
        if _dual and d.get("size_total"):
            _size_detail = "%s（两块执行器各 %s，系统识别为 %s，按序列号合并为一盘）" % (
                fmt(d.get("size_total")), fmt(d.get("size")),
                fmt(d.get("dev_label") or d.get("dev")))
        def _cnt(v, field):
            """SMART 错误计数：有值给数字，没值给原因（灰显）。"""
            if v is not None:
                return esc(v)
            return "<span class='na'>不适用（%s）</span>" % esc(_na_reason(d, field))
        # 两个计数都没有时合成一句原因，避免「不适用（x） / 不适用（x）」重复占半行
        if d.get("read_errors") is None and d.get("write_errors") is None:
            _err_s = na_html("不适用（%s）" % _na_reason(d, "read_errors"))
        else:
            _err_s = ("html", _cnt(d.get("read_errors"), "read_errors") + " / "
                      + _cnt(d.get("write_errors"), "write_errors"))
        _rw_s = " / ".join([x for x in [
            _short_size(d.get("data_units_read")), _short_size(d.get("data_units_written")),
        ] if x]) or fmtna("", "该盘 SMART 未记录读写量")
        disk_detail_items.append(sub(dev_s, fmt(_disk_iface(d))) + props([
            ["型号", fmtna(d.get("model"), na)],
            ["序列号", fmtna(d.get("serial"), na)],
            ["容量", _size_detail],
            ["接口 / 转速", fmt(_disk_iface(d))],
            ["接法", fmtna(d.get("channel"), na)],
            ["温度", _temp_s],
            ["健康分级", _grade_cell(d)],
            ["判定原因", ("html", "<span class='na'>%s</span>" % esc("；".join(g.get("reasons") or [])))
             if g.get("reasons") else na_html("无异常项（各项 SMART / 阵列卡指标均正常）")],
            ["通电时间", fmtna(d.get("power_on_hours"), na, " h")],
            ["读取 / 写入错误", _err_s],
            ["重映射 / 缺陷", fmt(_realloc) if _realloc is not None else fmtna("", _na_reason(d, "reallocated"))],
            ["待映射扇区", fmt(d.get("pending")) if d.get("pending") is not None else fmtna("", _na_reason(d, "pending"))],
            ["累计读取 / 累计写入", _rw_s],
        ]))
    disk_detail_blocks = grid2(disk_detail_items)
    disk_asleep = sum(1 for d in disks if d.get("asleep"))
    # 硬件阵列卡（LSI/Broadcom，storcli 读取）。与下面的系统软阵列严格分开——本机磁盘是 JBOD
    # 直通后由 mdadm 组软阵列，若混在一段里，读者会误以为 raid0/raid1 是阵列卡建的。
    raid_ok = bool(raid.get("model"))
    raid_card_rows = []
    raid_pd_rows = []
    if raid_ok:
        _cv = raid.get("cachevault") or ""
        _cvs = (raid.get("cachevault_status") or "").lower()
        if not _cv:
            _cv_txt = "不适用（本机未检测到掉电保护模块）"
        elif _cvs and "optimal" not in _cvs and _cvs not in ("ok", "present"):
            _cv_txt = ("html", f"<span class='warn'>{esc(_cv)}（⚠ 状态异常）</span>")
        else:
            _cv_txt = _cv
        _vds = raid.get("virtual_drives") or []
        _hss = raid.get("hotspares") or []
        raid_card_rows = [
            ["型号", fmt(raid.get("model"))],
            ["固件版本", fmtna(raid.get("fw_version"), "阵列卡未返回")],
            ["固件包", fmtna(raid.get("fw_package"), "阵列卡未返回")],
            ["BIOS 版本", fmtna(raid.get("bios_version"), "阵列卡未返回")],
            ["驱动", fmtna(raid.get("driver"), "阵列卡未返回")],
            ["掉电保护（CacheVault）", _cv_txt],
            ["芯片温度 (ROC)", fmtna(raid.get("roc_temp"), "阵列卡未返回", " ℃")],
            ["控制器温度", fmtna(raid.get("controller_temp"), "阵列卡未返回", " ℃")],
            ["序列号", fmtna(raid.get("serial"), "阵列卡未返回")],
            ["SAS 地址", fmtna(raid.get("sas_address"), "阵列卡未返回")],
            ["PCI 地址", fmtna(raid.get("pci"), "阵列卡未返回")],
            ["逻辑盘（硬件 RAID）",
             "、".join(str(v.get("name") or v.get("dgvd") or "?") for v in _vds) if _vds
             else "无（本机磁盘为 JBOD 直通，交由系统软阵列管理）"],
            ["热备盘", "、".join(str(h.get("slot") or h.get("sn") or "?") for h in _hss) if _hss else "无"],
        ]
        for dv in (raid.get("drives") or []):
            ph = dv.get("pd_health") or {}
            _bad = bool(dv.get("failed")) or bool(ph.get("smart_alert")) or (ph.get("pred_fail") or 0) > 0
            raid_pd_rows.append([
                fmt(dv.get("slot")), fmt(dv.get("model")), fmt(dv.get("sn")),
                fmt(dv.get("intf")), fmt(dv.get("media")), fmt(dv.get("size")),
                fmt(dv.get("rpm")), fmt(dv.get("state")),
                (fmt(ph.get("temp"), " ℃") if ph.get("temp") is not None else "—"),
                fmt(ph.get("wwn") or ""),
                ("html", "<span class='danger'>⚠ 异常</span>") if _bad else ("html", "<span class='ok'>正常</span>"),
            ])
    raid_rows = [[fmt(a.get("name")), fmt(a.get("level")), fmt(a.get("state")), fmt(a.get("size")),
                  fmt("、".join(a.get("disks") or []))] for a in (storage.get("raid_arrays") or [])]
    def _vol_fs(v):
        # 云挂载：文件系统名（fuse.rclone）对用户无意义，改显示是哪个云盘 + 账号
        if v.get("cloud"):
            name = v.get("cloud_name") or ""
            return (v.get("cloud_type") or "云盘") + (f"（账号：{name}）" if name else "")
        return v.get("fstype")
    vol_rows = [[fmt(v.get("mount")), fmt(_vol_fs(v)), fmt(v.get("size")), fmt(v.get("used")),
                 fmt(v.get("avail")), fmt(v.get("pcent"))] for v in (storage.get("volumes") or [])]
    # Docker：原先「状态」列直接印 docker 的英文原话（"Up 6 days (healthy)"），
    # 「内存」印容器自己的上限（所有人共用宿主上限、没信息量），网络还给裸字节。
    # 现改为：中文运行时长 + 健康标记 / 实际占用 + 百分比 / 流向箭头 + 换算单位。
    c_rows = []
    for c in (docker.get("containers") or []):
        _st = str(c.get("status") or "").lower()
        _run = str(c.get("runtime") or "")
        if "unhealthy" in _st:
            _state = ("html", "<span class='danger'>%s · 异常</span>" % esc(_run or "运行异常"))
        elif "healthy" in _st:
            _state = ("html", "<span class='ok'>%s · 健康</span>" % esc(_run or "运行中"))
        elif c.get("running") is False:
            _state = ("html", "<span class='warn'>%s</span>" % esc(_run or "未运行"))
        else:
            _state = ("html", esc(_run or "运行中"))
        _mem_s = ("%s（%s%%）" % (_bytes(c.get("mem_bytes")), fmt(c.get("mem_pct")))
                  if c.get("mem_bytes") is not None else "—")
        _net_s = ("↓%s ↑%s" % (_bytes(c.get("net_rx")), _bytes(c.get("net_tx")))
                  if c.get("net_rx") is not None else "—")
        c_rows.append([
            fmt(c.get("name")), fmt(c.get("image")), _state, fmt(c.get("ports")),
            (fmt(c.get("cpu"), " %") if c.get("cpu") is not None else "—"),
            _mem_s, _net_s,
        ])
    topology = storage.get("topology", "") or ""

    body = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>nasdash 硬件健康报告</title><style>" + CSS + "</style></head><body>"
        f"<h1 class='rep-title'>nasdash 硬件健康报告</h1>"
        f"<p class='rep-sub'>报告类型：HTML ｜ 生成时间 <b>{esc(rep.get('generated_at'))}</b> ｜ "
        f"版本 <b>{esc(rep.get('version'))}</b> ｜ 主机 <b>{esc(rep.get('host'))}</b> ｜ "
        f"运行时长 <b>{esc(rep.get('uptime'))}</b> ｜ 系统 <b>{esc(sys_.get('os'))}"
        + (f" · 内核 {esc(sys_.get('kernel'))}" if sys_.get("kernel") else "") + "</b>"
        + ("　<span class='redact'>已脱敏</span>" if rep.get("redacted") else "")
        + "</p>"
        # 计算机摘要（AIDA64 风：顶部概览）
        + cat("计算机摘要")
        + props([
            ["计算机名称", sys_.get("hostname")],
            ["操作系统", sys_.get("os")],
            ["运行时长", fmt(sys_.get("uptime"))],
            ["nasdash 版本", rep.get("version")],
            ["CPU 温度", fmt(sys_.get("cpu_temp"), " ℃")],
            ["内存使用率", fmt(mem.get("percent"), " %")],
            ["硬盘数量", "{n} 块{extra}".format(
                n=len(disks), extra=("（含 %d 块休眠）" % disk_asleep) if disk_asleep else "")],
            ["阵列卡", ("已识别：" + fmt(raid.get("model"))) if raid.get("model") else "无"],
            ["Docker 容器", f"{docker.get('running',0)} / {docker.get('total',0)} 运行中"],
            ["风扇", f"{len(fans)} 个接口（{_fan_on} 个在转）"],
            ["活动告警", f"{len(alerts)} 项"],
        ])
        + cat("活动告警")
        + alert_box
        + cat("运行日志（诊断）")
        + log_html
        + cat("系统")
        + props(sys_rows)
        + (cat("显卡") + gpu_blocks if gpu_items else "")
        + cat("主板 / BIOS")
        + props(board_rows)
        + cat("内存")
        + note(mem_summary)
        + data(["插槽", "状态", "品牌", "制造商", "型号", "容量", "类型", "频率"], mm_rows)
        + (note(" ｜ ".join(mem_board)) if mem_board else "")
        + (note("已装内存条详细参数（每根条一块；括号内为字段为空的原因）") + mem_detail_blocks if mem_detail_blocks else "")
        + cat("传感器 — 温度")
        + data(["传感器", "当前", "上限", "临界"], temp_rows)
        + cat("风扇")
        + note("共 {n} 个接口、{m} 个在转。运行状态与「风扇」页徽章同源（手动固定 / 线性温控 / 曲线温控·温度源）。".format(
            n=len(fans), m=_fan_on))
        + data(["风扇", "转速", "当前占空比", "运行状态", "目标占空比"], fan_rows)
        + ((cat("传感器 — 电压") + data(["电压", "值"], volt_rows)) if volt_rows else "")
        + cat("网卡")
        + data(["名称", "状态", "MAC", "速率", "IP 地址", "实时速率"], nic_rows)
        + nic_detail_blocks
        + cat("硬盘 SMART")
        + note("健康分级由 SMART 自检 + 阵列卡侧信息综合判定；累计读写为 SMART 记录的整盘吞吐量。")
        + data(["设备", "品牌", "型号", "容量", "接口 / 转速", "温度", "健康"], disk_rows)
        + ((note("每块硬盘明细（括号内为字段为空的原因）") + disk_detail_blocks)
           if disk_detail_blocks else "")
        + ((cat("阵列卡（硬件）")
            + props(raid_card_rows)
            + (note("阵列卡上的物理盘（storcli 读取）") if raid_pd_rows else "")
            + (data(["槽位", "型号", "序列号", "接口", "介质", "容量", "转速", "状态", "温度", "WWN", "健康"], raid_pd_rows)
               if raid_pd_rows else ""))
           if raid_ok else "")
        + cat("系统软阵列（mdadm）")
        + (note("以上为 Linux 内核软阵列（mdadm）。硬件阵列卡未组逻辑盘时，磁盘以 JBOD 直通给系统，由 mdadm 组软阵列——"
                "即上表阵列与本机阵列卡无关。") if raid_ok else "")
        + data(["阵列", "级别", "状态", "容量", "成员盘"], raid_rows)
        + cat("存储卷")
        + data(["挂载点", "文件系统", "总容量", "已用", "可用", "使用率"], vol_rows)
        + cat("Docker 容器")
        + note(f"运行中 {docker.get('running',0)} / 共 {docker.get('total',0)}；内存为实际占用（括号内为占宿主内存比例），网络为容器累计流量。")
        + data(["容器", "镜像", "运行状态", "端口", "CPU", "内存", "网络 ↓/↑"], c_rows)
        + (cat("存储拓扑")
           + f"<div class='note'><pre style='margin:0;white-space:pre-wrap;font-family:Consolas,Menlo,monospace;font-size:12px'>{esc(topology)}</pre></div>"
           if topology.strip() else "")
        + "<div class='footer'>本报告由 nasdash 自动生成，仅供硬件健康参考。</div>"
        "</body></html>"
    )
    from fastapi.responses import HTMLResponse
    return HTMLResponse(body, status_code=200, headers={"Content-Type": "text/html; charset=utf-8"})
