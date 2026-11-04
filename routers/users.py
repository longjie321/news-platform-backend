from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from config.db_conf import get_db
from models.users import User
from schemas.users import (UserRequest, UserRegisterRequest, UserAuthResponse,
                           UserInfoResponse, UserUpdateRequest, UserChangePasswordRequest)
# UserRegisterRequest：注册专用（密码要求 ≥6 位）；UserRequest：登录用（只要求非空）
from crud import users
from utils.response import success_response
from utils.auth import get_current_user

router = APIRouter(prefix="/api/user", tags=["user"])


@router.post("/register", summary="Register a new user")
async def register(user_data: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    # 注册逻辑: 验证用户是否存在 -> 创建用户 -> 生成 Token -> 响应结果
    # 用 UserRegisterRequest（而不是 UserRequest）：注册时多校验一条"密码至少 6 位"
    existing_user = await users.get_user_by_username(db, user_data.username)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户已存在")
    user = await users.create_user(db, user_data)
    token = await users.create_token(db, user.id)
    # return {

    response_data = UserAuthResponse(token=token, user_info=UserInfoResponse.model_validate(user))
    return success_response(message="注册成功", data=response_data)

@router.post("/login", summary="User login")
async def login(user_data: UserRequest, db: AsyncSession = Depends(get_db)):
    # 登录逻辑: 验证用户是否存在 -> 验证密码 -> 生成 Token -> 响应结果
    user = await users.authenticate_user(db, user_data.username, user_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = await users.create_token(db, user.id)

    response_data = UserAuthResponse(token=token, user_info=UserInfoResponse.model_validate(user))
    return success_response(message="登录成功", data=response_data)

@router.get("/info", summary="Get current user info")
async def get_user_info(user: User = Depends(get_current_user)):
    return success_response(message="获取用户信息成功", data=UserInfoResponse.model_validate(user))


# 修改用户信息: 验证Token -> 更新（用户输入数据 put提交 -> 请求体参数 -> 定义Pydantic模型类） -> 响应结果
# 参数: 用户输入的 + 验证Token的 + db（调用更新的方法）
@router.put("/update", summary="Update user info")
async def update_user_info(user_data: UserUpdateRequest, user: User = Depends(get_current_user),
 db: AsyncSession = Depends(get_db)):
    user = await users.update_user(db, user.username, user_data)
    return success_response(message="更新用户信息成功", data=UserInfoResponse.model_validate(user))

@router.put("/password", summary="Change password")
async def update_password(
        password_data: UserChangePasswordRequest,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)):
    res_change_pwd = await users.change_password(db, user, password_data.old_password, password_data.new_password)
    if not res_change_pwd:
        # 旧密码填错是"用户输入不对"，属于 400，不能报 500
        # 原来报 500 会让前端以为是服务器故障，从而引导用户去"稍后重试"（完全误导）
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="旧密码不正确")
    return success_response(message="修改密码成功")
