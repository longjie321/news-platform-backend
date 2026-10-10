from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from utils.exception import (
    http_exception_handler,
    validation_error_handler,
    integrity_error_handler,
    sqlalchemy_error_handler,
    general_exception_handler,
)


def register_exception_handlers(app):
    """
    注册全局异常处理

    关于注册顺序（这点别被"子类要在父类前面"带偏）：
      Starlette 查找处理器时是按 type(exc).__mro__ 从上往下逐个匹配的，
      命中第一个就返回 —— 也就是说**注册顺序完全不影响结果**。
      这里按"具体 → 抽象"排列只是为了读起来清楚，不是必须的。
      （真正必须按顺序注册的是 FastAPI 的中间件洋葱模型，两者别混）
    """
    app.add_exception_handler(HTTPException, http_exception_handler)  # 业务主动抛的错误
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # 参数校验（422）
    app.add_exception_handler(IntegrityError, integrity_error_handler)  # 唯一约束/外键等完整性冲突
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)  # 其它数据库错误
    app.add_exception_handler(Exception, general_exception_handler)  # 兜底
