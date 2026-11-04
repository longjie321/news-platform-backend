from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """一条对话消息。role 只允许 user / assistant。"""

    # 不能用 str：接口会把 messages 原样转发给 DeepSeek，
    # 一旦允许 role="system"，外部就等于能自己写系统提示词了
    role: Literal["user", "assistant"]

    # 上限 8000 字，防止一条消息塞几 MB 进来烧 token
    content: str = Field(..., min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """AI 问答请求体。model 不传就用后端默认。"""

    # 白名单而不是 str：这个值会原样发给 DeepSeek，
    # 不限制等于拿我们的 key 开了个免费网关
    model: Optional[Literal["deepseek-chat", "deepseek-reasoner"]] = None

    # 最多 50 条，防止堆历史把提示词撑爆
    messages: List[ChatMessage] = Field(..., min_length=1, max_length=50)
