import json
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config.ai_conf import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEFAULT_MODEL
from config.db_conf import get_db
from crud import users
from schemas.ai import ChatRequest

router = APIRouter(prefix="/api", tags=["ai"])


@router.post("/aichat", summary="AI chat (streaming)")
async def aichat(data: ChatRequest,
                 authorization: Optional[str] = Header(None),
                 db: AsyncSession = Depends(get_db)):
    """
    AI 问答代理接口：前端不再直连 DeepSeek，key 保存在后端
    使用 SSE 流式转发，保持前端的打字机效果
    登录可选：不带 token 也能用（匿名），带 token 可在后续做用户级限流/记录
    """
    # 可选认证：有 token 就解析登录用户，没有就当作匿名调用
    user = None
    if authorization:
        token = authorization.replace("Bearer ", "")
        user = await users.get_user_by_token(db, token)
        # token 无效不报错，仅当作匿名（不阻塞 AI 问答本身）

    # 检查后端是否配置了 DeepSeek key（key 从 .env 读取，没配的话给出友好提示）
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="后端未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": data.model or DEFAULT_MODEL,
        "messages": [msg.model_dump() for msg in data.messages],
        "stream": True,
    }

    async def sse_proxy():
        # 流式请求 DeepSeek，把响应一行行转发给前端
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                # DeepSeek 返回非 200：把错误信息透传给前端展示
                if resp.status_code != 200:
                    error_body = (await resp.aread()).decode("utf-8", errors="ignore")
                    yield f"data: {json.dumps({'error': {'message': f'DeepSeek 接口错误({resp.status_code}): {error_body}'}})}\n\n"
                    return
                # 正常响应：原样转发 SSE 流（data: {...} 行 + 空行）
                async for line in resp.aiter_lines():
                    yield line + "\n"

    return StreamingResponse(
        sse_proxy(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    # 这两个响应头和 /api/rag/chat 保持一致（那边漏了会导致行为不一致）：
    #   Cache-Control: no-cache    → 别让浏览器缓存这段流
    #   X-Accel-Buffering: no      → 前面有 Nginx 之类的代理时不要缓冲，
    #                                否则前端会"攒一大段才一次性显示"，打字机效果就没了
