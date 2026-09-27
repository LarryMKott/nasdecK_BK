# -*- coding: utf-8 -*-
"""FastAPI 依赖：替代 Flask 装饰器钩子与全局 request 对象（硬性规范 #3）。

- json_body：复刻 `request.get_json(force=True, silent=True) or {}` 语义。
  同步端点无法 await，故做成异步依赖预先解析，端点签名注入：
      def handler(request: Request, payload: dict = Depends(json_body)): ...
  解析失败/空 body 返回 None（silent），由端点沿用原 `payload or {}` 语义，
  避免 FastAPI 默认 422 改变错误契约。
- _jr：jsonify + 状态码 的统一替代（出参结构原样透传，不做模型过滤）。
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse


async def json_body(request: Request):
    """强制解析 JSON body（无视 Content-Type，等价 force=True）；
    空 body / 解析失败返回 None（等价 silent=True）。"""
    try:
        raw = await request.body()
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _jr(data, code: int = 200) -> JSONResponse:
    """jsonify(data), code 的直译替代。返回 JSONResponse：
    FastAPI 对 Response 返回值跳过 response_model 过滤，保证出参与 Flask 逐字节兼容。"""
    return JSONResponse(content=data, status_code=code)
