from backend.infrastructure.logging.errring import _ERR_RING
from backend.infrastructure.gateway import get_gateway_user
from backend.infrastructure.version import _VERSION_CHECK
from backend.infrastructure.version import _check_latest_version
import sys
import time

# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
from fastapi import APIRouter, Depends, Request

from backend.infrastructure.gateway import require_admin
from backend.presentation.api.dependencies import _jr, json_body, json_body_as
from backend.presentation.schemas import ErrorsClearRequest, ErrorsTestRequest

router = APIRouter()



@router.post("/api/errors/clear")
def api_errors_clear(_admin = Depends(require_admin)):
    """清空错误历史圈（问题解决后用户手动清掉，不必等重启）。"""
    _ERR_RING.clear()
    return _jr({"ok": True, "cleared": True})

@router.post("/api/errors/test")
def api_errors_test():
    """人为写入一条测试错误记录（验证「错误记录」捕获→展示→清除全链路用）。

    走的是与真实报错完全相同的链路：stderr 打印 → _StderrTee 扫描 → 入圈 →
    report.error_ring 暴露 → 前端弹窗展示。文案明确标注「测试」，避免被当真 bug 排查。
    """
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + "[ERROR] 测试错误记录：这是一条人为写入的示例报错（验证错误记录功能用），不是真实故障，可放心清除", file=sys.stderr)
    return _jr({"ok": True})

@router.get("/api/me")
def api_me(request: Request):
    """返回当前网关登录用户，供前端展示登录身份。"""
    return _jr(get_gateway_user(request))

@router.get("/api/version")
def api_version(request: Request):
    """检测新版本：返回 {current, latest, update_available, url, error}。force=1 强制刷新缓存。"""
    if request.query_params.get("force") == "1":
        _VERSION_CHECK["checked_at"] = 0  # 使缓存失效，触发重查
    return _jr(_check_latest_version())