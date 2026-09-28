#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAPI 快照工具（G3 门禁，见 docs/整洁架构重构开发计划.md 第三节）。

导出当前应用的 OpenAPI paths 并与基线深比较，防止重构漂移出参/路径/模型。
只比 paths（含 $ref 引用的请求/响应模型形状）；info.version 等元数据不比。

用法：
    python scripts/dump_openapi.py             # 生成/覆盖基线 openapi_baseline.json
    python scripts/dump_openapi.py --check     # 与基线比较，不一致退出码 1
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 仓库根入 path，保证 import app 可用

BASELINE = Path(__file__).resolve().parent / "openapi_baseline.json"


def current_paths() -> dict:
    from fastapi.testclient import TestClient
    import app as app_module
    with TestClient(app_module.app) as client:
        spec = client.get("/openapi.json").json()
    return spec.get("paths", {})


def main():
    paths = current_paths()
    if "--check" in sys.argv:
        base = json.loads(BASELINE.read_text("utf-8"))
        if paths == base:
            print(f"[dump_openapi] G3 通过：{len(base)} 条 path 与基线逐字节一致")
            return
        added = sorted(set(paths) - set(base))
        removed = sorted(set(base) - set(paths))
        changed = sorted(k for k in set(paths) & set(base) if paths[k] != base[k])
        print(f"[dump_openapi] G3 失败：新增 {added}，缺失 {removed}，内容变化 {changed}")
        sys.exit(1)
    BASELINE.write_text(json.dumps(paths, ensure_ascii=False, indent=1, sort_keys=True),
                        encoding="utf-8")
    print(f"[dump_openapi] 基线已生成：{len(paths)} 条 path -> {BASELINE.name}")


if __name__ == "__main__":
    main()
