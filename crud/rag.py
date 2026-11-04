# ============================================================
# 区域：RAG 核心逻辑（crud 层）
# 作用：串起"切块 → 向量化 → 存库 → 检索"，对外提供 2 个函数
#   ① build_index  ：把 MySQL 的新闻同步进向量库（离线跑，支持增量）
#   ② retrieve     ：拿问题去向量库找相关新闻（在线跑，每次问答都调）
#
# 关键理解（面试会问）：
#   ① 增量索引：给每篇新闻算一个"内容指纹"（md5），指纹没变就不重复向量化。
#      403 篇全量重建要几分钟，增量只要几秒。
#   ② 内容变了必须先删旧块再写新块：如果新版切出的块变少了，
#      只 upsert 会留下旧版的"僵尸块"，检索时会命中已经改掉的内容。
#   ③ 为什么用 asyncio.to_thread：向量化要调网络接口、Chroma 读写是同步操作，
#      直接在协程里跑会把整个服务的其他请求卡住；丢到线程池就不会。
#
# ============================================================

# ------------------------------------------------------------
# 第一部分：导包（8 个 import，来自三种不同来源）
# ------------------------------------------------------------

import asyncio
# 把 Python 自带的 asyncio 模块拿进来

import hashlib
# 拿进 Python 自带的哈希库

from sqlalchemy import func, select
# 从 sqlalchemy 里取出 func 和 select 这两个东西
# 【2026-09-20 补进来的 func】它是"调用数据库自带函数"的工具：
#   func.count()        → SQL 里的 COUNT()，用来数条数
#   func.char_length()  → SQL 里的 CHAR_LENGTH()，用来算正文有多少字
#   统计类问题（"一共有多少篇""哪一篇最长"）就是靠它俩直查数据库的，见本文件末尾那一段
# 从 sqlalchemy 里取出 select 这一个函数

from sqlalchemy.ext.asyncio import AsyncSession
# 取出异步版的"数据库会话"类，只用它做类型标注

from config.rag_conf import SCORE_THRESHOLD, TOP_K
# 从配置中心取出两个常量

from models.news import Category, News
# 取出 news 表对应的两个 ORM 类

from utils.embedding import embed_texts
# 取出"把文字批量转成向量"的函数

from utils.splitter import split_text
# 取出"把长文本切成小块"的函数

from utils import vectorstore
# 把 utils/vectorstore.py 这个**模块本身**拿进来

# ============================================================
# 第二部分：工具函数
# ============================================================

def fingerprint(text: str) -> str:
# 定义一个函数，输入一段文字，输出它的"内容指纹"
    """算内容指纹：内容一样指纹就一样，用来判断这篇新闻要不要重新向量化"""
# docstring（文档字符串）
    return hashlib.md5(text.encode("utf-8")).hexdigest()
# 把 text 转成字节 → 算 md5 → 转成 32 位十六进制字符串 → 返回

def is_small_talk(question: str) -> bool:
# 定义函数，判断一个问题是"闲聊/打招呼"还是"真问题"
    """
    判断是不是"打招呼"这类问题
    是的话直接当普通 AI 回答，不用去查新闻库（省一次检索，回答也更自然）
    为什么需要它：实测发现"你好"跟新闻的相似度能到 0.46，
      光靠阈值挡不住，会被误判成"有相关资料"
    """
    q = question.strip().lower()
# 把用户的问题"清洗"一下，存进短名 q
    for word in ("你好", "您好", "hello", "hi", "在吗", "你是谁", "谢谢", "再见"):
# 把这些词挨个拿出来试
        if q.startswith(word):
# 判断清洗过的问题是不是"以这个词开头"
            return True
# 命中一个就立刻返回 True
    return False
# 循环走完一个都没命中 → 返回 False

# ============================================================
# 第三部分：① 入库 build_index —— 把 MySQL 新闻同步进向量库
# ============================================================

async def build_index(db: AsyncSession, rebuild: bool = False) -> dict:
# 定义异步函数，把 MySQL 里的新闻切块 + 向量化 + 写进向量库
    """
    把新闻同步进向量库
    rebuild=False（默认）：增量，只处理新增和内容变化的新闻
    rebuild=True         ：全量重建，先清空向量库再全部重灌
    返回统计：{total, added, updated, skipped, removed, chunks}
    """
    if rebuild:
# 如果调用方要求"全量重建"，走这个分支
        await asyncio.to_thread(vectorstore.reset)
# 把向量库整个清空（删掉那张表）
        old_hashes = {}
# 给 old_hashes 一个空字典
    else:
# 不是全量重建，就按"增量"处理
        old_hashes = await asyncio.to_thread(vectorstore.get_hashes)
# 先把库里现有的"新闻id → 内容指纹"取出来，供下面逐个比对

    stmt = select(News, Category.name).join(Category, News.category_id == Category.id)
# 拼一条"联表查询"（新闻 + 它的分类名），存进变量但不执行
    rows = (await db.execute(stmt)).all()
# 真正把 SQL 发给数据库，把结果全部取出来

    ids, texts, metas = [], [], []
# 一次定义三个空列表（链式赋值的一种形式）

    added = updated = skipped = removed = 0
# 一次定义四个计数器，都从 0 开始
    seen = set()
# 准备一个空集合，用来记录"MySQL 里还存在的新闻 id"

    for news, category_name in rows:
# 一行一行遍历查询结果，并把每行"拆成两部分"
        seen.add(news.id)
# 把这篇新闻的 id 记进"见过的集合"
        text = f"{news.title}\n{news.content}"
# 把标题和正文拼成一段，供下面算指纹
        fp = fingerprint(text)
# 给这段文字算指纹，存进 fp

        if old_hashes.get(news.id) == fp:
# 判断"库里原有的指纹"和"刚算出来的指纹"是不是一样
            skipped += 1
# 跳过计数器 +1
            continue
# 跳过本轮循环剩下的代码，直接进入下一条新闻

        if news.id in old_hashes:
# 判断"这个 id 是否已经在库里"（用来区分"更新"和"新增"）
            await asyncio.to_thread(vectorstore.delete_news, news.id)
# 先把这篇新闻的旧块删干净
            updated += 1
# 更新计数器 +1
        else:
# 库里原来没有这个 id → 属于新增
            added += 1
# 新增计数器 +1

        chunks = split_text(news.content) or [news.title]
# 把正文切成小块；如果切出来是空的，就退化成"只有标题的一块"

        for i, chunk in enumerate(chunks):
# 遍历每一块，同时拿到它的序号
            ids.append(f"news_{news.id}_{i}")
# 给这一块生成唯一编号，加进 ids 列表
            texts.append(f"{news.title}\n{chunk}")
# 把"标题 + 换行 + 这一块内容"加进 texts 列表
            metas.append({
# 往 metas 列表里追加一个字典 —— 这一块的"档案袋"
                "news_id": news.id,
                "title": news.title,
                "category": category_name,
                "content_hash": fp,
            })
            # 每块的"档案袋"：命中时靠它告诉用户答案出自哪篇新闻

    for news_id in old_hashes:
# 遍历"库里已有指纹"的所有键，找出该清理的孤儿数据
        if news_id not in seen:
# 如果这个 id 不在"MySQL 里还存在的 id 集合"里 → 它已经被删了
            await asyncio.to_thread(vectorstore.delete_news, news_id)
# 把这个孤儿 id 在向量库里的块删掉
            removed += 1
# 清理计数器 +1

    if texts:
# 判断"这次到底有没有东西要写"
        embeddings = await asyncio.to_thread(embed_texts, texts)
# 把这一批文本全部转成向量
        await asyncio.to_thread(vectorstore.add, ids, texts, embeddings, metas)
# 把四个列表一起写进向量库

    return {
# 返回一个字典，把本次统计结果交给调用方
        "total": len(rows),
        "added": added,
        # 新增了几篇
        "updated": updated,
        # 更新了几篇（内容变了、先删旧块再写新块的）
        "skipped": skipped,
        # 跳过了几篇（指纹没变，增量省下来的工作量就看这个数）
        "removed": removed,
        # 清理了几个孤儿（MySQL 已删、向量库还留着的）
        "chunks": len(texts),
    }

# ============================================================
# 第四部分：② 检索 retrieve —— 拿问题去找相关新闻，并判断"要不要带资料"
# ============================================================

async def retrieve(question: str, top_k: int = None) -> dict:
# 定义异步函数，输入问题，输出"检索结果 + 模式判断"
    """
    检索并判断"这次要不要带资料"
    question：用户问题
    top_k：检索几块（不传用配置里的默认值）
    返回：{"hits": 资料列表, "top_score": 最高相似度, "mode": "rag" 或 "ai"}
    """
    k = top_k or TOP_K
# 确定"这次要取几块"

    if is_small_talk(question):
# 先判断这是不是"打招呼"这类闲聊
        return {"hits": [], "top_score": 0.0, "mode": "ai"}
# 直接返回"没有资料、分数 0、走 ai 模式"

    vector = await asyncio.to_thread(embed_texts, [question])
# 把用户的问题也转成向量

    hits = await asyncio.to_thread(vectorstore.search, vector[0], k)
# 拿问题向量去库里找最像的 k 块

    top_score = hits[0]["score"] if hits else 0.0
# 取"最高相似度"，作为"库里到底有没有相关内容"的判断依据

    mode = "rag" if top_score >= SCORE_THRESHOLD else "ai"
# 决定这次走"带资料"还是"当普通 AI"——整个降级设计就这一行

    return {"hits": hits, "top_score": top_score, "mode": mode}
# 把三个结果打包返回

# ============================================================
# 结构化问答：向量检索答不了的那一类问题（2026-09-20 新增）
#
# 【为什么要有这一段 —— 一个真实的"答不了"案例】
#   用户问："你一共有多少篇新闻" / "最长的一篇新闻是什么"
#   页面上的回答标签全是"通用回答"，内容是"我没有固定的新闻篇数哦"
#   —— 说明它压根没检索到新闻，直接降级成普通 AI 聊天了。
#
#   根因不是阈值调得不对，而是【向量检索本来就答不了这类问题】：
#     向量检索做的是"找出和问题最像的那几块文本"，
#     而"一共有多少篇""最长的是哪一篇"需要的是 COUNT / ORDER BY ——
#     属于【聚合和排序】，不是【找相似】。
#     就算检索到的 4 块新闻全都相关，模型也无从知道库里的总数是多少。
#
#   所以补一条"直查数据库"的通道：
#     命中统计类问题 → 直接查 SQL → 把查到的数字当成"资料"
#     没命中         → 走原来的向量检索（老逻辑一行都没动）
#
# ★ 和项目二对比着看（面试很好用）
#   项目二（Agent）：调哪个工具、调几次，由【模型】决定
#   项目一（本文件）：判断规则是【我写死的】—— 只能覆盖我预先想到的那几种问法
#   这就是"Agent 里模型做决定、RAG 里我做决定"最具体的体现。
#   反过来看也成立：项目二能答的问题类型是开放的，项目一只覆盖下面这几种。
# ============================================================

# 统计类问题的关键词表。
#
# 【判断顺序：先"最长"，再"分类分布"，最后"总数"】——
# 和上面 is_small_talk 一个思路：优先认更具体的意图。
# 因为"最长的那篇新闻有多少字"同时含"最长"和"多少"，按"最长"处理才对。
LONGEST_KEYWORDS = ("最长", "篇幅最长", "字数最多", "内容最多")

DISTRIBUTION_KEYWORDS = ("各分类", "每个分类", "分类分布", "分类各多少", "哪几类")

# 总数类：只认"多少 / 几篇 / 几条"这种明确的【数量询问】
# ⚠️ 不要写成"一共 / 总共"：用户说"一共有哪些科技新闻"是在要【列表】，
#    向量检索能处理，硬判成"统计"反而答非所问。
COUNT_KEYWORDS = ("多少", "几篇", "几条")

def detect_structured_intent(question: str) -> str:
    """
    判断这个问题是不是"统计 / 排序类"，也就是"向量检索答不了、必须直查数据库"的那一类。

    返回值（返回空串表示不是统计类 → 交回原来的向量检索）：
        "longest"        问哪一篇正文最长
        "distribution"   问各分类分别有多少篇
        "count"          问总数（或某个分类的数量）
    """
    q = (question or "").strip()

    if not q:
        return ""

    for word in LONGEST_KEYWORDS:
        if word in q:
            return "longest"

    for word in DISTRIBUTION_KEYWORDS:
        if word in q:
            return "distribution"

    for word in COUNT_KEYWORDS:
        if word in q:
            return "count"

    return ""

async def build_structured_hits(
    db: AsyncSession,
    intent: str,
    question: str,
) -> list:
    """
    直查数据库，把统计结果拼成一条"资料"。

    返回结构和向量检索的 hits 完全一致：
        [{"text": 资料正文, "meta": {"title": ..., "category": ...}, "score": 1.0}]

    为什么故意做得一模一样：
      这样 routers/ragchat.py 那边【一行都不用改】就能用 ——
      它只认 hits 这个结构，不关心资料是"检索来的"还是"查库来的"。
    """
    if intent == "count":
        # 先看看问题里有没有点名某个分类："科技类有多少篇" → 只数科技这一个
        category_names = (await db.execute(select(Category.name))).scalars().all()

        matched = [name for name in category_names if name in question]

        if len(matched) == 1:
            total = await db.scalar(
                select(func.count())
                .select_from(News)
                .join(Category, News.category_id == Category.id)
                .where(Category.name == matched[0])
            )
            text = f"数据库里【{matched[0]}】分类的新闻一共 {total} 篇。"
            title = f"{matched[0]}分类的篇数"
            category = matched[0]
        else:
            total = await db.scalar(select(func.count()).select_from(News))
            text = f"数据库里一共有 {total} 篇新闻。"
            title = "新闻总数"
            category = "全部"

    elif intent == "distribution":
        rows = (
            await db.execute(
                select(Category.name, func.count())
                .select_from(News)
                .join(Category, News.category_id == Category.id)
                .group_by(Category.name)
            )
        ).all()

        # 按篇数从多到少排一下：模型读起来有条理，也免得它自己乱排
        ordered = sorted(rows, key=lambda item: -item[1])
        parts = [f"{name} {count} 篇" for name, count in ordered]
        total = sum(count for _, count in rows)

        text = f"数据库里一共有 {total} 篇新闻，各分类：" + "、".join(parts) + "。"
        title = "各分类的新闻篇数"
        category = "全部"

    elif intent == "longest":
        # 按【正文字数】降序取第一条 —— 这就是"最长的一篇"
        # ⚠️ 用 char_length 而不是 length：后者按【字节】算，一个汉字算 3 个，
        #    虽然排序结果碰巧一样，但报出来的"字数"会大三倍，模型一开口就说错。
        #    coalesce 是"正文为 NULL 时当空字符串"，免得 NULL 参与排序时跑到最前面。
        length_column = func.char_length(func.coalesce(News.content, ""))

        row = (
            await db.execute(
                select(News, Category.name)
                .join(Category, News.category_id == Category.id)
                .order_by(length_column.desc())
                .limit(1)
            )
        ).first()

        if row is None:
            text = "数据库里目前没有任何新闻。"
            title = "最长的一篇"
            category = "全部"
        else:
            news, category_name = row
            length = len(news.content or "")

            text = (
                f"正文最长的一篇是《{news.title}》"
                f"（新闻编号 {news.id}，分类 {category_name}，正文 {length} 字）。"
            )
            title = f"最长的一篇：《{news.title}》"
            category = category_name

    else:
        # 传进来的 intent 不在支持范围内 → 什么都不给，让调用方走原来的路
        return []

    return [
        {
            "text": text,
            "meta": {"title": title, "category": category},
            # score 固定 1.0：表示"这条资料是直查数据库得到的，不是检索来的"。
            # 前端会把它显示在来源卡片上，方便一眼看出这次是查库不是检索。
            "score": 1.0,
        }
    ]
