from fastapi import Header, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from config.db_conf import get_db
from crud import users


# 整合: 根据 Token 查询用户, 返回用户
async def get_current_user(
        authorization: str = Header(None),
        db: AsyncSession = Depends(get_db)
):
    """
    从请求头里拿 Token，换成当前用户
    约定：请求头格式是 "Authorization: Bearer xxxxx"
    """
    # 这里用 Header(None) 而不是 Header(...)：不写的话请求头缺失时 FastAPI 会直接返回 422，
    # 而"没登录"应该是 401。改成自己判断，错误码才准确（前端拿到 401 才会跳登录页）
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录，请先登录")

    token = authorization.replace("Bearer ", "").strip()
    # 去掉 "Bearer " 前缀；strip() 防止多打的空格导致 token 对不上

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的令牌")

    user = await users.get_user_by_token(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的令牌或已经过期的令牌")

    return user
