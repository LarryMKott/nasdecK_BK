# -*- coding: utf-8 -*-
"""路径与环境解析（自 app.py L134-192 搬迁，APP_DIR 锚点改为包父目录）。

【迁移差异】原代码 APP_DIR = dirname(app.py)；拆包后本文件位于 backend/ 内，
故 REPO_ROOT 取 backend/ 的父目录，与原 APP_DIR 指向同一层（部署时即应用根）。
其余逻辑原样保留。

【配置点】
- TRIM_PKGVAR：飞牛运行时注入的用户数据持久目录（配置/history.db 所在）
- 未注入时按 /vol*/@appdata/com.dashboard.nasdeck 探测，再回退 LEGACY_APPATA
"""
import os
import shutil

# 应用根目录：backend/ 的父目录（部署时即飞牛应用目录，与原单文件 app.py 同层）。
# 锚点链：本文件(config) → infrastructure → backend → 应用根。阶段2a 目录落位时
# 锚点漏随迁（仍按 backend/ 直下两层计），APP_DIR 曾指到 backend/infrastructure：
# / 与 /vue 静态直出 500、version appid 误判、debug.log/配置兜底目录整体偏移。
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "..", ".."))
APP_DIR = REPO_ROOT
# 前端产物目录：templates/vue/index.html（Vue 构建产物，静态直出）。
# 全应用无 Jinja 模板，不覆盖 template_folder。

# 用户配置持久目录：飞牛运行时通过环境变量 TRIM_PKGVAR 提供 @appdata 持久目录
# （与应用卸载无关，重装后保留；cmd/main 也用它存 app.pid/app.log）。
# 早期版本把配置写在 APP_DIR，导致每次重装被清空。现统一写入此持久目录，重装不丢配置。
# LEGACY_APPATA 是旧固件/旧安装下 _config_dir 的硬编码兜底位置（当 TRIM_PKGVAR 未注入时），
# 与运行时 TRIM_PKGVAR（如 /vol1/@appdata/...）可能不在同一物理路径；
# 重装迁移时须把它也当作旧配置来源，否则标注会孤儿在旧目录导致 UI 空白。
LEGACY_APPATA = "/usr/local/apps/@appdata/com.dashboard.nasdeck"


def _config_dir():
    d = os.environ.get("TRIM_PKGVAR")  # 【配置点】飞牛注入的持久数据目录
    if not d:
        # 某些启动路径（如热更新后 restart）可能未注入 TRIM_PKGVAR，
        # 此时不要直接回退到 APP_DIR（重装即被清空），而是主动定位飞牛
        # 持久化 @appdata 目录，保证配置与历史库（history.db）指向同一处，
        # 避免多进程/环境变量差异导致「写入一个库、API 读另一个空库」。
        import glob
        cand = glob.glob("/vol*/@appdata/com.dashboard.nasdeck")
        if cand:
            d = cand[0]
    if not d:
        d = LEGACY_APPATA
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return APP_DIR


def _migrate_legacy_configs():
    """从旧版（配置存 APP_DIR 或旧 @appdata 兜底目录）升级时，
    把已有配置迁移到当前持久目录，避免丢失。由 serve.main() 显式调用
    （原为 import 期执行，重构后移到启动序列）。"""
    cfg = _config_dir()
    if cfg == APP_DIR:
        return
    # 旧配置可能来源（按陈旧程度排序；跳过与当前目标相同的目录，避免无意义的自复制）
    legacy_sources = [APP_DIR, LEGACY_APPATA]
    for name in ("fan_labels.json", "fan_disk_temp.json", "fan_sys_temp.json"):
        dst = os.path.join(cfg, name)
        if os.path.exists(dst):
            continue
        for src_dir in legacy_sources:
            if src_dir == cfg:
                continue
            src = os.path.join(src_dir, name)
            if os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass
                break
