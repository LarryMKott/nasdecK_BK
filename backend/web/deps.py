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
from pydantic import BaseModel


class BodyFormatError(Exception):
    """请求体结构非法（非 JSON 对象 / 未通过模型结构校验）。
    由 create_app 的全局 handler 统一转 422 {"ok": false, "error": ...}。
    原 Flask 对这类 body 会因 .get 崩成 500，统一 422 仅错误路径差异（更友好）。"""


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


def json_body_as(model: type[BaseModel]):
    """带模型的 body 依赖工厂：结构校验 + OpenAPI 文档化，字段级校验仍在路由内。

    用法：def handler(payload: dict = Depends(json_body_as(FanSetRequest))): ...
    - 空 body / 解析失败 → {}（与原 get_json(silent=True) or {} 语义一致）
    - body 非 JSON 对象 / 模型结构校验失败 → BodyFormatError（全局 handler → 422）
      模型全部字段可选 + extra=allow，合法对象必过校验，不产生新拒绝
    - 返回原始 dict，路由内原有 data.get(...) 校验逐字保留
    """
    async def dep(request: Request) -> dict:
        raw = await json_body(request)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise BodyFormatError("请求体需为 JSON 对象")
        model.model_validate(raw)  # 宽松：字段全 Any + extra=allow，仅校验结构
        return raw
    return dep
