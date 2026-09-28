#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性搬迁脚本：backend/ 旧布局 → 整洁架构四层布局（留档，见 docs/整洁架构重构开发计划.md「二」映射表）。

数据流：backend_backup_20260928/（搬迁前全量备份，单一事实来源）
        → 逐文件按 DIR_MAP 落位 + 按 CONTENT_MAP 重写 import
        → backend_new/ 完整新树 → 原子替换 backend/。
特例：daemon.py / schedule.py / serve.py 为过渡期顶层胶水（阶段 5 随 UseCase 落地迁 tasks/ 与 main.py），
      本轮原位保留、仅重写其 import；backend/backend/ 空壳杂物不入新树（阶段 7 清理项提前完成）。

用法：python scripts/migration/rehome_clean_arch.py --apply   （不带 --apply 仅干跑打印计划）
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # scripts/migration/ 下深一层，取仓库根
SRC = ROOT / "backend_backup_20260928"
DST = ROOT / "backend_new"

# ---- 文件级落位映射（旧相对路径前缀 → 新相对路径前缀；特例优先于通配）----
FILE_MAP = [
    ("web/deps.py", "presentation/api/dependencies.py"),
    ("web/routers/", "presentation/api/endpoints/"),
    ("web/", "presentation/"),
    ("collectors/", "infrastructure/collectors/"),
    ("fans/", "infrastructure/fans/"),
    ("health/", "infrastructure/health/"),
    ("history/", "infrastructure/persistence/"),
    ("shell.py", "infrastructure/command/cmd_runner.py"),
    ("state.py", "infrastructure/state.py"),
    ("caching.py", "infrastructure/caching.py"),
    ("jsonstore.py", "infrastructure/jsonstore.py"),
    ("paths.py", "infrastructure/config/paths.py"),
    ("version.py", "infrastructure/version.py"),
    ("errring.py", "infrastructure/logging/errring.py"),
    ("gateway.py", "infrastructure/gateway.py"),
]
STAY = ("daemon.py", "schedule.py", "serve.py")  # 过渡期顶层胶水，原位保留

# ---- 内容重写映射（有序：长前缀优先；含 from backend import shell 特例）----
CONTENT_MAP = [
    (re.compile(r"^from backend import shell$", re.M),
     "from backend.infrastructure.command import cmd_runner as shell"),
    ("backend.web.routers", "backend.presentation.api.endpoints"),
    ("backend.web.deps", "backend.presentation.api.dependencies"),
    ("backend.web.app", "backend.presentation.app"),
    ("backend.web.schemas", "backend.presentation.schemas"),
    ("backend.web.staticpages", "backend.presentation.staticpages"),
    ("backend.web", "backend.presentation"),
    ("backend.collectors", "backend.infrastructure.collectors"),
    ("backend.fans", "backend.infrastructure.fans"),
    ("backend.history", "backend.infrastructure.persistence"),
    ("backend.health", "backend.infrastructure.health"),
    ("backend.shell", "backend.infrastructure.command.cmd_runner"),
    ("backend.paths", "backend.infrastructure.config.paths"),
    ("backend.errring", "backend.infrastructure.logging.errring"),
    ("backend.state", "backend.infrastructure.state"),
    ("backend.caching", "backend.infrastructure.caching"),
    ("backend.jsonstore", "backend.infrastructure.jsonstore"),
    ("backend.version", "backend.infrastructure.version"),
    ("backend.gateway", "backend.infrastructure.gateway"),
]

# 新增包骨架（层注释 __init__.py；收集器/风扇/健康沿用命名空间包风格不补 __init__）
SKELETON_INIT = {
    "infrastructure/__init__.py": "# 基础设施层：实现 domain 抽象接口；对接 OS/硬件/SQLite/shell。禁止导入 application/presentation。",
    "infrastructure/command/__init__.py": "# 系统命令执行底层（cmd_runner，原 backend/shell.py）。",
    "infrastructure/config/__init__.py": "# 配置与路径（原 backend/paths.py）。",
    "infrastructure/logging/__init__.py": "# 错误环/日志（原 backend/errring.py）。",
    "infrastructure/persistence/__init__.py": "# SQLite 持久化（原 backend/history/db.py）。",
    "presentation/__init__.py": "# 表现层：FastAPI 路由/Schema/依赖。禁止导入 infrastructure/domain（读数据走 application）。",
    "presentation/api/__init__.py": "",
    "presentation/api/endpoints/__init__.py": "# 路由端点（原 backend/web/routers/*，文件名与路由契约不变）。",
    "application/__init__.py": "# 应用层：UseCase 业务编排。仅导入 domain/common；出参用 DTO，不碰 Pydantic/Web/IO。",
    "application/usecases/__init__.py": "# 阶段 3 落地：按 10 个前端面板逐个收敛。",
    "application/dto/__init__.py": "# 应用层 DTO（阶段 3 落地）。",
    "tasks/__init__.py": "# 后台定时任务（阶段 5 落地：daemon/schedule 循环体改调 UseCase 后迁入）。",
    "common/__init__.py": "# 全局公共组件（阶段 4 归位业务异常等；无业务逻辑）。",
}


def map_rel(rel: Path):
    """旧相对路径 → 新相对路径；返回 None 表示不入新树。"""
    posix = rel.as_posix()
    if posix.startswith("backend/") or "__pycache__" in posix:
        return None  # 空壳杂物 / 缓存
    if posix in STAY:
        return rel
    for old, new in FILE_MAP:
        if posix == old or posix.startswith(old):
            return Path(new + posix[len(old):])
    return rel  # domain/** 与其余顶层文件原样


def rewrite(text: str) -> str:
    for rule in CONTENT_MAP:
        text = rule[0].sub(rule[1], text) if isinstance(rule[0], re.Pattern) \
            else text.replace(rule[0], rule[1])
    return text


def main():
    apply = "--apply" in sys.argv
    files = sorted(SRC.rglob("*.py"))
    plan, skipped = [], []
    for f in files:
        rel = f.relative_to(SRC)
        tgt = map_rel(rel)
        (plan if tgt else skipped).append((rel, tgt))
    print(f"[rehome] 源 .py {len(files)} 个：搬迁 {len(plan)}，丢弃 {len(skipped)}"
          f"（{sorted(str(s[0]) for s in skipped)}）")
    if not apply:
        for rel, tgt in plan:
            if rel != tgt:
                print(f"  {rel} -> {tgt}")
        return
    if DST.exists():
        shutil.rmtree(DST)
    n_moved = 0
    for rel, tgt in plan:
        out = DST / tgt
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rewrite(SRC.joinpath(rel).read_text(encoding="utf-8")), encoding="utf-8")
        n_moved += 1
    for rel, comment in SKELETON_INIT.items():
        out = DST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(comment + "\n", encoding="utf-8")
    print(f"[rehome] 新树就绪：{n_moved} 个模块 + {len(SKELETON_INIT)} 个包骨架 -> {DST}")
    # 原子替换
    old = ROOT / "backend"
    shutil.rmtree(old)
    DST.rename(old)
    print("[rehome] 旧 backend/ 已删除，新结构就位")
    # 根级文件同步改指（app.py 门面 / 测试 patch 目标）
    for f in (ROOT / "app.py", ROOT / "scripts" / "test_app_parsers.py"):
        t = f.read_text(encoding="utf-8")
        f.write_text(rewrite(t), encoding="utf-8")
        print(f"[rehome] 已改指 {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
