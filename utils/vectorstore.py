"""ChromaDB 封装：存「文本 + 向量 + 来源」，按意思相近检索。

两个容易踩的点：
- Chroma 返回的是 distance（越小越像），不是相似度，必须 1 - distance 换算
- upsert 是「有就覆盖、没有就插入」，所以建索引脚本可以反复跑，不会堆出两份

chromadb 的接口全是同步的，crud/rag.py 调用时要 asyncio.to_thread 丢线程池，
不然会卡住 FastAPI 的事件循环。
"""

import chromadb

from config.rag_conf import CHROMA_DIR, COLLECTION_NAME

_client = None


def _collection():
    """拿集合对象：第一次调用才连库（懒加载），集合不存在会自动建。"""
    global _client
    if _client is None:
        # 必须用绝对路径：相对路径是按「当前工作目录」解析的，
        # 从 scripts/ 里跑一次索引就会多长出一份库
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    return _client.get_or_create_collection(
        name=COLLECTION_NAME,
        # 文本用余弦：只看向量方向，不看长度
        metadata={"hnsw:space": "cosine"},
    )


def add(ids: list, texts: list, embeddings: list, metadatas: list):
    """写入一批块。四个列表必须等长、位置一一对应，否则 Chroma 报 Unequal lengths。"""
    _collection().upsert(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        # 命中时要靠它告诉用户答案出自哪篇新闻
        metadatas=metadatas,
    )


def search(vector: list, top_k: int) -> list:
    """按向量检索，返回 [{text, meta, score}]，按相似度从高到低。"""
    result = _collection().query(
        # 接口支持一次查多个问题，所以向量要再套一层列表
        query_embeddings=[vector],
        n_results=top_k,
    )
    # 返回的是「列表套列表」，我们只问了一个问题，所以每项取 [0]
    texts = result["documents"][0]
    metas = result["metadatas"][0]
    distances = result["distances"][0]

    hits = []
    for i in range(len(texts)):
        # 用下标遍历，是为了让 texts / metas / distances 三个列表对齐
        hits.append({
            "text": texts[i],
            "meta": metas[i],
            # 距离 0 = 完全一样，所以相似度 = 1 - 距离。
            # 写反了不会报错，只是最像的排到最后——最难查的那种
            "score": round(1 - distances[i], 4),
        })

    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits


def get_hashes() -> dict:
    """取出 {news_id: content_hash}，增量索引靠它判断哪些新闻变了。"""
    # 只取 metadatas，不带正文和向量，快很多
    data = _collection().get(include=["metadatas"])

    hashes = {}
    for meta in data["metadatas"]:
        # 跳过空档案袋和没有 news_id 的块
        if meta and "news_id" in meta:
            # 老数据可能没 content_hash，用 .get 给默认值；元数据不允许 None，所以给空串
            hashes[meta["news_id"]] = meta.get("content_hash", "")
    return hashes


def delete_news(news_id: int):
    """删掉某篇新闻的所有块（内容改了要重新切块时用）。"""
    _collection().delete(where={"news_id": news_id})


def reset():
    """删掉整个集合。全量重建索引前用。"""
    global _client
    if _client is None:
        # 不能调 _collection()：那个函数会顺手建表，先建再删是白干
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        _client.delete_collection(name=COLLECTION_NAME)
    except Exception:
        # 集合本来就不存在，等于目标已经达成；只有这种情况才允许 except: pass
        pass


def count() -> int:
    """库里现在有多少块。"""
    return _collection().count()
