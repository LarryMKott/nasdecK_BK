#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整洁架构分层依赖检查器（G4 门禁，见 docs/整洁架构重构开发计划.md 第三节）。

AST 静态扫描 backend/ 下六个新层目录的 import（含函数级惰性导入），按
「允许依赖矩阵」报违规；豁免走 scripts/layer_waivers.json（只减不增，
阶段 4 出口 = 清零）。附带模块级 import 环检测（环 = 阶段 2 验收项）。

用法：
    python scripts/check_layers.py            # 检查 + 环检测（违规/环 → 退出码 1）
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
WAIVERS = Path(__file__).resolve().parent / "layer_waivers.json"

# 层 → 允许导入的 backend 内层前缀（矩阵见开发计划 3.1）；未列出的层不检查（legacy 过渡区）
RULES = {
    "backend.domain": {"backend.domain"},
    "backend.application": {"backend.domain", "backend.common", "backend.application"},
    "backend.infrastructure": {"backend.domain", "backend.common", "backend.infrastructure"},
    "backend.presentation": {"backend.application", "backend.common", "backend.presentation"},
    "backend.tasks": {"backend.application", "backend.domain", "backend.common", "backend.tasks"},
    "backend.common": set(),
}
# domain/application/common 禁三方库（领域纯标准库原则）；infrastructure/presentation/tasks 允许
NO_THIRDPARTY = ("backend.domain", "backend.application", "backend.common")


def module_name(path: Path) -> str:
    rel = path.relative_to(BACKEND)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # 去 .py 后缀
    return ".".join(("backend", *parts)) if parts else "backend"


def layer_of(mod: str):
    for prefix in RULES:
        if mod == prefix or mod.startswith(prefix + "."):
            return prefix
    return None


def _resolve(node: ast.ImportFrom, mod: str, is_init: bool) -> str:
    """ImportFrom → 绝对模块名（相对导入按当前包定位）。"""
    pkg = mod if is_init else mod.rpartition(".")[0]
    base = pkg
    if node.level > 1:  # 越上级 .. 向上回退
        for _ in range(node.level - 1):
            base = base.rpartition(".")[0]
    if node.module:
        base = f"{base}.{node.module}"
    return base


def imports_of(path: Path, mod: str, is_init: bool):
    """返回 (模块级 import 模块名列表, 全部 import 模块名列表)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top, nested = [], []

    def collect(node, into):
        if isinstance(node, ast.Import):
            into.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            into.append(_resolve(node, mod, is_init) if node.level else (node.module or ""))

    for node in tree.body:
        collect(node, top)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)) and sub not in tree.body:
                    collect(sub, nested)
    return top, top + nested


def scan():
    """返回 (违规列表, 扫描模块数)。违规 = (模块, 被导入模块, 原因)。"""
    violations, checked = [], 0
    for path in sorted(BACKEND.rglob("*.py")):
        mod = module_name(path)
        layer = layer_of(mod)
        if layer is None:
            continue  # legacy 过渡区：归属随各阶段 cutover，暂不检查
        checked += 1
        is_init = path.name == "__init__.py"
        _, all_imports = imports_of(path, mod, is_init)
        for dep in all_imports:
            if not dep or dep == mod:
                continue
            top = dep.split(".")[0]
            if top in sys.stdlib_module_names:
                continue
            if dep.startswith("backend"):
                if layer_of(dep) is not None and not any(
                        dep == p or dep.startswith(p + ".") for p in RULES[layer]):
                    violations.append((mod, dep, f"{layer} 禁止导入"))
            elif layer in NO_THIRDPARTY:
                violations.append((mod, dep, f"{layer} 禁止三方库 {top}"))
    return violations, checked


def dependency_graph():
    """backend.* 全部模块的模块级依赖图（环检测用，含 legacy）。"""
    graph = {}
    for path in sorted(BACKEND.rglob("*.py")):
        mod = module_name(path)
        is_init = path.name == "__init__.py"
        top, _ = imports_of(path, mod, is_init)
        graph[mod] = [d for d in top if d == "backend" or d.startswith("backend.")]
    return graph


def find_cycles(graph):
    color, cycles = {}, []

    def dfs(u, stack):
        color[u] = 1
        stack.append(u)
        for v in graph.get(u, ()):
            if color.get(v) == 1:
                cycles.append(tuple(stack[stack.index(v):]))
            elif color.get(v, 0) == 0:
                dfs(v, stack)
        stack.pop()
        color[u] = 2

    for m in graph:
        if color.get(m, 0) == 0:
            dfs(m, [])
    return cycles


def main():
    waivers = json.loads(WAIVERS.read_text("utf-8")) if WAIVERS.exists() else {}
    violations, checked = scan()
    unwaived, waived = [], 0
    for mod, dep, why in violations:
        allow = waivers.get(mod, [])
        if any(dep == a or dep.startswith(a + ".") for a in allow):
            waived += 1
        else:
            unwaived.append((mod, dep, why))
    print(f"[check_layers] 扫描新层模块 {checked} 个：违规 {len(unwaived)}，豁免抵扣 {waived}")
    for mod, dep, why in unwaived:
        print(f"  VIOLATION {mod} -> {dep}  ({why})")
    cycles = find_cycles(dependency_graph())
    print(f"[check_layers] 模块级依赖环：{len(cycles)}")
    for c in cycles:
        print("  CYCLE " + " -> ".join(c + (c[0],)))
    if unwaived or cycles:
        sys.exit(1)
    print("[check_layers] G4 通过")


if __name__ == "__main__":
    main()
