# ============================================================
# 区域：RAG 问答接口（routers 层）
# 作用：提供 2 个接口
#   POST /api/rag/chat   智能问答（SSE 流式）
#   GET  /api/rag/stats  看看向量库里有多少块新闻
#
# 这个接口的核心设计：**两种模式自动切换**
#   rag 模式：检索到相关新闻 → 把新闻当参考资料发给模型，让它照着答并标注来源
#   ai  模式：没检索到相关的   → 当普通 AI 助手回答，不硬塞不相关的新闻
#   这样它平时就是一个正常的 AI 助手，只有问到新闻里的事才"变身"成知识库问答
#
# 关键理解（面试会问）：
#   ① SSE（Server-Sent Events）：服务端持续往客户端推数据，前端就能做出打字机效果。
#      它就是普通 HTTP，比 WebSocket 简单，适合"问一次、答一长段"的场景。
#   ② 流式响应一旦开始发，HTTP 状态码就改不了了！所以"没配 key"这类错误必须在
#      return 之前抛出来；而"生成过程中出错"只能作为一种事件从流里发出去。
#   ③ 事件分了 4 种：mode（模式+来源）→ delta（正文碎片，很多条）→ done（耗时）→ error
#      前端按事件名分别处理，比自己解析模型返回的原始格式清楚得多。
#
# ============================================================
# 【学习版说明书】
# ============================================================
# 本文件是"逐词精讲版"。每行代码下面固定两段：
#   # 做什么：  一句话说清这行在干什么
#   # 逐词：    把这行里每个"词"拆开 —— 它是什么、从哪来、原意是什么
#
# 标签：【内置】不用 import ｜【标准库】自带模块要 import ｜【三方库】pip 装的
#       【本地】项目自己写的 ｜【自起名】只是自己起的名字，不是任何 API
#
# 这个文件是全项目**异步和生成器最密集**的地方，重点看这几样：
#   async def / async with / async for / await 四种"async 家族"
#   yield（生成器）—— 打字机效果的本质
#   装饰器 @router.post(...) —— 一个函数是怎么变成接口的
#   SSE 协议的报文格式（为什么要两个换行）
#
# 规则：跨文件引用写"文件 + 函数名"，不写行号。
# ============================================================

# ------------------------------------------------------------
# 第一部分：导包（3 个标准库 + 2 个 FastAPI + 4 个项目模块）
# ------------------------------------------------------------

import json
# 做什么：把 Python 自带的 JSON 库拿进来
# 逐词：
#   json  【标准库】JavaScript Object Notation（JavaScript 对象表示法）的缩写。
#         它本来是 JS 里的数据格式，现在成了"网络上传数据"的通用格式 ——
#         长得跟 Python 的字典/列表几乎一样：{"key": "值", "list": [1, 2]}
#         不用 pip 装（Python 自带）
# 本文件两个用法：
#   json.dumps(字典)  → dumps = dump string，把 Python 对象转成 JSON **字符串**（发出去用）
#   json.loads(字符串)→ loads = load string，把 JSON 字符串转回 Python 对象（收进来用）
#   ⚠️ 记忆技巧：带 s 的是处理"字符串"，不带 s 的是处理"文件"
# json：把事件数据转成 SSE 里的 data 字段

import time
# 做什么：把 Python 自带的时间库拿进来
# 逐词：
#   time  【标准库】时间
# 本文件只用它一个函数：time.perf_counter() —— 取一个**高精度计时器读数**，
#   做两次减法就是"中间花了多少秒"。为什么要 high precision：
#   time.time() 是壁钟时间（可能被系统对时调整），perf_counter 是单调递增的计时器，
#   专门用来"量一段代码跑了多久"，更准
# time：量一下整轮花了多久

import httpx
# 做什么：把 HTTP 客户端库拿进来
# 逐词：
#   httpx  【三方库】http + x（扩展版），比 requests 更好用、还能异步
# ⚠️ 这个文件用的是它的**异步**用法（和 utils/embedding.py 不一样）：
#     utils/embedding.py：httpx.post(...)            同步，一个请求发完等响应
#     本文件：httpx.AsyncClient(...) + client.stream()  异步 + 流式
#   而且流式读上游返回，才能做出"上游吐一段、我们转一段"的效果
# httpx：异步 HTTP 客户端，用来流式请求 DeepSeek

from fastapi import APIRouter, Depends, HTTPException
# 【2026-09-20 补的 Depends】它是 FastAPI 的"依赖注入"工具：
#   写成 `db: AsyncSession = Depends(get_db)` 之后，每个请求会自动拿到一个数据库会话、
#   请求结束再自动关掉。
#   本项目（RAG）原来不碰数据库，所以一直没用到它 ——
#   现在加了"统计类问题直查数据库"，才需要。
# 做什么：从 FastAPI 里取出两个东西
# 逐词：
#   from / fastapi / import  【三方库】"从 fastapi 包里取出"
#   fastapi  【三方库】pip 装的 Web 框架（把 Python 函数变成 HTTP 接口）。名字 = fast（快）+ API
#   APIRouter  【三方库】拆开：API + Router（路由器）
#        → "接口路由器"：一个能把一组接口打包在一起的东西。
#          好处：一个模块管一组接口，最后在 main.py 里统一挂上去（叫"模块化路由"）
#   HTTPException  【三方库】拆开：HTTP + Exception（异常）
#        → "HTTP 异常"：抛它，FastAPI 会自动把它转成一个 JSON 格式的错误响应（带状态码）。
#          ⚠️ 和普通异常的差别：普通异常会变成 500 加一堆堆栈，
#             HTTPException 是"受控的报错"，返回体干净、状态码也可控
# APIRouter：路由分组；HTTPException：抛 HTTP 错误

from fastapi.responses import StreamingResponse
# 做什么：从 fastapi.responses 里取出"流式响应"类
# 逐词：
#   fastapi.responses  【三方库】responses = "各种响应类型"放的地方
#        （对比：JSONResponse 返回 JSON、FileResponse 返回文件……）
#   StreamingResponse  【三方库】拆开：Streaming（流式的）+ Response（响应）
#        → "流式响应"：它接收一个**生成器**，然后"生成器吐一段、就往客户端发一段"。
#          打字机效果靠的就是它 —— 没有它，得等全部内容生成完才一次性发出去
# StreamingResponse：流式响应（打字机效果靠它）

from config.ai_conf import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEFAULT_MODEL
# 做什么：从 AI 配置文件里取出三个常量
# 逐词：
#   from / config / .ai_conf  【本地】"从 config/ai_conf.py 里"
#   DEEPSEEK_API_KEY   【本地】DeepSeek 的密钥（从 .env 读进来）
#   DEEPSEEK_BASE_URL  【本地】接口根地址（DeepSeek 也是 OpenAI 兼容格式）
#   DEFAULT_MODEL      【本地】默认用哪个模型（比如 deepseek-chat）
#   这三个名字都是**项目自己起的**，值从 .env 读 —— 所以换 key 不用动代码
# DeepSeek 的密钥、地址、默认模型

from config.db_conf import get_db
# 做什么：取出"给每个请求发一个数据库会话"的函数
# 【2026-09-20 新加的】原来本文件完全不碰数据库（RAG 只查向量库），
#   现在加了"统计类问题直查数据库"，所以要能拿到会话

from sqlalchemy.ext.asyncio import AsyncSession
# 做什么：取出异步版的"数据库会话"类，只用它做类型标注
# 【2026-09-20 新加的】给 rag_chat 的 db 参数写类型用（不写也能跑，但编辑器就没提示了）

from crud.rag import build_structured_hits, detect_structured_intent, retrieve
# 做什么：从 RAG 核心逻辑里取出三个函数
# 逐词：
#   crud       【本地】项目里的 crud 包（放"直接操作数据"的函数）
#   .rag       【本地】crud/rag.py
#   retrieve   【本地】原来那个"检索 + 判模式"的函数
#        ⚠️ 它是 async def 定义的，所以下面调用时必须 await
#   detect_structured_intent  【本地】2026-09-20 加：判断这是不是"统计类问题"
#   build_structured_hits     【本地】2026-09-20 加：统计类问题直接查库，拼成一条"资料"
# retrieve：检索 + 判断这次该用哪种模式

from schemas.rag import RagChatRequest
# 做什么：取出"请求体校验模型"
# 逐词：
#   schemas       【本地】项目里放"数据形状定义"的包
#   .rag          【本地】schemas/rag.py
#   RagChatRequest【本地】那里定义的类。
#        ⚠️ 它只在**函数签名**里当类型标注用（rag_chat 的参数），
#           但作用很实在：FastAPI 看到这个标注，就会自动把请求 JSON 解析成这个对象、
#           顺便做完校验（question 非空、top_k 在 1~10 之间），不合法直接 422
# 请求体的数据模型

from utils.vectorstore import count
# 做什么：取出"数向量库有多少块"的函数
# 逐词：
#   utils / .vectorstore / count  【本地】utils/vectorstore.py 里那个不带下划线的 count()
#        ⚠️ 注意它和 chromadb 自带的 .count() 同名但是两回事：
#            这里的 count 是**项目自己定义的函数**（它内部再去调 chromadb 的 .count()）
# count：统计向量库有多少块

router = APIRouter(prefix="/api/rag", tags=["rag"])
# 做什么：造一个"路由分组"，本文件所有接口都会自动带上 /api/rag 前缀
# 逐词：
#   router  【自起名】路由器实例的名字（存一下，下面用 @router.post 往上挂接口）
#   =       【内置】赋值
#   APIRouter(...)  【三方库】上面 import 的类，加括号 = 造一个实例
#   prefix="/api/rag"  【三方库】参数名。prefix = "前缀"
#        → 本文件里写的 "/chat" 实际路径是 "/api/rag/chat"
#        ⚠️ 好处：前缀变了只改这一处
#   tags=["rag"]       【三方库】参数名。tag = "标签"
#        → 只在自动生成的接口文档（/docs 页面）里当分组标题用，不影响功能
#        值是**列表**（所以带方括号），因为一个接口可以有多个标签
# ⚠️ 这个 router 对象最后必须在 main.py 里被 app.include_router(ragchat.router) 挂上去，
#    否则这些接口根本不会生效 —— 这是新手常见的"代码写了但访问 404"的原因
# 本模块的接口都以 /api/rag 开头

# ------------------------------------------------------------
# 第二部分：小工具① —— 拼 SSE 报文
# ------------------------------------------------------------


def sse(event: str, data: dict) -> str:
    # 做什么：定义函数，把"事件名 + 数据"拼成 SSE 协议要求的字符串
    # 逐词：
    #   def       【内置】定义函数
    #   sse       【自起名】Server-Sent Events 的缩写 → "服务端推送事件"。
    #             这是**协议名**，不是库的方法名；用它当函数名是因为这个函数的职责就是"拼 SSE 报文"
    #   event     【自起名】参数：事件名（比如 "delta"）。
    #             : str 【内置】类型标注：该传字符串
    #   data      【自起名】参数：要发给前端的数据。
    #             : dict 【内置】类型标注：该传字典（因为下面要用 json.dumps 转它）
    #   -> str    【内置】返回类型标注：返回一个字符串
    """把一条消息拼成 SSE 格式：事件名 + 数据 + 空行"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    # 做什么：拼出 SSE 报文并返回
    # 逐词（拆成四段看）：
    #   ① "event: {event}"        → 事件名那一行。
    #        ⚠️ "event: " 后面必须有一个空格，这是 SSE 协议规定的格式
    #   ② "\ndata: {json.dumps(...)}"
    #        → 换行后写数据行。"data: " 同样必须带一个空格
    #   ③ json.dumps(字典, ensure_ascii=False)
    #        → 【标准库】把字典转成 JSON 字符串。
    #          ensure_ascii=False 是关键：不加它，中文会被转成 \u4f60\u597d 这种转义，
    #          前端收到就看不懂了（虽然程序能还原，但日志里全是乱码）
    #   ④ "\n\n" 结尾
    #        → 【SSE 协议规定】必须用"一个空行"来表示"这条消息到此结束"。
    #          因为 \n 是"换行"，所以"两个 \n" = 一个空行。
    #          ⚠️ 少写一个 \n，前端会一直等"消息结束"，看起来就是"没反应"
    # 一条完整的报文长这样（注意最后那个空行）：
    #     event: delta
    #     data: {"content": "你"}
    #     （空行）
    # ensure_ascii=False：中文不转义，前端直接拿到能看的中文
    # 结尾必须是两个换行 —— SSE 协议规定"空行"才代表一条消息结束

# ------------------------------------------------------------
# 第三部分：小工具② —— 拼发给模型的 messages
# ------------------------------------------------------------


def build_messages(question: str, hits: list, history: list) -> list:
    # 做什么：定义函数，把"系统提示词 + 历史对话 + 本次问题"拼成模型要的格式
    # 逐词：
    #   build_messages  【自起名】build（拼装）+ messages（消息，复数）
    #   question   【自起名】本次用户问题
    #   hits       【自起名】检索到的新闻块（可能为空列表）
    #   history    【自起名】之前的对话（可能是 None）
    #   -> list    【内置】返回一个"消息列表"，交给下面的 stream_ai 发给模型
    """
    拼发给模型的 messages
    hits 不为空 → 把新闻资料写进 system 提示词，让模型照着答
    hits 为空   → 只给一个普通的系统提示词
    """
    if hits:
        # 做什么：判断"这次检索到资料了吗"
        # 逐词：
        #   if hits  【内置】非空列表在条件里算"真值" → 这句等于"如果 hits 里有东西"
        #   为什么这么写而不是 if len(hits) > 0：Python 惯例是"直接用对象本身当条件"，
        #   空列表/空字符串/0/None 都是假，非空容器都是真
        blocks = []
        # 做什么：准备一个空列表，装"每一段资料"的文本
        # 逐词：
        #   blocks  【自起名】block（块、段落）+ s → 几段资料
        #   []      【内置】空列表
        for i, hit in enumerate(hits, start=1):
            # 做什么：遍历每一条检索结果，同时拿到"序号"
            # 逐词：
            #   for ... in ...     【内置】遍历
            #   enumerate(hits, start=1)  【内置】"枚举"：把列表变成"（序号, 元素）"一对一对地给。
            #       ⚠️ start=1 是关键：默认从 0 开始，这里指定从 1 开始。
            #          为什么：下面要在提示词里写"资料1、资料2"，
            #          给人（和给模型）看都是从一开始数更自然
            #   i     【自起名】序号（1, 2, 3 …）
            #   hit   【自起名】那一条检索结果。
            #         ⚠️ 它是个字典，形如 {"text": 块内容, "meta": 档案袋, "score": 相似度}
            #         这个名字来自 utils/vectorstore.py 的 search() 返回的结构
            blocks.append(f"[资料{i}]《{hit['meta']['title']}》\n{hit['text']}")
            # 做什么：拼一段资料的文本，加进 blocks
            # 逐词：
            #   .append(...)  【内置】列表方法：往末尾追加一个元素
            #   f"[资料{i}]《...》\n{...}"  【内置 f-string】
            #       [资料{i}]  → 方括号和"资料"两个字都是**普通文字**（装饰用），
            #                    只有 {i} 是变量占位符
            #       《》       → 中文书名号，纯装饰
            #   hit['meta']['title']  【内置】"字典套字典"的取值：
            #       先从 hit 里取 meta（也就是那个档案袋），再从档案袋里取 title。
            #       档案袋是 crud/rag.py 在入库时写进去的（news_id/title/category/content_hash）
            #   \n        【内置】换行符，让标题和正文分行
            #   hit['text']  【内置】那一块的正文
            #   ⚠️ 写法细节：外层用双引号、内层用单引号 ——
            #      因为最外面已经被 f"..." 的双引号占用了，里面再用双引号会起冲突
            # 给每块资料编号，模型才能写"根据资料1"这种可追溯的引用

        references = "\n\n".join(blocks)
        # 做什么：把所有资料段用"空行"串成一大段文字
        # 逐词：
        #   references  【自起名】reference（参考资料）+ s
        #   "\n\n"      【内置】分隔符（两个换行 = 空行）
        #   .join(列表) 【内置】字符串方法，顺序**别搞反**：是"分隔符.join(列表)"。
        #        → 用这个分隔符把列表里的每一项串起来。
        #          例：",".join(["a","b"]) → "a,b"
        #        写反成 blocks.join("\n\n") 会直接报错
        # 为什么用空行分隔：让"一段资料"和"下一段资料"在视觉上分开，模型更好区分
        system = (
            "你是头条新闻助手。请优先依据下面的新闻资料回答用户问题，"
            "并用「根据资料1」这样的方式标注你的依据。"
            "如果这些资料和问题无关，就直接用你自己的知识回答，不要硬扯资料。\n\n"
            f"【新闻资料】\n{references}"
        )
        # 做什么：拼出系统提示词（system prompt）
        # 逐词：
        #   system  【自起名】system = "系统"，指 system 角色的提示词（给模型定规矩的那段）
        #   ( ... ) 【内置】最外层这对括号是"隐式字符串拼接"的载体：
        #       相邻的多个字符串字面量 Python 会自动拼成一个，所以这五行其实是一整段话。
        #       ⚠️ 注意最后一段是 f"..."（带 f 的），只有它里面的 {references}
        #          才会被替换成变量值；前面几段没有 f，所以里面的字符都是原样
        #   内容分四句，每句都有用途：
        #     ① 定角色："你是头条新闻助手"
        #     ② 定格式："优先依据下面资料回答" + "标注依据" → 让答案可追溯
        #     ③ **最关键**："资料和问题无关就用你自己知识回答，不要硬扯资料"
        #        → 这是判错时的兜底：万一阈值把不相关的资料带进来了，模型也不会答得很怪。
        #          实测：把"你好"误判成 rag 模式时，模型照样正常回了问候语
        #     ④ 贴资料："【新闻资料】\n" + references
        # 最后一句很重要：把"资料没用时就正常回答"写进提示词，
        # 这样即使阈值判断错了（把不相关的资料带上来了），模型也不会答得很怪
    else:
        # 做什么：没检索到资料时，给一个最简提示词
        # 逐词：
        #   else          【内置】"否则"（和上面的 if hits: 配对）
        #   注意这个分支里**完全不带任何新闻**，避免模型编造来源
        system = "你是头条新闻助手，你内接的模型是deepseek大模型，请简洁、准确地回答用户的问题。"
        # 没有资料时给一个最简提示词（不带任何新闻，避免模型乱编来源）

    messages = [{"role": "system", "content": system}]
    # 做什么：把系统提示词装成第一条消息
    # 逐词：
    #   messages  【自起名】发给模型的消息列表
    #   [ ... ]   【内置】方括号 = 列表；里面放的是一个字典
    #   {"role": "system", "content": system}
    #        → 【OpenAI/DeepSeek 规定的消息格式】每条消息必须有两个字段：
    #            role    = 角色，只能是 system / user / assistant 三种
    #                      system 表示"给模型的规矩"、user 是用户说的、assistant 是模型说的
    #            content = 内容
    #        ⚠️ 这些键名（"role"/"content"）是模型接口定的，不能改
    # 第一条永远是 system（定规矩 + 给资料）

    if history:
        messages += [msg.model_dump() for msg in history]
        # 做什么：把前端传来的历史对话，一条条追加进消息列表
        # 逐词：
        #   messages += [...]  【内置】对列表来说 += 是"把右边的元素逐个并进来"
        #        （等价于 messages.extend([...])，不是字符串那种拼接）
        #   [msg.model_dump() for msg in history]  【内置 + 三方库】列表推导式：
        #        遍历 history 里的每个 msg，调用它的 model_dump()，收集成新列表
        #   msg        【自起名】history 里的一条元素（一个 ChatMessage 对象）
        #   .model_dump()  【三方库】Pydantic v2 的方法：把"模型对象"转成"普通字典"。
        #        ⚠️ 为什么要转：history 里的元素是 Pydantic 对象，
        #           而 messages 里其他元素都是普通字典 —— 混着发过去模型接口不认。
        #           必须统一成字典格式。
        #        ⚠️ 版本差异：Pydantic v1 里这个方法叫 .dict()，看到 .dict() 是同一件事
        #   history 本身可能是 None —— 但上面的 if history 挡住了（None 是假值），
        #     所以进到这里时它一定是非空列表
    # 中间夹上多轮历史（前端传上来的），注意顺序：system → 历史 → 本次问题
    #   顺序错了模型会理解错，比如把历史当成本次问题回答

    messages.append({"role": "user", "content": question})
    # 做什么：把本次问题作为最后一条消息加进去
    # 逐词：
    #   .append(...)  【内置】往列表末尾追加一个元素（一次加一个）
    #   {"role": "user", "content": question}
    #        → 一个字典，role 固定写 "user"（表示"这是用户说的话"），content 是问题原文
    #   ⚠️ 为什么用 append 而上面用 +=：这里加的是**一个**字典 → append；
    #      上面要并入**一批** → +=（extend）。选错了会变成嵌套列表
    return messages
    # 做什么：返回拼好的消息列表
    # 最终形状（多轮对话时）：
    #   [ {"role":"system", ...},
    #     {"role":"user", ...}, {"role":"assistant", ...},   ← 历史（成对出现）
    #     {"role":"user", "content": 本次问题} ]              ← 最后一定是本次问题

# ------------------------------------------------------------
# 第四部分：小工具③ —— 请求 DeepSeek 的流式接口
# ------------------------------------------------------------


async def stream_ai(messages: list):
    # 做什么：定义**异步生成器**函数：向上游请求流式回答，一段段 yield 出来
    # 逐词：
    #   async def  【内置】这个函数里有 yield，所以它是"异步生成器函数"。
    #        ⚠️ 三个关键性质：
    #          ① 调用它不会执行函数体，只得到一个"异步生成器对象"
    #          ② 必须用 async for 去消费（普通 for 会报
    #             TypeError: 'async_generator' object is not iterable）
    #          ③ 函数体每执行到 yield 就"暂停"，把值交出去，等下次被要的时候再继续
    #   stream_ai  【自起名】stream（流）+ ai → "流式请求 AI"。
    #        ⚠️ 注意它和上面 build_messages 的区别：
    #            build_messages 是普通函数（返回一个值）
    #            stream_ai 有 yield，所以是生成器（会"产出很多次"）
    #   messages   【自起名】参数：拼好的消息列表
    #   ⚠️ 注意函数签名**没有 -> 返回类型**：生成器"产出"的东西不走 return，
    #      写返回类型标注反而会误导（所以这里故意留空）
    """
    请求 DeepSeek 的流式接口，一段段把正文吐出来
    产出：(正文片段, 错误信息)；正常时错误信息是 None
    """
    async with httpx.AsyncClient(timeout=120) as client:
        # 做什么：创建一个异步 HTTP 客户端，用完自动关闭
        # 逐词：
        #   async with ... as ...  【标准库语法】"异步上下文管理器"：
        #        进入时准备（这里是建客户端/连接池），离开时自动收尾（关闭它）。
        #        ⚠️ 前面加 async 是因为"准备和收尾"都是异步动作
        #   httpx.AsyncClient(...)  【三方库】httpx 的**异步**客户端类。
        #        ⚠️ 对比同步写法 httpx.post(...)：那种是一次性的；
        #           用 Client 的好处是可以复用连接、还能做流式读
        #   timeout=120  【三方库】参数名：总超时 120 秒。
        #        ⚠️ 为什么给到 120 秒这么久：模型生成一长段回答本身可能要几十秒，
        #           给太短会在"正常但慢"的时候误报超时
        #   client  【自起名】这个客户端实例，下面用它发请求
        async with client.stream(
            # 做什么：发起一个**流式**请求（不等整个响应下载完，边下边处理）
            # 逐词：
            #   client        【自起名】上面那个异步客户端
            #   .stream(...)  【三方库】"流式请求"方法。
            #        ⚠️ 为什么不用 .post()：.post() 会等对方把整个响应发完才返回，
            #           那就没有"打字机效果"了 —— 只能等模型全答完再一次性给前端。
            #           用 .stream() 才能"上游吐一段、我们马上转一段"
            #   它也返回一个"异步上下文管理器"，所以外面又套了一层 async with
            "POST",
            # 做什么：第一个位置参数 = HTTP 方法
            # 逐词：
            #   "POST"  【字符串】注意带引号。
            #        ⚠️ 是 http 的"提交"方法（要发正文 data 给对方，就用 POST）
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            # 做什么：第二个参数 = 要请求的完整 URL
            # 逐词：
            #   f"..."                【内置】f-string 拼字符串
            #   {DEEPSEEK_BASE_URL}   【本地】比如 https://api.deepseek.com/v1
            #   /chat/completions     【服务商规定的路径】拆开：
            #        chat（聊天）+ completions（补全）→ "聊天补全"接口。
            #        这是 OpenAI 的规定路径，DeepSeek 兼容它，所以长得一样
            headers={
                # 做什么：设置请求头
                # 逐词：headers= 【三方库】参数名；{ } 【内置】字典（HTTP 头就是键值对）
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                # 逐词：
                #   "Authorization"  【HTTP 标准字段名】授权。
                #   f"Bearer {key}"  【HTTP 标准格式】Bearer = 持有者，
                #        后面必须跟一个空格再跟密钥（漏了空格会 401）
                #   {DEEPSEEK_API_KEY}  【本地】从 config/ai_conf.py 读来的密钥
                "Content-Type": "application/json",
                # 逐词：
                #   "Content-Type"      【HTTP 标准字段名】
                #   "application/json"  【HTTP 标准取值】告诉对方"我发的正文是 JSON"
            },
            json={"model": DEFAULT_MODEL, "messages": messages, "stream": True},
            # 做什么：设置请求正文
            # 逐词：
            #   json=  【三方库】便利参数：给字典，它自动转成 JSON 字符串并设好 Content-Type
            #   里面的三个字段：
            #     "model"    : DEFAULT_MODEL  → 【本地】用哪个模型
            #     "messages" : messages       → 【自起名】上面 build_messages 拼好的消息列表
            #     "stream"   : True           → 【关键！】告诉上游"请一段段返回，别等全生成完"。
            #          ⚠️ 少了这个 True，上游会一次性返回完整答案 ——
            #             那我们这边的流式就断在第一环了（前端还是"一次性蹦出来"）
        ) as resp:
            # 做什么：这次的 `as resp` 拿到的是"响应对象"
            # 逐词：
            #   as resp  【自起名】resp = response（响应）。
            #        ⚠️ 它和上面那个 client 不是一回事：client 是"客户端"（用来发请求），
            #           resp 是"这一次请求的响应"（包含状态码、正文等）
            if resp.status_code != 200:
                # 做什么：检查上游返回的状态码
                # 逐词：
                #   resp.status_code  【三方库】HTTP 状态码属性（200 = 成功）
                #   !=                【内置】"不等于"
                #   if                【内置】"如果"
                yield None, f"AI 接口错误({resp.status_code})，请稍后再试"
                # 做什么：**产出一对值**：(None, 错误信息)
                # 逐词：
                #   yield  【内置】"产出" —— 生成器专用关键字。
                #        ⚠️ 它和 return 的区别：
                #            return = 交出一个值并**结束**函数
                #            yield  = 交出一个值并**暂停**，等下次被要时从这儿继续
                #          这里 yield 出的是一个**元组**：(None, "错误信息…")
                #   None   【内置】"空的" —— 放在第一位表示"这次没有正文片段"
                #   f"..." 【内置】f-string 拼错误信息
                # ⚠️ 为什么用"产出 (片段, 错误) 这种成对的值"而不是抛异常：
                #    因为这个函数是在"流已经开始发"之后被消费的，
                #    抛异常会打断整个流；改成"产出一条错误"，
                #    调用方就能把它当成一个正常的 error 事件发出去（见下面 event_stream）
                # 调用方（下面那个 async for）就是靠"哪一个是 None"来判断正常/异常的
                return
                # 做什么：结束这个生成器，后面不再产出任何东西
                # 逐词：
                #   return  【内置】在生成器里，return 表示"到此为止，不再 yield 了"。
                #        ⚠️ 注意生成器里的 return 不能带返回值
                #        （要"产出值"只能用 yield）
            # 上游报错就把错误交给调用方，由它作为 error 事件发给前端

            async for line in resp.aiter_lines():
                # 做什么：一行一行地读上游返回的内容
                # 逐词：
                #   async for ... in ...  【内置】"异步遍历"。
                #        ⚠️ **必须**用 async for，不能用普通 for ——
                #           因为"等下一行到来"这件事本身需要 await，
                #           普通 for 会报 TypeError: 'async_generator' object is not iterable
                #   resp.aiter_lines()  【三方库】httpx 提供的**异步迭代器**：
                #        aiter = async iterator（异步迭代器）+ lines → "一次给一行"。
                #        对比 .iter_lines()：那是同步版。这里因为是流式响应，必须用异步版
                #   line  【自起名】这一行的内容（字符串）
                if not line.startswith("data: "):
                    # 做什么：过滤掉"不是数据行"的内容
                    # 逐词：
                    #   not               【内置】"取反"
                    #   line.startswith("data: ")  【内置】字符串方法：
                    #        start（开始）+ s + with（用）→ "这一行是不是以 'data: ' 开头"。
                    #        返回 True/False
                    #   ⚠️ "data: " 结尾那个**空格**不能少 —— 上游就是这个格式
                    #   为什么会有别的行：SSE 流里还有空行、注释行等；
                    #      我们只处理数据行，其他一律跳过
                    continue
                    # 做什么：跳过这一行，直接读下一行
                    # 逐词：
                    #   continue  【内置】"继续" —— 结束**这一次**循环，开始下一次。
                    #        ⚠️ 和 break 的区别：break 是整个循环不干了；
                    #           continue 只是"这一轮到此为止"
                # 上游返回里还有空行等其他内容，只挑 "data: " 开头的

                data = line[6:].strip()
                # 做什么：把行首的 "data: " 前缀去掉，再去掉首尾空白
                # 逐词：
                #   data        【自起名】处理后的内容（是个 JSON 字符串）
                #   line[6:]    【内置】这是**切片**：从第 6 个字符取到末尾。
                #        ⚠️ 为什么是 6："data: " 正好 6 个字符（d-a-t-a-冒号-空格）。
                #           所以从下标 6 开始切，正好把前缀切掉。
                #           ⚠️ 下标从 0 开始：line[0] 是 'd'，line[5] 是那个空格
                #   .strip()    【内置】去掉首尾空白（含行尾的换行符 \n）
                #        ⚠️ 两个操作可以连着写：line[6:] 的结果还是字符串，
                #           所以能接着调 .strip()
                # 去掉 "data: " 前缀（6 个字符）

                if data == "[DONE]":
                    # 做什么：判断"上游是不是发完了"
                    # 逐词：
                    #   if              【内置】"如果"
                    #   data            【自起名】上面处理后的内容
                    #   ==              【内置】判断相等（**两个等号**，一个等号是赋值）
                    #   "[DONE]"        【上游约定】一个固定字符串，注意**带引号**。
                    #        DONE = "完成"。这是 OpenAI 协议规定的"流结束"标记
                    break
                    # 做什么：结束这个 async for 循环，不再读后面的内容
                    # 逐词：
                    #   break  【内置】"中断"整个循环。
                    #        ⚠️ 这里用 break 而不是 continue：继续读下去没有意义了
                # [DONE] 是 OpenAI 协议约定的"流结束"标记

                try:
                    # 做什么：尝试解析这一行的 JSON
                    # 逐词：
                    #   try  【内置】"尝试" —— 把可能出错的代码放这里，
                    #        一旦出错就跳到下面的 except，而不是让整个程序崩掉
                    obj = json.loads(data)
                    # 做什么：把 JSON 字符串解析成 Python 字典
                    # 逐词：
                    #   obj          【自起名】object（对象）的缩写 → 解析出来的字典
                    #   json.loads   【标准库】loads = load string → "从字符串加载"。
                    #        ⚠️ 带 s 是处理字符串（json.load 是处理文件对象）
                    #   (data)       【自起名】上面处理过的 JSON 字符串
                    #   解析出来大概长这样：
                    #     {"choices": [{"delta": {"content": "你"}}]}
                    #     → choices（候选答案们）→ 取第 0 个 → delta（增量）→ content（正文片段）
                except json.JSONDecodeError:
                    # 做什么：只捕获"JSON 解析失败"这一种异常，然后跳过这一行
                    # 逐词：
                    #   except                 【内置】"除了…情况" —— 捕获异常的入口
                    #   json.JSONDecodeError   【标准库】**具体的**异常类型：
                    #        JSON（格式名）+ Decode（解码）+ Error（错误）
                    #        → "JSON 解码失败"。
                    #        ⚠️ 为什么不写 except Exception：那样会把所有错都吞掉，
                    #           包括"代码写错了"这种真 bug，排查时会很难受。
                    #           只捕自己预期的那一种，是更专业的做法
                    continue
                # 偶尔有切分不完整的行，跳过就行
                # （流式传输是一块块到达的，理论上偶尔会有边界情况）

                piece = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                # 做什么：从这一帧里取出"正文片段"，层层兜底防止报错
                # 逐词（从外往内拆，这行是整个文件最"绕"的一行）：
                #   piece  【自起名】"一小片" → 正文片段（可能是一个字、也可能几个字）
                #   obj.get("choices")   【内置】字典的 .get(键)：
                #        有 choices 就返回它的值，没有就返回 None（**不会报错**）。
                #        对比 obj["choices"]：没这个键会直接 KeyError 崩掉
                #   (...) or [{}]        【内置】or 兜底：
                #        如果 choices 是 None 或空列表（都是假值），就换成 [{}]。
                #        ⚠️ 为什么要这一步：下面要取 [0]（第一个候选），
                #           如果 choices 是空的，取 [0] 会 IndexError 崩掉
                #   [0]                  【内置】取第 0 个（第一个候选答案）。
                #        ⚠️ 为什么接口返回的是"一个列表"：因为可以一次要多个候选答案，
                #           我们只要一个，所以固定取第 0 个
                #   .get("delta", {})    【内置】再取 delta 字段（增量内容），
                #        没有就给空字典 {} → 这样下面能安全地接着调 .get
                #   .get("content")      【内置】最后取 content，没有就返回 None
                # ⚠️ 为什么会"没有 content"：流式返回的开头几帧、结尾几帧，
                #    只有角色信息没有正文，所以 content 可能是 None。
                #    这行代码的设计目标就是"结构不完整也不崩，只是拿不到内容"
                if piece:
                    # 做什么：判断"这一帧到底有没有正文"
                    # 逐词：
                    #   if piece  【内置】None 是假值、空字符串也是假值
                    #        → 所以这句等于"拿到真的内容了才处理"
                    yield piece, None
                    # 做什么：把这一小段正文"产出"给调用方
                    # 逐词：
                    #   yield     【内置】产出并暂停 —— 函数在这里"挂起"，
                    #        等调用方把这段发完、再来要下一段时，才从这里继续往下读
                    #   piece     【自起名】这一小段正文
                    #   None      【内置】第二位放 None，表示"这次没有错误"
                    #        （对比上面出错时 yield None, "错误信息" ——
                    #          第一位放 None 表示"这次没有正文"）
                    #   这就是"打字机效果"的**最源头**：一收到碎片就立刻交出去，
                    #   而不是攒成完整回答再返回
                    # 一收到碎片就吐出去 —— 这就是打字机效果的来源

# ------------------------------------------------------------
# 第五部分：接口① —— 智能问答（流式）
# ------------------------------------------------------------


@router.post("/chat", summary="智能问答（流式）")
# 做什么：**装饰器** —— 把下面那个函数注册成 "POST /api/rag/chat" 接口
# 逐词：
#   @              【内置】"装饰器"的语法符号。
#        装饰器 = "放在函数前面、用 @ 开头"的东西，作用是给函数**附加功能**。
#        这里的附加功能就是"把这个函数注册成一条路由"
#   router         【自起名】上面造的那个路由分组
#   .post(...)     【三方库】"注册为 POST 接口"的方法。
#        （同族还有 .get / .put / .delete，分别对应四种 HTTP 方法）
#   "/chat"        【路径】注意这里**不写 /api/rag** ——
#        因为 router 创建时已经设了 prefix="/api/rag"，会自动拼上。
#        最终路径：/api/rag/chat
#   summary="..."  【三方库】参数：写进自动接口文档的一句话说明
#   执行时机：这个装饰器在**模块被导入时**就跑完了（不是请求时才注册），
#     所以在 main.py import 到它的时候，路由就已经挂好了
async def rag_chat(
    data: RagChatRequest,
    db: AsyncSession = Depends(get_db),
):
    # 做什么：定义接口的处理函数（收到请求时执行它）
    # 逐词：
    #   async def  【内置】异步函数。FastAPI 支持 async def，
    #        好处是"等数据库/等网络"的时候能去处理别的请求
    #   rag_chat   【自起名】函数名（内部叫法），会自动变成接口文档里的名字
    #   data: RagChatRequest
    #        → 【本地 + 三方库】这一步很神奇：函数的参数类型写成 Pydantic 模型，
    #          FastAPI 就会**自动**做到：
    #            ① 从请求体里读 JSON  ② 按 RagChatRequest 的规则校验
    #            ③ 把结果变成一个 RagChatRequest 对象传进来（所以下面用 data.question）
    #          ⚠️ 校验不过会直接返回 422，根本进不到函数体 —— 这就是"校验前置"
    #   data  【自起名】这个对象身上有 question / history / top_k 三个字段
    #   db: AsyncSession = Depends(get_db)
    #        → 【三方库 + 本地】这就是"依赖注入"：
    #          FastAPI 看到这个写法，会在每个请求进来时自己去调 get_db()，
    #          把拿到的数据库会话塞进 db，请求结束再自动关掉。
    #          ⚠️ 它不来自请求体 —— 用户既不需要、也没法伪造这个参数
    #          【2026-09-20 加】只有"统计类问题直查数据库"那一小段会用到它
    """
    智能问答

    两条路（2026-09-20 起）：
      ① 统计类问题（"一共有多少篇""最长的是哪一篇"）→ 直查数据库，把数字当资料
         —— 因为这类问题向量检索答不了：它只会"找相似"，不会 COUNT / ORDER BY
      ② 其余问题 → 走原来的向量检索：检索到相关的就带着资料回答，没有就按普通 AI 回答
    """
    if not DEEPSEEK_API_KEY:
        # 做什么：检查"密钥配了没有"
        # 逐词：
        #   not                【内置】取反。空字符串是假值，所以 not 之后为真 → 进分支
        #   DEEPSEEK_API_KEY   【本地】从 config/ai_conf.py 读来的密钥
        #   ⚠️ 为什么必须在**这**一步检查（而不是等下面调接口时才发现）：
        #      因为下面的 return StreamingResponse 一开始发数据，
        #      HTTP 响应头和状态码就"已经发出去了、改不了了"。
        #      所以配置类错误必须赶在"开始发流"之前抛出来
        raise HTTPException(status_code=500, detail="后端未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置")
        # 做什么：抛一个 HTTP 异常（FastAPI 会自动转成 JSON 错误响应）
        # 逐词：
        #   raise        【内置】"抛出" —— 主动制造异常，程序在这里中断
        #   HTTPException  【三方库】上面 import 的类。加括号 = 造一个异常实例
        #   status_code=500  【三方库】参数：用哪个 HTTP 状态码。
        #        500 = 服务器内部错误（这不是用户的错，是我们自己没配好）
        #   detail="..."     【三方库】参数：错误详情。
        #        会变成响应体里的 {"detail": "..."}
        #   信息写得很具体（说清哪个变量、去哪个文件配），因为这是给运维/自己看的，
        #   越具体越好排查
    # 没配 key 必须在"开始发流"之前报错（流一旦开始就改不了状态码了）

    # ★ 第一步：先判断这是不是"向量检索答不了"的统计类问题（2026-09-20 新增）
    #
    # 【为什么要有这一步】
    #   实测用户问"你一共有多少篇新闻"，页面标签是"通用回答" ——
    #   向量检索没命中，直接降级成普通 AI 聊天，只能答"我没有固定的新闻篇数哦"。
    #   这类问题的解法不是去调阈值，而是绕开向量检索、直接查数据库。
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
    # 做什么：调用检索函数，拿到"资料 + 最高分 + 模式"
    # 逐词：
    #   result  【自起名】检索结果（一个字典）
    #   await              【内置】等它跑完（retrieve 是 async def 定义的）
    #   retrieve           【本地】crud/rag.py 的函数
    #   data.question      【本地】从请求对象上取用户问题。
    #        ⚠️ 注意是 data.xxx（属性）而不是 data["xxx"]（字典取值）——
    #           因为 data 是个 Pydantic **对象**，不是字典
    #   data.top_k         【本地】前端可选传的"检索几条"（没传就是 None，retrieve 里会兜底成 4）
    # 返回 {"hits":[...], "top_score": 0.55, "mode":"rag" 或 "ai"}
    # 检索 + 判断模式

    hits = result["hits"] if result["mode"] == "rag" else []
    # 做什么：只有 rag 模式才把资料留着，ai 模式一律清空
    # 逐词：
    #   hits        【自起名】要带进提示词的资料列表
    #   result["hits"]  【内置】字典取值，取出检索到的资料
    #   A if 条件 else B  【内置】三元表达式：条件成立取 A，否则取 B
    #   result["mode"] == "rag"  【内置】判断这次是不是 rag 模式
    #   else []     【内置】不成立就取空列表
    # ⚠️ 为什么 ai 模式要主动清空 hits（而不是直接不管）：
    #    因为 retrieve 在 ai 模式下可能仍然返回了"分数不够高但确实检索到的东西"。
    #    那些不相关的新闻塞进提示词，只会干扰模型回答 ——
    #    所以这里明确置空，保证 ai 模式"干干净净当普通助手"
    # 只有 rag 模式才把资料带上（ai 模式不带，避免不相关的新闻干扰回答）

    sources = [
        # 做什么：把 hits 转成"给前端展示的来源列表"（列表推导式 + 字典）
        # 逐词：
        #   sources  【自起名】"来源"（复数）→ 来源卡片的数据
        #   [ ... ]  【内置】方括号 = 列表（这是一段"列表推导式"，见下面最后一行）
        {
            # 【内置】花括号 = 字典，代表"一条来源"
            "title": hit["meta"].get("title", ""),
            # 逐词：
            #   "title"        【自起名】键名（前端要按这个名字读）
            #   hit["meta"]    【内置】先从命中结果里取档案袋
            #   .get("title", "")  【内置】从档案袋里取 title；
            #        取不到就返回空字符串 ""（**不会报错**）。
            #        ⚠️ 为什么给默认值：老数据可能没这个字段，
            #           用 [] 会 KeyError；而且这里给的是 ""（不是 None），
            #           因为前端要直接渲染，空字符串比 null 安全
            "category": hit["meta"].get("category", ""),
            # 逐词：同上，取分类名；取不到给 ""
            "score": hit["score"],
            # 逐词：
            #   "score"       【自起名】键名 → 相似度分数
            #   hit["score"]  【内置】取出相似度。
            #        这个分数是 utils/vectorstore.py 里用 1-距离 算出来的
        }
        for hit in hits
        # 【内置】列表推导式的"循环部分"：把 hits 里每个元素叫 hit，
        #   上面那个字典就是"每个 hit 加工后的结果"。
        #   完整读法："对于 hits 里的每个 hit，生成一个含 title/category/score 的字典"
    ]
    # 整理成给前端展示的来源列表
    # ⚠️ 注意这里**只挑了三样**（标题/分类/分数），没有把整块正文发过去 ——
    #    因为前端只需要显示"答案出自哪几篇"，正文在模型回答里已经体现了。
    #    少发数据 = 响应更小 = 更快

    messages = build_messages(data.question, hits, data.history)
    # 做什么：拼出发给模型的消息列表
    # 逐词：
    #   messages     【自起名】消息列表
    #   build_messages  【本地】本文件上面定义的函数
    #   data.question  【本地】用户问题
    #   hits           【自起名】上面处理过的资料（ai 模式时是空列表）
    #   data.history   【本地】前端传上来的历史对话（可能是 None）
    # ⚠️ 时机很重要：这一步必须在"开始发流"**之前**做完。
    #    因为流一旦开始，就不能再执行"需要 await 的耗时操作"了
    #    （那会让前端等很久才有第一个字节，还容易超时）
    # 拼提示词

    async def event_stream():
        # 做什么：在函数内部再定义一个**异步生成器**，它才是真正"往外发数据"的东西
        # 逐词：
        #   async def  【内置】异步函数。因为有 yield，所以是异步生成器
        #   event_stream  【自起名】event（事件）+ stream（流）→ "事件流"。
        #        它的职责：按顺序 yield 出四种 SSE 报文
        #   ⚠️ 为什么要定义在函数**内部**（而不是模块顶层）：
        #       因为它需要用到外层的 hits / sources / result / messages 这些变量。
        #       定义在里面就自动"看得见"它们（这叫"闭包"）；
        #       定义在外面就得靠参数一个个传，很啰嗦
        #   ⚠️ 注意这个函数**没有参数、也没有被 await 调用**：
        #       它作为生成器对象被传给 StreamingResponse，
        #       由 StreamingResponse 去迭代它（每 yield 一次就发一段）
        started = time.perf_counter()
        # 做什么：记下"开始时间"（用来算总耗时）
        # 逐词：
        #   started  【自起名】"开始时刻"
        #   time.perf_counter()  【标准库】取一个高精度计时读数（单位：秒）。
        #        ⚠️ 这个数本身没有意义（不是"几点几分"），
        #           它只是一个"单调递增的秒表读数" ——
        #           拿两次相减，差值就是"过了多少秒"
        # 事件流：按 模式 → 正文碎片 → 结束 的顺序往外发

        yield sse("mode", {
            # 做什么：**第一条事件** —— 把"这次是什么模式、依据了哪几篇新闻"告诉前端
            # 逐词：
            #   yield     【内置】产出一条（生成器关键字）
            #   sse(...)  【本地】本文件上面定义的函数：把参数拼成 SSE 报文字符串
            #   "mode"    【自起名事件名】注意带引号（是字符串）。四种事件之一
            #   { ... }   【内置】字典 → 会被 sse 里的 json.dumps 转成 JSON
            "mode": result["mode"],
            # 逐词：
            #   "mode"          【自起名】键名（前端按它读）
            #   result["mode"]  【内置】上面检索算出来的模式："rag" 或 "ai"
            "sources": sources,
            # 逐词：
            #   "sources"  【自起名】键名 → 来源列表
            #   sources    【自起名】上面那个列表推导式算出来的东西
            #   前端拿到它就能**立刻**把来源卡片渲染出来，不用等回答完
            "top_score": result["top_score"],
            # 逐词：
            #   "top_score"         【自起名】键名 → 最高相似度
            #   result["top_score"] 【内置】比如 0.7255
            #   带着它的用处：调试时一眼看出"离阈值 0.55 差多少"
        })
        # 第一条先把"这次是什么模式、依据了哪几篇新闻"告诉前端，
        # 前端就能立刻把来源卡片渲染出来，不用等回答完

        answer = ""
        # 做什么：准备一个空字符串，用来"边收边拼"完整回答
        # 逐词：
        #   answer  【自起名】最终回答
        #   = ""    【内置】空字符串（⚠️ 不是 None —— 下面要往它上面 += 字符串）
        # 为什么要自己拼一份：最后 done 事件要报"回答长度"；
        #   而且自己留存一份，将来想落库/做统计也方便
        async for piece, error in stream_ai(messages):
            # 做什么：**消费**上面那个异步生成器，一段段把内容收回来
            # 逐词：
            #   async for ... in ...  【内置】异步遍历（⚠️ 不能用普通 for）
            #   piece, error          【内置】"元组解包"：
            #        因为 stream_ai 每次 yield 的是**一对值** (片段, 错误)，
            #        所以这里可以一次写两个名字把两项分别接住。
            #        正常时是 ("你好", None)；出错时是 (None, "错误信息…")
            #   stream_ai(messages)   【本地】本文件上面那个异步生成器函数，
            #        ⚠️ 这**里要带括号**！因为它是生成器函数，
            #           带括号才是"调用它、拿到生成器对象"；
            #           不带括号传的是函数本身，没法迭代（对比 to_thread 是反过来的）
            if error:
                # 做什么：判断"这一段是不是错误"
                # 逐词：
                #   if error  【内置】错误信息是字符串 → 非空字符串为真；
                #        正常时它是 None（假值）→ 不进这个分支
                yield sse("error", {"message": error})
                # 做什么：把错误当成一个 **error 事件**发给前端
                # 逐词：
                #   sse(...)         【本地】拼 SSE 报文
                #   "error"          【自起名事件名】四种事件里的最后一种
                #   {"message": error}  【内置】字典，键名是 "message"，
                #        值是那句错误信息
                # ⚠️ 为什么"错误"要作为一种**事件**发出去，而不是抛异常：
                #    因为这时候流**已经开始发了**，HTTP 状态码早就发出去了、改不了。
                #    唯一能把错误告诉前端的办法，就是在流里发一条特殊事件
                break
                # 做什么：出错就结束循环，不再继续读后面的内容
                # 逐词：
                #   break  【内置】"中断"整个循环
                # ⚠️ 注意 break 之后代码会继续走到下面的 done 事件 ——
                #    也就是说出错时前端会收到 error 事件**和**done 事件。
                #    前端按"看到 error 就停"处理即可。
                #    （更严格的做法是出错后不再发 done，这是个可以改进点）
            answer += piece
            # 做什么：把这一小段拼进完整回答
            # 逐词：
            #   +=     【内置】复合赋值：answer += piece 等价于 answer = answer + piece。
            #          对字符串来说就是"接在后面"（拼接）
            #   piece  【自起名】上面解包出来的正文片段
            #   注意：这里**不是**在"产出一段"（那是下一行的 yield），
            #   只是在维护我们自己手里的完整版本
            yield sse("delta", {"content": piece})
            # 做什么：把这一小段包装成 **delta 事件**推给前端
            # 逐词：
            #   "delta"  【自起名事件名】delta = "增量、变化量" —— 表示"这是新增的一小段"。
            #        四种事件里它会出现**很多次**（一次一个字或几个字）
            #   {"content": piece}  【内置】字典：键名 "content"（内容），值是这一段
            #   前端每收到一条 delta 就往页面上追加一点文字 → 打字机效果
            # 收到一段就转发一段
            #   "delta" 是【自起名】的事件名（delta = 增量、变化量）

        yield sse("done", {
            # 做什么：**最后一条事件** —— 告诉前端"这一轮结束了，并附带统计"
            # 逐词：
            #   "done"   【自起名事件名】done = "完成"
            #   { ... }  统计信息字典
            "answer_length": len(answer),
            # 逐词：
            #   "answer_length"  【自起名】键名 → 回答长度
            #   len(answer)      【内置】数一数回答有多少个**字符**
            #   前端可以用它"校验自己收到的字数和后端一致"（少收了就是网络断了）
            "latency_ms": int((time.perf_counter() - started) * 1000),
            # 做什么：算这一轮总共花了多少毫秒
            # 逐词：
            #   "latency_ms"   【自起名】键名。latency = "延迟"，ms = millisecond（毫秒）
            #   (time.perf_counter() - started)  【标准库】"现在" 减 "开始" = 过了多少**秒**
            #   * 1000         【内置】秒换毫秒（×1000）
            #   int(...)       【内置】把小数转成整数（丢掉小数部分）。
            #        ⚠️ 注意 int() 是"截断"不是"四舍五入"：
            #           1234.9 会变成 1234（不是 1235）。
            #           对"耗时"来说差 1 毫秒无所谓，但要知道这个行为
        })
        # 结束事件带上长度和耗时，前端可以显示"用时 x.x 秒"

    return StreamingResponse(
        # 做什么：把生成器交给 StreamingResponse，让它"每 yield 一次就发一段"
        # 逐词：
        #   return             【内置】把响应对象返回给 FastAPI（由它真正发给客户端）
        #   StreamingResponse  【三方库】上面 import 的流式响应类
        event_stream(),
        # 做什么：第一个位置参数 = 要流式发送的**生成器对象**
        # 逐词：
        #   event_stream   【本地】上面那个异步生成器函数
        #   ()             【内置】⚠️ **括号不能少**！
        #        带括号 = 调用它 → 得到"生成器对象"（StreamingResponse 要的就是这个）
        #        不带括号 = 传的是"函数本身"，StreamingResponse 没法迭代它，会报错
        # ⚠️ 这里和 asyncio.to_thread 正好相反，容易记混：
        #      to_thread(vectorstore.reset)  → 不要括号（要函数本身）
        #      StreamingResponse(event_stream()) → 要括号（要生成器对象）
        #      记法：to_thread 是"把函数交给别人去调"；StreamingResponse 是"拿一个可迭代的东西"
        media_type="text/event-stream",
        # 做什么：设置响应的内容类型
        # 逐词：
        #   media_type=        【三方库】参数名：媒体类型（也就是 MIME 类型）
        #   "text/event-stream"  【SSE 协议规定值】拆开：
        #        text  = 文本类
        #        event = 事件
        #        stream = 流
        #        → "文本/事件流"。
        #        ⚠️ 浏览器/前端就是靠这个值知道"这是个 SSE 长连接"，
        #           少了它前端那套 EventSource 或流式解析就不生效
        headers={
            # 做什么：给响应的响应头里加两项
            # 逐词：headers= 【三方库】参数名；{ } 【内置】字典
            "Cache-Control": "no-cache",
            # 做什么：告诉浏览器和中间的代理"不要缓存这个响应"
            # 逐词：
            #   "Cache-Control"  【HTTP 标准字段名】缓存控制
            #   "no-cache"       【HTTP 标准取值】"不要缓存"。
            #        ⚠️ 为什么必须加：这是个"每次内容都不一样"的流，
            #           被缓存了前端可能拿到上一次的回答
            "X-Accel-Buffering": "no",
            # 做什么：告诉 Nginx 之类的反向代理"不要缓冲，来多少直接转发多少"
            # 逐词：
            #   "X-Accel-Buffering"  【Nginx 约定的私有响应头】X- 开头表示"非标准头"，
            #        Accel = Accelarated（Nginx 的加速模块名）
            #   "no"                 【取值】"不要缓冲"
            #   ⚠️ 不设的后果（重要）：代理会攒够一批才转发，
            #       前端就变成"一大段一次性蹦出来"，打字机效果彻底没了。
            #       而且**本地直连看不出来**（没有代理），一上生产就暴露 ——
            #       所以现在就加上，属于"提前防坑"
        },
    )
    # X-Accel-Buffering: no 很关键 —— 不设的话前面有代理时会被缓冲，
    #   前端就会"攒一大段才一次性显示"，打字机效果就没了

# ------------------------------------------------------------
# 第六部分：接口② —— 知识库统计
# ------------------------------------------------------------


@router.get("/stats", summary="知识库统计")
# 做什么：把下面那个函数注册成 "GET /api/rag/stats" 接口
# 逐词：
#   @router.get("/stats")  【三方库】注册为 **GET** 接口。
#        ⚠️ 和上面那个 .post 的区别：
#             GET  = 只读取数据、不改服务器状态，可以直接用浏览器地址栏访问
#             POST = 要提交数据、会改状态
#          所以"查统计"用 GET 更符合语义（这也叫"符合 RESTful 风格"）
#   加了 prefix 之后，最终路径是 /api/rag/stats
async def rag_stats():
    # 做什么：定义处理函数（不需要任何参数）
    # 逐词：
    #   rag_stats  【自起名】rag + stats（statistics 统计）
    #   ()         【内置】没有参数 —— 因为这个接口不需要前端传任何东西，
    #              也不需要数据库会话（只查向量库）
    """看看向量库里现在有多少块新闻"""
    return {
        # 做什么：直接返回一个字典（FastAPI 会自动把它转成 JSON 响应）
        # 逐词：
        #   return  【内置】返回
        #   { }     【内置】花括号 = 字典
        "code": 200,
        # 逐词：
        #   "code"  【自起名】本项目自定义的响应格式：用 body 里的 code 表示业务状态
        #   200     【数字】照搬 HTTP 的成功码，便于前端统一判断
        #   ⚠️ 注意这个 code 和 HTTP 状态码是**两回事**：
        #       HTTP 状态码在响应头里（由框架决定）；这个 code 在响应体里（由我们自己写）
        #       项目里统一用这套格式，好处是前端只判断 body.code 就行
        "message": "查询成功",
        # 逐词：
        #   "message"  【自起名】键名 → 给用户看的提示文字
        #   "查询成功"  字符串
        "data": {"chunk_count": count()},
        # 做什么：真正的数据放在 data 里（嵌套一层，方便以后加字段）
        # 逐词：
        #   "data"     【自起名】键名 → 数据本体
        #   { ... }    又是一个字典（所以 data 是个"套了一层"的结构）
        #   "chunk_count"  【自起名】键名。chunk（块）+ count（数量）→ "块数"
        #   count()    【本地】utils/vectorstore.py 的函数（上面 import 进来的）：
        #        ⚠️ 注意这里**没有 await** —— 因为那个 count() 是**普通同步函数**
        #           （它内部调 chromadb 的 .count()，很快、不阻塞）。
        #           如果对一个 async 函数忘记 await，你会拿到一个协程对象，
        #           而且 json 序列化时直接报错 —— 这种错很容易发现
        #   它内部调的是 chromadb 集合对象的 .count()，只数个数、不取数据，非常快
    }
