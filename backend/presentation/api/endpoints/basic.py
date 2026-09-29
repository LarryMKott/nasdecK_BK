# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3 批 B：版本检查/错误圈清空经容器访问器（IAppInfo / IHealthService），
# /api/me 网关直读留待阶段 4 随异常处理器迁 presentation 时归位。
import sys
import time

from fastapi import APIRouter, Depends, Request

from backend.container import get_container
from backend.infrastructure.gateway import get_gateway_user, require_admin
from backend.presentation.api.dependencies import _jr

router = APIRouter()



@router.post("/api/errors/clear")
def api_errors_clear(_admin = Depends(require_admin)):
    """清空错误历史圈（问题解决后用户手动清掉，不必等重启）。"""
    get_container().health.clear_error_ring()
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
    force = request.query_params.get("force") == "1"
    return _jr(get_container().app_info.check_latest_version(force=force))
