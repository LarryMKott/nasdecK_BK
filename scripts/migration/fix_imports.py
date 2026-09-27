#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拆分配套：对 backend/ 自动生成模块迭代补 import。

流程：pyflakes 找未定义名（F821）→ 查符号表（各模块顶层定义）或 stdlib 映射
→ 在文件头 marker 后插入 import → 重复直至收敛。

约定：
- run_cmd / sudo_cmd 是测试 monkeypatch 接缝，消费方一律模块属性调用
  （split_app.py 已把调用点改写为 shell.run_cmd(...)），本脚本补 `from backend import shell`；
- 其余跨模块引用用 from-import（纯函数搬迁，无重赋值）；
- web/ 下路由函数属阶段 6 手工迁移，本脚本对 web/ 只补项目内符号，不因
  flask/fastapi 未定义而报错。
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
MARKER = "# --split--"

# stdlib / 第三方名 → import 语句
STDLIB_IMPORTS = {
    "re": "import re", "os": "import os", "time": "import time", "json": "import json",
    "glob": "import glob", "sys": "import sys", "errno": "import errno",
    "socket": "import socket", "signal": "import signal", "platform": "import platform",
    "shutil": "import shutil", "functools": "import functools",
    "collections": "import collections", "ipaddress": "import ipaddress",
    "threading": "import threading", "sqlite3": "import sqlite3",
    "subprocess": "import subprocess", "random": "import random",
    "hashlib": "import hashlib", "base64": "import base64", "smtplib": "import smtplib",
    "email": "import email", "email.mime": "import email.mime",
    "urllib": "import urllib.request, urllib.error",
    "urllib.request": "import urllib.request",
    "urllib.parse": "import urllib.parse",
    "wsgiref": "from wsgiref.simple_server import make_server",
    "datetime": "import datetime", "_threading": "import threading as _threading",
    "_glob": "import glob as _glob", "_sqlite3": "import sqlite3 as _sqlite3",
    "requests": "import requests",
}

# FastAPI 相关名（web 层阶段 6 迁移时手工处理，这里不自动补）
FASTAPI_NAMES = {"request", "jsonify", "Response", "stream_with_context",
                 "send_from_directory", "make_response", "send_file", "Flask",
                 "wraps", "app"}


def module_names():
    """符号表：name → 'backend.xxx.yyy'。含包/模块名本身（shell、state...）。"""
    table = {}
    for py in BACKEND.rglob("*.py"):
        rel = py.relative_to(BACKEND).with_suffix("")
        dotted = "backend." + ".".join(rel.parts) if str(rel) != "." else "backend"
        mod_name = rel.parts[-1] if str(rel) != "." else "backend"
        if mod_name != "__init__":
            table[mod_name] = f"from backend import {mod_name}" if "." not in dotted.replace("backend.", "") else None
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            print(f"语法错误 {py}: {e}", file=sys.stderr)
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                table.setdefault(node.name, dotted)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        table.setdefault(t.id, dotted)
    return table


def pyflakes_undefined(path):
    out = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(path)],
        capture_output=True, text=True).stdout
    undefined = set()
    for line in out.splitlines():
        m = re.search(r"undefined name '([^']+)'", line)
        if m:
            undefined.add(m.group(1))
    return undefined


def add_imports(path, imports):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    insert_at = 1 if lines and lines[0].startswith(MARKER) else 0
    existing = text
    new_lines = [i + "\n" for i in sorted(imports) if i + "\n" not in existing]
    if not new_lines:
        return False
    lines[insert_at:insert_at] = new_lines + ["\n"]
    path.write_text("".join(lines), encoding="utf-8")
    return True


def main():
    table = module_names()
    targets = [p for p in BACKEND.rglob("*.py") if p.name != "__init__.py"]
    for rnd in range(6):
        pending = {}
        for py in targets:
            rel = str(py.relative_to(BACKEND))
            names = pyflakes_undefined(py)
            if not names:
                continue
            imports = set()
            for n in sorted(names):
                if n in FASTAPI_NAMES and rel.startswith("web"):
                    continue  # 阶段 6 手工处理
                if n == "shell":
                    imports.add("from backend import shell")
                elif n in table and table[n]:
                    owner = table[n]
                    if owner != "backend." + rel.replace("/", ".").removesuffix(".py"):
                        imports.add(f"from {owner} import {n}")
                elif n in STDLIB_IMPORTS:
                    imports.add(STDLIB_IMPORTS[n])
                else:
                    print(f"未解决: {rel}: {n}")
            if imports:
                pending[py] = imports
        if not pending:
            print(f"第 {rnd} 轮：全部收敛，无未定义名")
            return 0
        for py, imports in pending.items():
            add_imports(py, imports)
        print(f"第 {rnd} 轮：{len(pending)} 个文件补了 import")
    print("6 轮未收敛，存在硬伤", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
