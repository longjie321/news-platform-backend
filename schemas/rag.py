"""RAG 问答接口的请求模型。"""

from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.ai import ChatMessage


class RagChatRequest(BaseModel):
    """POST /api/rag/chat 的请求体。

    校验放在这一层，不合法的请求进不了业务代码，后面就不用到处判空。
    三个字段都设了上限——不设的话，构造一个大请求就能把提示词撑爆。
    """

    # 必填。最少 1 个字、最多 500 字，防止把整篇文章当问题传进来
    question: str = Field(..., min_length=1, max_length=500, description="用户问题")

    # 选填。前端本来就存着消息列表，所以不再单独落库；
    # 最多 20 条是防护：接口不能依赖前端自觉
    history: Optional[List[ChatMessage]] = Field(None, max_length=20, description="之前的对话，用于多轮")

    # 选填，1~10。不传就用配置里的 TOP_K
    top_k: Optional[int] = Field(None, ge=1, le=10, description="检索几块新闻，不传用默认值")
