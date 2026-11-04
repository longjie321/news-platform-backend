# 区域：RAG 问答接口（routers 层）
# 作用：提供 2 个接口

# ------------------------------------------------------------
# 第一部分：导包（3 个标准库 + 2 个 FastAPI + 4 个项目模块）
# ------------------------------------------------------------

import json
# 把 Python 自带的 JSON 库拿进来

import time
# 把 Python 自带的时间库拿进来

import httpx
# 把 HTTP 客户端库拿进来

from fastapi import APIRouter, Depends, HTTPException
# 【2026-09-20 补的 Depends】它是 FastAPI 的"依赖注入"工具：

from fastapi.responses import StreamingResponse
# 从 fastapi.responses 里取出"流式响应"类

from config.ai_conf import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEFAULT_MODEL
# 从 AI 配置文件里取出三个常量

from config.db_conf import get_db
# 取出"给每个请求发一个数据库会话"的函数
# 【2026-09-20 新加的】原来本文件完全不碰数据库（RAG 只查向量库），
#   现在加了"统计类问题直查数据库"，所以要能拿到会话

from sqlalchemy.ext.asyncio import AsyncSession
# 取出异步版的"数据库会话"类，只用它做类型标注
# 【2026-09-20 新加的】给 rag_chat 的 db 参数写类型用（不写也能跑，但编辑器就没提示了）

from crud.rag import build_structured_hits, detect_structured_intent, retrieve
# 从 RAG 核心逻辑里取出三个函数

from schemas.rag import RagChatRequest
# 取出"请求体校验模型"

from utils.vectorstore import count
# 取出"数向量库有多少块"的函数

router = APIRouter(prefix="/api/rag", tags=["rag"])
# 造一个"路由分组"，本文件所有接口都会自动带上 /api/rag 前缀

# ------------------------------------------------------------
# 第二部分：小工具① —— 拼 SSE 报文
# ------------------------------------------------------------

def sse(event: str, data: dict) -> str:
# 定义函数，把"事件名 + 数据"拼成 SSE 协议要求的字符串
    """把一条消息拼成 SSE 格式：事件名 + 数据 + 空行"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
# 拼出 SSE 报文并返回

# ------------------------------------------------------------
# 第三部分：小工具② —— 拼发给模型的 messages
# ------------------------------------------------------------

def build_messages(question: str, hits: list, history: list) -> list:
# 定义函数，把"系统提示词 + 历史对话 + 本次问题"拼成模型要的格式
    """
    拼发给模型的 messages
    hits 不为空 → 把新闻资料写进 system 提示词，让模型照着答
    hits 为空   → 只给一个普通的系统提示词
    """
    if hits:
# 判断"这次检索到资料了吗"
        blocks = []
# 准备一个空列表，装"每一段资料"的文本
        for i, hit in enumerate(hits, start=1):
# 遍历每一条检索结果，同时拿到"序号"
            blocks.append(f"[资料{i}]《{hit['meta']['title']}》\n{hit['text']}")
# 拼一段资料的文本，加进 blocks

        references = "\n\n".join(blocks)
# 把所有资料段用"空行"串成一大段文字
        system = (
            "你是头条新闻助手。请优先依据下面的新闻资料回答用户问题，"
            "并用「根据资料1」这样的方式标注你的依据。"
            "如果这些资料和问题无关，就直接用你自己的知识回答，不要硬扯资料。\n\n"
            f"【新闻资料】\n{references}"
        )
# 拼出系统提示词（system prompt）
    else:
# 没检索到资料时，给一个最简提示词
        system = "你是头条新闻助手，你内接的模型是deepseek大模型，请简洁、准确地回答用户的问题。"
        # 没有资料时给一个最简提示词（不带任何新闻，避免模型乱编来源）

    messages = [{"role": "system", "content": system}]
# 把系统提示词装成第一条消息

    if history:
        messages += [msg.model_dump() for msg in history]
# 把前端传来的历史对话，一条条追加进消息列表

    messages.append({"role": "user", "content": question})
# 把本次问题作为最后一条消息加进去
    return messages
# 返回拼好的消息列表
    # 最终形状（多轮对话时）：
    #   [ {"role":"system", ...},
    #     {"role":"user", ...}, {"role":"assistant", ...},   ← 历史（成对出现）
    #     {"role":"user", "content": 本次问题} ]              ← 最后一定是本次问题

# ------------------------------------------------------------
# 第四部分：小工具③ —— 请求 DeepSeek 的流式接口
# ------------------------------------------------------------

async def stream_ai(messages: list):
# 定义**异步生成器**函数：向上游请求流式回答，一段段 yield 出来
    """
    请求 DeepSeek 的流式接口，一段段把正文吐出来
    产出：(正文片段, 错误信息)；正常时错误信息是 None
    """
    async with httpx.AsyncClient(timeout=120) as client:
# 创建一个异步 HTTP 客户端，用完自动关闭
        async with client.stream(
# 发起一个**流式**请求（不等整个响应下载完，边下边处理）
            "POST",
# 第一个位置参数 = HTTP 方法
            f"{DEEPSEEK_BASE_URL}/chat/completions",
# 第二个参数 = 要请求的完整 URL
            headers={
# 设置请求头
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": DEFAULT_MODEL, "messages": messages, "stream": True},
# 设置请求正文
        ) as resp:
# 这次的 `as resp` 拿到的是"响应对象"
            if resp.status_code != 200:
# 检查上游返回的状态码
                yield None, f"AI 接口错误({resp.status_code})，请稍后再试"
# **产出一对值**：(None, 错误信息)
                return
# 结束这个生成器，后面不再产出任何东西

            async for line in resp.aiter_lines():
# 一行一行地读上游返回的内容
                if not line.startswith("data: "):
# 过滤掉"不是数据行"的内容
                    continue
# 跳过这一行，直接读下一行

                data = line[6:].strip()
# 把行首的 "data: " 前缀去掉，再去掉首尾空白

                if data == "[DONE]":
# 判断"上游是不是发完了"
                    break
# 结束这个 async for 循环，不再读后面的内容

                try:
# 尝试解析这一行的 JSON
                    obj = json.loads(data)
# 把 JSON 字符串解析成 Python 字典
                except json.JSONDecodeError:
# 只捕获"JSON 解析失败"这一种异常，然后跳过这一行
                    continue
                # 偶尔有切分不完整的行，跳过就行
                # （流式传输是一块块到达的，理论上偶尔会有边界情况）

                piece = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
# 从这一帧里取出"正文片段"，层层兜底防止报错
                if piece:
# 判断"这一帧到底有没有正文"
                    yield piece, None
# 把这一小段正文"产出"给调用方

# ------------------------------------------------------------
# 第五部分：接口① —— 智能问答（流式）
# ------------------------------------------------------------

@router.post("/chat", summary="智能问答（流式）")
# **装饰器** —— 把下面那个函数注册成 "POST /api/rag/chat" 接口
async def rag_chat(
    data: RagChatRequest,
    db: AsyncSession = Depends(get_db),
):
# 定义接口的处理函数（收到请求时执行它）
    """
    智能问答

    两条路（2026-09-20 起）：
      ① 统计类问题（"一共有多少篇""最长的是哪一篇"）→ 直查数据库，把数字当资料
         —— 因为这类问题向量检索答不了：它只会"找相似"，不会 COUNT / ORDER BY
      ② 其余问题 → 走原来的向量检索：检索到相关的就带着资料回答，没有就按普通 AI 回答
    """
    if not DEEPSEEK_API_KEY:
# 检查"密钥配了没有"
        raise HTTPException(status_code=500, detail="后端未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置")
# 抛一个 HTTP 异常（FastAPI 会自动转成 JSON 错误响应）

    # ★ 第一步：先判断这是不是"向量检索答不了"的统计类问题（2026-09-20 新增）
    intent = detect_structured_intent(data.question)

    if intent:
        # 统计类 → 直查数据库，把结果拼成一条"资料"。
        # 它返回的结构和向量检索的 hits 一模一样，所以下面那些代码一行都不用改。
        structured_hits = await build_structured_hits(db, intent, data.question)

        result = {
            "hits": structured_hits,
            "top_score": 1.0,
            # mode 仍然用 "rag"：这样前端的模式标签、来源卡片逻辑全都不用动。
            # 是不是"查库来的"，看来源卡片的分数（固定 1.0）就能分辨。
            "mode": "rag",
        }
    else:
        # 其余问题 → 走原来的向量检索（这一段是老代码，一行没动）
        result = await retrieve(data.question, data.top_k)
# 调用检索函数，拿到"资料 + 最高分 + 模式"

    hits = result["hits"] if result["mode"] == "rag" else []
# 只有 rag 模式才把资料留着，ai 模式一律清空

    sources = [
# 把 hits 转成"给前端展示的来源列表"（列表推导式 + 字典）
        {
            # 花括号 = 字典，代表"一条来源"
            "title": hit["meta"].get("title", ""),
            "category": hit["meta"].get("category", ""),
            "score": hit["score"],
        }
        for hit in hits
        # 列表推导式的"循环部分"：把 hits 里每个元素叫 hit，
        #   上面那个字典就是"每个 hit 加工后的结果"。
        #   完整读法："对于 hits 里的每个 hit，生成一个含 title/category/score 的字典"
    ]
    # 整理成给前端展示的来源列表
    # ⚠️ 注意这里**只挑了三样**（标题/分类/分数），没有把整块正文发过去 ——
    #    因为前端只需要显示"答案出自哪几篇"，正文在模型回答里已经体现了。
    #    少发数据 = 响应更小 = 更快

    messages = build_messages(data.question, hits, data.history)
# 拼出发给模型的消息列表

    async def event_stream():
# 在函数内部再定义一个**异步生成器**，它才是真正"往外发数据"的东西
        started = time.perf_counter()
# 记下"开始时间"（用来算总耗时）

        yield sse("mode", {
# **第一条事件** —— 把"这次是什么模式、依据了哪几篇新闻"告诉前端
            "mode": result["mode"],
            "sources": sources,
            "top_score": result["top_score"],
        })
        # 第一条先把"这次是什么模式、依据了哪几篇新闻"告诉前端，
        # 前端就能立刻把来源卡片渲染出来，不用等回答完

        answer = ""
# 准备一个空字符串，用来"边收边拼"完整回答
        async for piece, error in stream_ai(messages):
# **消费**上面那个异步生成器，一段段把内容收回来
            if error:
# 判断"这一段是不是错误"
                yield sse("error", {"message": error})
# 把错误当成一个 **error 事件**发给前端
                break
# 出错就结束循环，不再继续读后面的内容
            answer += piece
# 把这一小段拼进完整回答
            yield sse("delta", {"content": piece})
# 把这一小段包装成 **delta 事件**推给前端

        yield sse("done", {
# **最后一条事件** —— 告诉前端"这一轮结束了，并附带统计"
            "answer_length": len(answer),
            "latency_ms": int((time.perf_counter() - started) * 1000),
# 算这一轮总共花了多少毫秒
        })
        # 结束事件带上长度和耗时，前端可以显示"用时 x.x 秒"

    return StreamingResponse(
# 把生成器交给 StreamingResponse，让它"每 yield 一次就发一段"
        event_stream(),
# 第一个位置参数 = 要流式发送的**生成器对象**
        media_type="text/event-stream",
# 设置响应的内容类型
        headers={
# 给响应的响应头里加两项
            "Cache-Control": "no-cache",
# 告诉浏览器和中间的代理"不要缓存这个响应"
            "X-Accel-Buffering": "no",
# 告诉 Nginx 之类的反向代理"不要缓冲，来多少直接转发多少"
        },
    )
    # X-Accel-Buffering: no 很关键 —— 不设的话前面有代理时会被缓冲，
    #   前端就会"攒一大段才一次性显示"，打字机效果就没了

# ------------------------------------------------------------
# 第六部分：接口② —— 知识库统计
# ------------------------------------------------------------

@router.get("/stats", summary="知识库统计")
# 把下面那个函数注册成 "GET /api/rag/stats" 接口
async def rag_stats():
# 定义处理函数（不需要任何参数）
    """看看向量库里现在有多少块新闻"""
    return {
# 直接返回一个字典（FastAPI 会自动把它转成 JSON 响应）
        "code": 200,
        "message": "查询成功",
        "data": {"chunk_count": count()},
# 真正的数据放在 data 里（嵌套一层，方便以后加字段）
    }
