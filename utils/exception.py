import os
import traceback

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette import status

# 是否返回详细的错误信息
#   默认 **关**（安全优先）：开着的话，任何 500 响应都会带上 traceback 和服务器文件路径，
#   等于把代码结构告诉外人。想看报错详情时，在 .env 里显式写 DEBUG=true。
#   注意变量名就是 DEBUG（不是 DEBUG_MODE），别写错。
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"


async def http_exception_handler(request: Request, exc: HTTPException):
    """
    处理 HTTPException：业务代码主动抛出的错误
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail,
            "data": None
        }
    )


async def validation_error_handler(request: Request, exc: RequestValidationError):
    """
    处理参数校验失败（422）
    为什么需要它：FastAPI 默认返回的是 {"detail": [...]} 这种格式，
    和我们项目统一的 {code, message, data} 不一样，前端要写两套解析。
    这里把它也翻译成统一格式。
    """
    details = []
    for err in exc.errors():
        location = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "header", "path")]
        details.append({"field": ".".join(location), "msg": err.get("msg", "")})

    first = details[0] if details else {"field": "", "msg": "参数不合法"}
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": 422,
            "message": f"参数校验失败：{first['field']} {first['msg']}",
            "data": {"errors": details}
        }
    )


async def integrity_error_handler(request: Request, exc: IntegrityError):
    """
    处理数据库完整性约束错误（重复数据、外键不存在等）
    """
    error_msg = str(exc.orig)

    # 按"具体的约束名"分别给提示。
    # 这里必须从上往下精确匹配，最后再兜底：
    #   原来只判断 "Duplicate entry" 就一律回"用户名已存在"，
    #   结果用户重复收藏一篇新闻时，也会被告知"用户名已存在"，非常莫名其妙
    if "username_UNIQUE" in error_msg:
        detail = "用户名已存在"
    elif "user_news_unique" in error_msg:
        detail = "已经收藏过这篇新闻了"
    elif "phone_UNIQUE" in error_msg:
        detail = "该手机号已被注册"
    elif "FOREIGN KEY" in error_msg:
        detail = "关联数据不存在"
    elif "Duplicate entry" in error_msg:
        detail = "数据重复，请勿重复提交"
    else:
        detail = "数据约束冲突，请检查输入"

    error_data = None
    if DEBUG_MODE:
        error_data = {
            "error_type": "IntegrityError",
            "error_detail": error_msg,
            "path": str(request.url)
        }

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "code": 400,
            "message": detail,
            "data": error_data
        }
    )


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    """
    处理 SQLAlchemy 数据库错误
    """
    error_data = None
    if DEBUG_MODE:
        error_data = {
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
            "traceback": traceback.format_exc(),
            "path": str(request.url)
        }

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": 500,
            "message": "数据库操作失败，请稍后重试",
            "data": error_data
        }
    )


async def general_exception_handler(request: Request, exc: Exception):
    """
    处理所有未捕获的异常
    """
    error_data = None
    if DEBUG_MODE:
        error_data = {
            "error_type": type(exc).__name__,
            "error_detail": str(exc),
            "traceback": traceback.format_exc(),
            "path": str(request.url)
        }

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": 500,
            "message": "服务器内部错误",
            "data": error_data
        }
    )
