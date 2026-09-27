# -*- coding: utf-8 -*-
"""FastAPI 应用组装（对应原 `app = Flask(__name__)` + 50 条散装路由的收口）。

- 统一全局异常处理器（硬性规范 #5）：403 网关鉴权 / 422 结构校验 / 500 兜底，
  错误体统一为项目既有约定 {"ok": false, "error": ...}
- OpenAPI 文档：/docs、/openapi.json（经网关时同样受飞牛登录态保护）
- 路由：8 组业务路由器 + 静态页路由器，path/method 与 Flask 版逐条一致
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.gateway import GatewayAuthError
from backend.version import APP_VERSION


def create_app() -> FastAPI:
    app = FastAPI(
        title="nasdash",
        version=APP_VERSION.lstrip("v"),
        description="飞牛OS NAS 硬件监控面板（FastAPI 重构版）",
        docs_url="/docs",            # OpenAPI 交互文档
        redoc_url=None,              # 不开 redoc，减少暴露面
        openapi_url="/openapi.json",
    )

    # ---- 统一异常处理 ----

    @app.exception_handler(GatewayAuthError)
    async def _gateway_auth_handler(request: Request, exc: GatewayAuthError):
        # 原 require_admin 装饰器的 403 响应体，逐字节保持
        return JSONResponse(content=exc.body, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        # FastAPI 默认 422 体是 {"detail": [...]}；统一为项目约定格式
        return JSONResponse(content={"ok": False, "error": "invalid request"}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception):
        # 兜底 500：原 Flask 为 HTML traceback 页；统一为 JSON 约定格式
        # （traceback 照常进 stderr → 错误历史圈，行为不变）
        return JSONResponse(content={"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                            status_code=500)

    # ---- 路由注册（path/method 与 Flask 版逐条一致，见 scripts/routes_baseline.json）----
    from backend.web.routers import (alerts, all as api_all, basic, disks, fans,
                                     metrics, raid, res)
    from backend.web import staticpages

    for router in (basic.router, api_all.router, raid.router, disks.router,
                   res.router, metrics.router, fans.router, alerts.router,
                   staticpages.router):
        app.include_router(router)
    return app
