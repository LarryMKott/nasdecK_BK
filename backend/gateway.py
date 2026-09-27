# -*- coding: utf-8 -*-
"""飞牛统一网关用户身份（自 app.py L81-111 搬迁；装饰器钩子改为 FastAPI 依赖注入）。

官方文档要求：访问经网关时，fnOS 先校验登录态，再通过 Header 转发用户信息
（X-Trim-Userid / X-Trim-Isadmin / X-Trim-Username）。应用必须以网关转发
Header 为准，「不要信任客户端传入的用户 ID」。本应用仅经网关暴露
（裸端口 9800 仅本地兜底、不对外），故浏览器请求必带这些 Header。

【迁移差异】原 `@require_admin()` 装饰器 → FastAPI 依赖 `Depends(require_admin)`；
鉴权失败不再由装饰器直接返回响应，而是抛出 GatewayAuthError，由 create_app()
注册的全局异常处理器统一转 403 JSONResponse（错误体与原装饰器逐字节一致）。
"""
from fastapi import Request


class GatewayAuthError(Exception):
    """require_admin 校验失败。body 即原 Flask 403 响应体。"""

    def __init__(self, body):
        self.body = body
        super().__init__(body.get("error", ""))


def get_gateway_user(request: Request):
    """读取网关转发的可信用户上下文；未经过网关时 uid 为空（authenticated=False）。"""
    h = request.headers
    uid = (h.get("X-Trim-Userid") or "").strip()
    return {
        "uid": uid or None,
        "username": (h.get("X-Trim-Username") or "").strip() or None,
        "isAdmin": (h.get("X-Trim-Isadmin") or "").strip().lower() == "true",
        "authenticated": bool(uid),  # 有 Userid 即视为网关已校验登录态
    }


def require_admin(request: Request):
    """FastAPI 依赖：要求经网关鉴权且为管理员，否则抛 403。
    用于所有配置写入 / 硬件控制类「管理接口」（文档：管理接口需要管理员身份）。
    用法：def handler(payload = Depends(json_body), _admin = Depends(require_admin))"""
    u = get_gateway_user(request)
    if not u["authenticated"]:
        raise GatewayAuthError({"ok": False, "error": "unauthorized: gateway login required"})
    if not u["isAdmin"]:
        raise GatewayAuthError({"ok": False, "error": "forbidden: admin required"})
    return u
