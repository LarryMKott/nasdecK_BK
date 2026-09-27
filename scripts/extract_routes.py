#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由奇偶校验工具（重构验证核心，见 docs/重构计划.md 第八节）。

两种用法：
1) 生成基线（对旧版单文件 app.py，纯正则静态抽取，绝不 import——
   旧代码 import 期会写风扇 sysfs、启动 6 条线程）：
       .venv/bin/python scripts/extract_routes.py

2) 校验新版（cutover 后对 FastAPI 应用实例做 diff，必须为空才算通过）：
       .venv/bin/python scripts/extract_routes.py --check
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "routes_baseline.json"
APP_PY = ROOT / "app.py"

# 匹配 @app.route("path") / @app.route('path', methods=["GET", "POST"])
ROUTE_RE = re.compile(
    r"""^@app\.route\(\s*(['"])(.+?)\1\s*(?:,\s*methods\s*=\s*\[([^\]]*)\])?\s*\)"""
)


def extract_static():
    """正则静态抽取旧 app.py 的全部路由。返回 {(path, method)} 集合。"""
    pairs = set()
    for line in APP_PY.read_text(encoding="utf-8").splitlines():
        m = ROUTE_RE.match(line.strip())
        if not m:
            continue
        path = m.group(2)
        methods = ["GET"]
        if m.group(3):
            methods = [x.strip().strip("\"'") for x in m.group(3).split(",") if x.strip()]
        for meth in methods:
            pairs.add((path, meth.upper()))
    return pairs


def extract_fastapi():
    """导入新应用（app.py 门面），收集 FastAPI 实际注册的路由。"""
    sys.path.insert(0, str(ROOT))
    import app as app_module  # noqa: 新版 app.py 为薄门面，import 无副作用

    pairs = set()
    for route in app_module.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for meth in methods:
            if meth in ("HEAD", "OPTIONS"):  # FastAPI 自动派生，Flask 基线不含
                continue
            pairs.add((path, meth.upper()))
    return pairs


def main():
    if "--check" in sys.argv:
        baseline = {(r["path"], m) for r in json.loads(BASELINE.read_text("utf-8"))["routes"] for m in r["methods"]}
        current = extract_fastapi()
        missing = sorted(baseline - current)
        extra = sorted(current - baseline)
        if not missing and not extra:
            print(f"路由奇偶校验通过：{len(baseline)} 条 path-method 完全一致")
            return 0
        print(f"路由不一致！缺失 {len(missing)} 条，多出 {len(extra)} 条")
        for p, m in missing:
            print(f"  缺失: {m:6} {p}")
        for p, m in extra:
            print(f"  多出: {m:6} {p}")
        return 1

    pairs = extract_static()
    grouped = {}
    for path, meth in sorted(pairs):
        grouped.setdefault(path, []).append(meth)
    payload = {
        "source": str(APP_PY),
        "generated_by": "scripts/extract_routes.py（正则静态抽取，不 import）",
        "route_count": len(pairs),
        "routes": [{"path": p, "methods": grouped[p]} for p in sorted(grouped)],
    }
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"基线已生成：{len(pairs)} 条 path-method 对（{len(grouped)} 个 path）-> {BASELINE}")


if __name__ == "__main__":
    sys.exit(main() or 0)
