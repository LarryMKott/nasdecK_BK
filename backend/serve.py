# -*- coding: utf-8 -*-
"""应用启动与 HTTP 服务（替代原 app.py `__main__` 块 + wsgiref 网关）。

【迁移差异】
- 服务器：werkzeug/Flask app.run + wsgiref(Unix Socket) → uvicorn（ASGI，生产级）
- 网关模式：监听 Unix Socket（uvicorn uds=），bind 后保持 chmod 0o777 供飞牛网关
  跨用户连接，退出清理 socket——语义与原 _serve_gateway 一致
- TCP 侧车：127.0.0.1:TRIM_SERVICE_PORT 由线程内 uvicorn.Server 提供（原 wsgiref
  make_server），线程内须禁用信号处理器（signal 只能在主线程装）
- 前缀中间件：WSGI 版 _PrefixMiddleware → 纯 ASGI 版（改 scope 而非 environ）
- import 期副作用全部显式化为本函数的启动序列，时序与原版等价：
  stderr 错误圈 → 配置迁移 → 历史库 → 风扇模式恢复 → 后台线程 → 监听

【配置点】环境变量（与原版一致，由 cmd/main 注入）：
- NAS_DECK_GATEWAY=1   启用网关模式（Unix Socket）
- APP_SOCKET           socket 路径（默认 APP_DIR/app.sock）
- GATEWAY_PREFIX       网关转发前缀（默认 /app/com.dashboard.nasdeck）
- TRIM_SERVICE_PORT    本地 TCP 端口（manifest service_port=9800 注入）
"""
import os
import threading

import uvicorn

from backend.infrastructure.logging.errring import install_error_ring
from backend.infrastructure.config.paths import APP_DIR, _migrate_legacy_configs


class _PrefixMiddleware:
    """纯 ASGI 前缀中间件：剥掉网关转发前缀再交给应用（对应原 WSGI 版）。

    必须包裹 ASGI app 而非改 app 对象本身，否则 __call__ 会再次回调形成无限递归。"""

    def __init__(self, asgi_app, prefix):
        self.asgi_app = asgi_app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope["path"] = path[len(self.prefix):] or "/"
                scope["root_path"] = self.prefix  # 等价 WSGI SCRIPT_NAME
        await self.asgi_app(scope, receive, send)


class _GatewayUdsServer(uvicorn.Server):
    """网关 Unix Socket 服务：bind 后 chmod 0o777（网关进程以其他用户连接）。"""

    def __init__(self, config, socket_path):
        super().__init__(config)
        self._socket_path = socket_path

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        try:
            os.chmod(self._socket_path, 0o777)
        except OSError:
            pass


def _tcp_sidecar(app, port):
    """本地 TCP 侧车：线程内 uvicorn（原 wsgiref make_server 的替代）。"""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    server.install_signal_handlers = lambda: None  # 信号处理器只能装在主线程
    threading.Thread(target=server.run, daemon=True, name="tcp-sidecar").start()


def main():
    # ---- 启动序列（原 import 期副作用显式化；顺序与原模块自上而下执行等价）----
    install_error_ring()                      # 原 L79：stderr 错误圈
    _migrate_legacy_configs()                 # 原 L187：旧配置迁移
    from backend.infrastructure.persistence.db import _init_history_db
    _init_history_db()                        # 原 L6851：历史库建表/补列
    from backend.infrastructure.fans.modes import _restore_fan_modes
    _restore_fan_modes()                      # 原 L1428：启动即恢复风扇模式，避免重启后全速
    from backend.daemon import start_background_workers
    start_background_workers()                # 原 6 处线程 start

    # ---- 组装应用 ----
    from backend.presentation.app import create_app
    app = create_app()

    # ---- 监听（原 __main__ 块）----
    app_dir = APP_DIR  # 原 __main__ 局部 APP_DIR 同源（paths.APP_DIR 锚定包父目录）
    if os.environ.get("NAS_DECK_GATEWAY") == "1":  # 【配置点】飞牛网关模式
        socket_path = os.environ.get("APP_SOCKET") or os.path.join(app_dir, "app.sock")
        # 网关转发请求路径带前缀 /app/{appname}，去掉前缀再交给原路由
        gw_prefix = (os.environ.get("GATEWAY_PREFIX") or "/app/com.dashboard.nasdeck").rstrip("/")
        asgi_app = app
        if gw_prefix and gw_prefix != "/":
            asgi_app = _PrefixMiddleware(app, gw_prefix)
        # 同时监听 127.0.0.1:TRIM_SERVICE_PORT（由 manifest service_port 注入），
        # 供飞牛网关按端口转发；仅绑定本地回环，不对外暴露。网关既可通过
        # app.sock 也可通过 127.0.0.1:service_port 访问，兼容 fygo-browser/app-cleaner/Hermes 三种转发模式。
        service_port = (os.environ.get("TRIM_SERVICE_PORT") or "").strip()
        if service_port:
            _tcp_sidecar(app, int(service_port))
        socket_path = os.path.abspath(socket_path)
        parent = os.path.dirname(socket_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            if os.path.exists(socket_path):
                os.unlink(socket_path)
        except OSError:
            pass
        config = uvicorn.Config(asgi_app, uds=socket_path, log_level="info",
                                access_log=True)
        try:
            _GatewayUdsServer(config, socket_path).run()
        finally:
            try:
                os.unlink(socket_path)
            except OSError:
                pass
    else:
        # 本地/CI 模式：裸 TCP 便于测试（不设 TRIM_SERVICE_PORT 则默认 9800）
        env_port = (os.environ.get("TRIM_SERVICE_PORT") or "").strip()
        port = int(env_port) if env_port else 9800
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
