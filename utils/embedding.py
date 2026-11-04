"""向量化：把文本转成向量，走硅基流动的 bge-m3（OpenAI 兼容格式）。

本来想在本地跑模型，这台机器的系统策略拦了 torch 的 DLL 加载，于是改成调 API：
效果一样、代码更短，也不用装 torch。
"""

import httpx

from config.rag_conf import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL

# 一次送 32 条。太多容易触发限流或超时；太少则请求次数多、总耗时变长
BATCH_SIZE = 32


def _embed_batch(texts: list) -> list:
    """调一次接口，把一批文本转成向量。内部函数，外面用 embed_texts。"""
    resp = httpx.post(
        f"{EMBEDDING_BASE_URL}/embeddings",
        headers={
            # Bearer 后面那个空格不能少，漏了会 401
            "Authorization": f"Bearer {EMBEDDING_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": EMBEDDING_MODEL,
            # 键名就是 input，接口规定的，不能改成 texts
            "input": texts,
            "encoding_format": "float",
        },
        timeout=60,
    )

    if resp.status_code != 200:
        # 没有向量后面什么都做不了，早报错比让错误藏到下游强；把对方原文带出来方便排查
        raise RuntimeError(f"向量化接口错误({resp.status_code}): {resp.text}")

    data = resp.json()["data"]
    # 接口不保证返回顺序和输入一致。不排序的话，可能把第 3 段的向量配给第 1 段文字，
    # 而且不会报错——属于最难查的那类静默错误
    data.sort(key=lambda item: item["index"])

    return [item["embedding"] for item in data]


def embed_texts(texts: list) -> list:
    """对外入口：任意条文本都行，内部按 BATCH_SIZE 分批。返回的向量与输入一一对应。"""
    # 先查密钥：不查的话会一路跑到接口返回 401，还得猜是 key 错了还是网络问题
    if not EMBEDDING_API_KEY:
        raise RuntimeError("未配置 EMBEDDING_API_KEY，请在 .env 文件中设置硅基流动的密钥")

    vectors = []
    for i in range(0, len(texts), BATCH_SIZE):
        # extend 才是摊平；append 会把整批塞成嵌套的一层
        vectors.extend(_embed_batch(texts[i:i + BATCH_SIZE]))

    return vectors
