from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """
    对话消息
    role: 说话人 —— 只允许 user / assistant，**不接受 system**
    content: 消息内容
    """
    role: Literal["user", "assistant"]
    # 为什么必须是 Literal 而不是 str（这是一处真实的安全隐患）：
    #   这两个接口是"把客户端传的 messages 原样转发给 DeepSeek"的代理。
    #   如果 role 允许任意字符串，调用方就能自己插一条 {"role":"system", ...}
    #   把自己的指令塞成系统提示词 —— 等于把模型的最高权限交给外部，
    #   既能让它无视后端设定的规则，也能拿来当免费的通用大模型接口用。

    content: str = Field(..., min_length=1, max_length=8000)
    # 加长度上限：不限制的话，一条消息就能塞进几 MB 文本，
    #   既吃内存也直接把 token 烧掉（等于用我们的额度做长文本任务）


class ChatRequest(BaseModel):
    """
    AI 问答请求体：前端传对话历史，模型名不传则用后端默认
    """
    model: Optional[Literal["deepseek-chat", "deepseek-reasoner"]] = None
    # 模型名必须走白名单（原来是无限制的 str）：
    #   这个值会被原样发给 DeepSeek，不限制的话调用方可以换成任意模型
    #   （包括更贵的），等于拿我们的 key 开了一个免费网关。
    #   写成 Literal 的好处：非法值由 Pydantic 直接返回 422，
    #   而且报错信息里会列出允许的取值，不用自己写判断。

    messages: List[ChatMessage] = Field(..., min_length=1, max_length=50)
    # messages 加数量上限：一轮对话最多 50 条，防止有人靠堆历史把提示词撑爆
