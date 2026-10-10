import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.users import User, UserToken
from schemas.users import UserRequest, UserUpdateRequest
from utils import security


def _hash_token(token: str) -> str:
    """
    令牌入库前先算摘要：即使数据库被人拖走，也拿不到能直接用的令牌
    为什么用 sha256 而不是 bcrypt（密码为什么用 bcrypt 这里为什么不）：
      令牌是 secrets 生成的 32 字节随机串，本身没有"被猜到"的可能，
      不需要慢哈希来抵抗暴力破解；而每次鉴权都要算一次，快更重要。
    顺带一提：这里也算"令牌只在服务端留摘要"的通用做法 ——
      明文只在登录响应里返回一次，库里查不到原文。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
    # 输出固定 64 位十六进制，正好放进 UserToken.token 的 String(255)


async def get_user_by_username(db: AsyncSession, username: str):
    stmt = select(User).where(User.username == username)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, user_data: UserRequest):
    hashed_password = security.get_hash_password(user_data.password)
    user = User(username=user_data.username, password=hashed_password)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# 生成 Token
async def create_token(db: AsyncSession, user_id: int):
    # 生成 Token + 设置过期时间 → 查询数据库当前用户是否有 Token → 有: 更新；没有: 添加
    token = secrets.token_urlsafe(32)
    # secrets：密码学安全的随机源（原来用的是 uuid4，虽然也够随机，
    #   但 uuid4 的设计目的是"唯一标识"，不是"当凭证"，用 secrets 更名正言顺）

    token_hash = _hash_token(token)
    # 只有摘要进数据库；明文 token 只在本函数 return 时交给登录接口一次

    # 有效期 180 天（约半年），避免频繁重新登录（实验项目，安全要求不高）
    expires_at = datetime.now() + timedelta(days=180)
    query = select(UserToken).where(UserToken.user_id == user_id)
    result = await db.execute(query)
    user_token = result.scalar_one_or_none()

    if user_token:
        # 已有 token：更新为新 token（db.add 确保被 session 跟踪，commit 才会写入）
        user_token.token = token_hash
        user_token.expires_at = expires_at
        db.add(user_token)
    else:
        # 没有 token：新建一条
        user_token = UserToken(user_id=user_id, token=token_hash, expires_at=expires_at)
        db.add(user_token)

    # 统一提交：保证 login 返回给前端时，token 已经写入数据库
    # （否则前端拿到 token 立即调用接口时，数据库可能还没更新，导致 401）
    await db.commit()

    return token
    # 返回明文给前端；库里存的是它的 sha256 摘要

async def authenticate_user(db: AsyncSession, username: str, password: str):
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not security.verify_password(password, user.password):
        return None

    return user

# 根据 Token 查询用户: 验证 Token → 查询用户
async def get_user_by_token(db: AsyncSession, token: str):
    # 注意：库里存的是摘要，所以要先算一遍摘要再比 —— 直接拿明文比永远查不到
    query = select(UserToken).where(UserToken.token == _hash_token(token))
    result = await db.execute(query)
    db_token = result.scalar_one_or_none()

    if not db_token or db_token.expires_at < datetime.now():
        return None

    query = select(User).where(User.id == db_token.user_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()

# 更新用户信息: update更新 -> 检查是否命中 -> 获取更新后的用户返回
async def update_user(db: AsyncSession, username: str, user_data: UserUpdateRequest):
    # update(User).where(User.username == username).values(字段=值, 字段=值)
    # user_data 是一个Pydantic类型, 得到字典 -> ** 解包
    # 没有设置值的不更新
    query = update(User).where(User.username == username).values(**user_data.model_dump(
        exclude_unset=True,
        exclude_none=True
    ))
    result = await db.execute(query)
    await db.commit()

    # 检查更新
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 获取一下更新后的用户
    updated_user = await get_user_by_username(db, username)
    return updated_user


# 修改密码: 验证旧密码 → 新密码加密 → 修改密码 → 作废旧登录态
async def change_password(db: AsyncSession, user: User, old_password: str, new_password: str):
    if not security.verify_password(old_password, user.password):
        return False

    hashed_new_pwd = security.get_hash_password(new_password)
    user.password = hashed_new_pwd
    # 更新: 由 SQLAlchemy 真正接管这个 User 对象, 确保可以 commit
    # 规避 session 过期或关闭导致的不能提交的问题
    db.add(user)

    # 改完密码必须把该用户的所有令牌作废：
    #   否则"密码已经改了，但之前泄露出去的登录态还能继续用"，改密码就白改了。
    #   前端拿到 401 就会跳登录页，用户重新登录即可。
    await db.execute(delete(UserToken).where(UserToken.user_id == user.id))

    await db.commit()
    await db.refresh(user)
    return True
