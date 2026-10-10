from typing import Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator
# field_validator：pydantic v2 起提供的"字段校验钩子"装饰器（v1 里叫 validator，写法不同）


class UserRequest(BaseModel):
    """
    登录 / 注册的通用请求体
    注意：这两个字段以前没写长度限制，导致 username="" password="" 能注册成功，
    而且注册完还能用空账号直接登录 —— 认证等于形同虚设。长度限制必须有。
    补充：光有长度限制还不够 —— "   " 的长度是 3，能过 min_length=1，
    所以下面两个校验函数负责把"纯空白"的凭据也堵掉（2026-09-21 补）。
    """
    username: str = Field(..., min_length=1, max_length=50)
    # min_length=1 堵空串；max_length=50 对齐数据库字段（users.username 是 String(50)），
    #   不设上限时超长用户名由 MySQL 报错，会变成 500 而不是 400

    password: str = Field(..., min_length=1, max_length=72)
    # 上限 72：bcrypt 只认前 72 字节，超出的部分等于没设，不如直接拦住

    @field_validator("username")
    @classmethod
    def username_not_blank(cls, value):
        # 为什么不用 strip 后再校验（那样 "   " 会变成 ""）：
        #   库里已经存在用户名前后带空格的账号（例如 'string ' 和 'string' 并存），
        #   strip 会把它们归一，等于让老账号登不上 —— 修漏洞不该顺手改掉老数据。
        # 所以这里只判断"是不是全是空白"，**不动原值**，非空白输入的行为一个字没变。
        if isinstance(value, str) and not value.strip():
            raise ValueError("用户名不能全为空白字符")
        return value

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value):
        # 同上的道理：只拦"全空白"，不改原值（万一老用户密码前后真带空格）
        if isinstance(value, str) and not value.strip():
            raise ValueError("密码不能全为空白字符")
        return value


class UserRegisterRequest(UserRequest):
    """
    注册专用：比登录多一条密码长度要求
    为什么不直接改 UserRequest —— 登录口不该卡密码强度：
      老用户的密码可能本来就不足 6 位，在登录口拦会让原本能登录的人登不上。
      强度校验只应对"新设置的密码"生效。
    """
    password: str = Field(..., min_length=6, max_length=72)
    # 和 UserChangePasswordRequest.new_password 保持一致（改密码也是 min_length=6）


# user_info 对应的类: 基础类 + Info 类 (id、用户名)
class UserInfoBase(BaseModel):
    """
    用户信息基础数据模型
    """
    nickname: Optional[str] = Field(None, max_length=50, description="昵称")
    avatar: Optional[str] = Field(None, max_length=255, description="头像URL")
    gender: Optional[str] = Field(None, max_length=10, description="性别")
    bio: Optional[str] = Field(None, max_length=500, description="个人简介")


class UserInfoResponse(UserInfoBase):
    id: int
    username: str

    # 模型类配置
    model_config = ConfigDict(
        from_attributes=True  # 允许从 ORM 对象属性中取值
    )


# data 数据类型
class UserAuthResponse(BaseModel):
    token: str
    user_info: UserInfoResponse = Field(..., alias="userInfo")

    # 模型类配置
    model_config = ConfigDict(
        populate_by_name=True,  # alias / 字段名兼容
        from_attributes=True  # 允许从 ORM 对象属性中取值
    )


class UserUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    gender: Optional[str] = None
    bio: Optional[str] = None
    phone: Optional[str] = None


class UserChangePasswordRequest(BaseModel):
    old_password: str = Field(..., alias="oldPassword", description="旧密码")
    new_password: str = Field(..., min_length=6, alias="newPassword", description="新密码")
