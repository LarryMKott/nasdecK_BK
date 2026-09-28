# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3：编排逻辑收敛至 application.usecases.dashboard_usecase（端点只做 解析→用例→透传）。
from fastapi import APIRouter, Request

from backend.container import get_container
from backend.presentation.api.dependencies import _jr

router = APIRouter()


@router.get("/api/all")
def api_all(request: Request):
    return _jr(get_container().dashboard.execute(request.query_params.get("force") == "1"))

# 【补遗】api_system 在初版生成脚本中漏映射，从 git 基线（e9dcec4）手工迁移。
# 板块独立接口：切换导航只拉当前板块，避免全量 /api/all。
@router.get("/api/system")
def api_system(request: Request):
    """系统资源板块（CPU/内存/温度/风扇/GPU/网卡 + 主板/内存条），供 #system 与 #fan 按需刷新。"""
    return _jr(get_container().dashboard.system_panel(request.query_params.get("force") == "1"))
