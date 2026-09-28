# --split-- 以下内容自 app.py 原样搬迁（import 由 fix_imports.py 补齐）
from backend.infrastructure.config.paths import APP_DIR
import json
import os
import re
import time
import urllib.request, urllib.error


# 【迁移差异】原文件 _parse_ver 定义了两次（L193 与 L267），Python 后者覆盖前者，
# 但 APP_VERSION 在 import 期（L219）由【第一个定义】计算，运行期版本比较用的是
# 【第二个定义】——两个算法都真实生效过。拆名保义：
#   _parse_ver_simple = 原第一定义（导入期算 APP_VERSION 专用）
#   _parse_ver        = 原第二定义（运行期 GitHub 最新版本比较用）
def _parse_ver_simple(v):
    try:
        s = str(v).strip().lstrip("vV")
        return tuple(int(x) for x in s.split("."))
    except Exception:
        return (0,)

def _parse_ver(v):
    """'v1.6.7' / '1.6.7' -> (1,6,7)"""
    v = (v or "").lstrip("vV").strip()
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts[:3]) if parts else (0, 0, 0)

def _app_version():
    appid = os.path.basename(APP_DIR)  # 如 com.dashboard.nasdash
    candidates = [
        os.path.join(APP_DIR, "manifest"),
        os.path.join("/var/apps", appid, "manifest"),
    ]
    best = None
    for path in candidates:
        try:
            with open(path) as f:
                m = re.search(r"^version\s*=\s*(\S+)", f.read(), re.M)
                if m:
                    ver = "v" + m.group(1).strip()
                    t = _parse_ver_simple(ver)
                    if best is None or t > best[0]:
                        best = (t, ver)
        except Exception:
            pass
    return best[1] if best else "v1.6.2"
APP_VERSION = _app_version()

def _fnos_version():
    """读取 fnOS 系统版本。优先从 /usr/trim/etc/version 读取；取不到再回退 os-release。"""
    try:
        if os.path.exists("/usr/trim/etc/version"):
            with open("/usr/trim/etc/version", encoding="utf-8", errors="ignore") as f:
                ver = f.read().strip()
            if ver:
                return "fnOS " + ver
    except Exception:
        pass
    for path in ("/usr/os-release", "/etc/fnos-release", "/etc/os-release"):
        try:
            with open(path) as f:
                txt = f.read()
            m = re.search(r"PRETTY_NAME=\"?([^\"\n]*fnOS[^\"\n]*)", txt, re.I)
            if not m:
                m = re.search(r"VERSION=\"?([^\"\n]*fnOS[^\"\n]*)", txt, re.I)
            if m:
                return m.group(1).strip()
            if path == "/etc/os-release":
                mv = re.search(r"VERSION_ID=\"?([^\"\n]*)", txt)
                if mv:
                    return "Debian " + mv.group(1).strip() + " (fnOS 底层)"
        except Exception:
            pass
    return "未知"

# ---------- 检测新版本（GitHub latest release，带缓存/超时/静默失败，绝不阻塞页面） ----------
_VERSION_CHECK = {"cached_result": None, "checked_at": 0}
_VERSION_CHECK_TTL = 6 * 3600  # 6 小时缓存，避免频繁打 GitHub API
_VERSION_REPO_URL = "https://api.github.com/repos/han951meng/nasdash/releases/latest"

def _check_latest_version():
    """查询 GitHub latest release；带缓存、5s 超时、异常静默返回。"""
    now = time.time()
    if _VERSION_CHECK["cached_result"] is not None and now - _VERSION_CHECK["checked_at"] < _VERSION_CHECK_TTL:
        return _VERSION_CHECK["cached_result"]
    result = {"current": APP_VERSION, "latest": None, "update_available": False, "url": None, "error": None}
    try:
        req = urllib.request.Request(_VERSION_REPO_URL, headers={"User-Agent": "nasdash-version-check"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = (data.get("tag_name") or "").strip()
        latest = ("v" + tag.lstrip("vV")) if tag else ""
        result["latest"] = latest
        result["url"] = data.get("html_url") or "https://github.com/han951meng/nasdash/releases"
        result["update_available"] = _parse_ver(latest) > _parse_ver(APP_VERSION)
    except Exception as e:
        result["error"] = str(e)[:160]
    _VERSION_CHECK["cached_result"] = result
    _VERSION_CHECK["checked_at"] = now
    return result


# ---- 阶段 3：应用信息适配器（实现 domain.interfaces.app_info.IAppInfo）----
from backend.domain.interfaces.app_info import IAppInfo


class AppInfo(IAppInfo):
    def nasdash_version(self) -> str:
        return APP_VERSION

    def fnos_version(self) -> str:
        return _fnos_version()
