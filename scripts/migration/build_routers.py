#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性迁移工具：把 app.py 的路由函数抽取并改写为 FastAPI 路由器。

改写规则（函数体逐字保留，只动Flask 特有接口）：
- @app.route("P", methods=["A","B"]) → @router.a("P")（每方法一条装饰器，栈叠）
- @require_admin() 装饰器移除 → 签名注入 `_admin=Depends(require_admin)`
- `request.get_json(...) or {}` → `payload or {}`（payload = Depends(json_body) 预解析）
- `request.args.get(` → `request.query_params.get(`
- `return jsonify(X), CODE` → `return _jr(X, CODE)`；`return jsonify(X)` → `return _jr(X)`
  （括号配平扫描，支持跨行）
- 签名按需注入 `request: Request`

用法：.venv/bin/python scripts/migration/build_routers.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "app.py"
OUT = ROOT / "backend" / "web" / "routers"

# 路由函数名 → 路由器模块
ROUTER_OF = {
    "api_me": "basic", "api_errors_clear": "basic", "api_errors_test": "basic",
    "api_version": "basic",
    "api_all": "all", "api_system": "all",
    "api_raid": "raid", "api_raid_locate": "raid", "api_raid_cc": "raid",
    "api_raid_cc_schedule": "raid", "api_raid_hotspare": "raid",
    "api_raid_copyback": "raid",
    "api_disks": "disks", "api_disks_rename": "disks",
    "api_disks_selftest_get": "disks", "api_disks_selftest_history": "disks",
    "api_disks_selftest_post": "disks", "api_disks_selftest_abort": "disks",
    "api_storage": "res", "api_docker": "res", "api_network_wol": "res",
    "api_network": "res", "api_ports": "res", "api_ports_appicon": "res",
    "api_ports_release": "res",
    "api_metrics": "metrics", "api_history": "metrics",
    "api_fan_set": "fans", "api_fan_pwm_mode": "fans", "api_fan_status": "fans",
    "api_fan_control_get": "fans", "api_fan_control_set": "fans",
    "api_fan_temps": "fans", "api_fan_disk_temp_get": "fans",
    "api_fan_disk_temp_set": "fans", "api_fan_sys_temp_get": "fans",
    "api_fan_sys_temp_set": "fans", "api_fan_rules_get": "fans",
    "api_fan_rules_set": "fans", "api_fan_fcs_post": "fans",
    "api_fan_labels_get": "fans", "api_fan_history": "fans",
    "api_fan_labels_post": "fans",
    "api_alerts": "alerts", "api_alerts_config_get": "alerts",
    "api_alerts_config_set": "alerts", "api_alerts_test": "alerts",
    "api_report": "alerts", "api_diagnostics": "alerts",
}

HEADER = '''# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.gateway import require_admin
from backend.web.deps import _jr, json_body

router = APIRouter()

'''


def route_info(node):
    """(path, methods, admin) 或 None。"""
    path = None
    methods = []
    admin = False
    for d in node.decorator_list:
        if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route" \
                and getattr(d.func.value, "id", "") == "app":
            path = ast.literal_eval(d.args[0])
            for kw in d.keywords:
                if kw.arg == "methods":
                    methods = [ast.literal_eval(e) for e in kw.value.elts]
            if not methods:
                methods = ["GET"]
        if (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "require_admin") \
                or getattr(d, "id", "") == "require_admin":
            admin = True
    return (path, methods, admin) if path else None


def _find_call(src, lparen):
    """src[lparen] == '(' 的调用，返回 (内层文本, 调用结束偏移)。括号配平。"""
    depth = 0
    i = lparen
    j = i
    in_str = None
    while j < len(src):
        c = src[j]
        if in_str:
            if c == "\\":
                j += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'":
            in_str = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[i + 1:j], j + 1
        j += 1
    raise ValueError("unbalanced parens")


def rewrite_jsonify(body_src):
    """return jsonify(X), CODE → return _jr(X, CODE)；return jsonify(X) → return _jr(X)。"""
    out = []
    pos = 0
    for m in re.finditer(r"return\s+jsonify\(", body_src):
        out.append(body_src[pos:m.start()])
        ret_prefix = m.group(0).replace("jsonify(", "")  # 只留 return 前缀，避免双层包裹
        inner, end = _find_call(body_src, m.end() - 1)
        rest = body_src[end:]
        mm = re.match(r"\s*,\s*(\d{3})\s*\n", rest)
        if mm:
            out.append(f"{ret_prefix}_jr({inner}, {mm.group(1)})\n")
            pos = end + mm.end()
        else:
            out.append(f"{ret_prefix}_jr({inner})")
            pos = end
    out.append(body_src[pos:])
    return "".join(out)


def rewrite_body(src, uses_request, uses_payload):
    src = re.sub(r"request\.get_json\(\s*force=True,\s*silent=True\s*\)\s*or\s*\{\}", "payload or {}", src)
    src = re.sub(r"request\.get_json\(\s*force=True\s*\)\s*or\s*\{\}", "payload or {}", src)
    src = re.sub(r"request\.get_json\(\s*silent=True\s*\)\s*or\s*\{\}", "payload or {}", src)
    src = src.replace("request.args.get(", "request.query_params.get(")
    return src


def transform(fn_node):
    path, methods, admin = route_info(fn_node)
    src = ast.get_source_segment(SRC.read_text(encoding="utf-8"), fn_node)
    # 去掉原装饰器（拿函数体：def 行起）
    def_line = src.index("def ")
    doc_src = src[def_line:]
    doc_src = rewrite_jsonify(doc_src)
    doc_src = rewrite_body(doc_src, True, True)

    lines = doc_src.splitlines(keepends=True)
    # def 行改写签名（整体替换，保留行尾换行）
    m = re.match(r"(async def|def)\s+\w+\(\)\s*:", lines[0])
    assert m, lines[0]
    uses_request = any(k in doc_src for k in ("request.", "payload"))
    sig_params = []
    if uses_request:
        sig_params.append("request: Request")
    if "payload" in doc_src:
        sig_params.append("payload: dict = Depends(json_body)")
    if admin:
        sig_params.append("_admin = Depends(require_admin)")
    lines[0] = f"def {fn_node.name}({', '.join(sig_params)}):\n"
    decorators = "".join(f'@router.{meth.lower()}("{path}")\n' for meth in methods)
    return decorators + "".join(lines)


def main():
    srctext = SRC.read_text(encoding="utf-8")
    tree = ast.parse(srctext)
    buckets = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ROUTER_OF:
            buckets.setdefault(ROUTER_OF[node.name], []).append(transform(node))
    for mod, funcs in sorted(buckets.items()):
        p = OUT / f"{mod}.py"
        text = HEADER + "\n\n" + "\n\n".join(funcs)
        if mod == "res":
            # 特例：appicon 图标响应。send_file+make_response → FileResponse
            # （显式 exists 检查保持原 send_file 缺文件走 except→404 的语义）
            text = text.replace(
                "from fastapi import APIRouter, Depends, Request",
                "from fastapi import APIRouter, Depends, Request, Response\nfrom fastapi.responses import FileResponse")
            # 特例：api_network 的 jsonify+自定义 Cache-Control 头 → _jr（JSONResponse 同样支持 .headers）
            text = text.replace(
                'resp = jsonify({"nics": nics, "time": _panel_time()})',
                'resp = _jr({"nics": nics, "time": _panel_time()})')
            # 特例：Flask ("", 404) 空体元组 → Response(status_code=404)
            text = text.replace('return ("", 404)', 'return Response(status_code=404)')
            text = text.replace(
                """        mt = _ICON_MIME.get(os.path.splitext(path)[1].lower(), "image/png")
        resp = make_response(send_file(path, mimetype=mt))""",
                """        if not os.path.exists(path):
            return Response(status_code=404)
        mt = _ICON_MIME.get(os.path.splitext(path)[1].lower(), "image/png")
        resp = FileResponse(path, media_type=mt)""")
        p.write_text(text, encoding="utf-8")
        print(f"生成 {p.relative_to(ROOT)}：{len(funcs)} 条路由")
    total = sum(len(v) for v in buckets.values())
    print(f"共 {total} 条路由函数，映射齐全: {total == len(ROUTER_OF)}")


if __name__ == "__main__":
    sys.exit(main())
