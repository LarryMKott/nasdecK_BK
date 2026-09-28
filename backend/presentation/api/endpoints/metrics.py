# -*- coding: utf-8 -*-
# 本文件由 scripts/migration/build_routers.py 自 app.py 生成；函数体原样搬迁，
# 仅改写 Flask 特有接口（装饰器/get_json/args/jsonify）为 FastAPI 等价物。
# v2.4 阶段 3：编排收敛至 application.usecases（实时指标 + 历史趋势查询用例），
# 端点只做 解析→用例→透传。
from fastapi import APIRouter, Request

from backend.container import get_container
from backend.presentation.api.dependencies import _jr

router = APIRouter()



@router.get("/api/metrics")
def api_metrics():
    """轻量实时指标：网络吞吐 + 磁盘 I/O + CPU/内存/负载。供前端高频(1s)轮询，不触发重型 /api/all(阵列卡/SMART等)。
    网络速率会把 OVS 桥（如 eno1-ovs）归并到物理口名（eno1），与 /api/system 的合并显示名对齐，
    这样前端按 data-nic 直接覆盖即可。"""
    return _jr(get_container().metrics.panel())

@router.get("/api/history")
def api_history(request: Request):
    """返回历史趋势降采样点（磁盘读/写速率），按 range 时间桶聚合，省流量。"""
    rng = request.query_params.get("range", "24h")
    return _jr(get_container().history_uc.points(rng))
