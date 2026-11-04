"""Redis 连接与缓存读写。

所有异常都吞掉，返回 None / False —— 业务层据此自动回退数据库。
"""

import json
import os
from typing import Any

import redis.asyncio as redis

# 优先读环境变量（容器部署用），本地开发用默认值
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
    # redis-py 8.x 连 Redis 5.x 必须指定，否则报 unknown command HELLO
    protocol=2,
    # 不设超时就走系统默认（约 4 秒），一次请求读两次缓存就是 8 秒。
    # 设了之后连不上立刻失败、走数据库兜底
    socket_connect_timeout=0.2,
    socket_timeout=0.5,
)


async def get_cache(key: str):
    """读字符串缓存，失败返回 None。"""
    try:
        return await redis_client.get(key)
    except Exception as e:
        print(f"获取缓存失败：{e}")
        return None


async def get_json_cache(key: str):
    """读 JSON 缓存（列表 / 字典），失败返回 None。"""
    try:
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        print(f"获取 JSON 缓存失败：{e}")
        return None


async def set_cache(key: str, value: Any, expire: int = 3600):
    """写缓存。字典 / 列表会先转 JSON；失败返回 False。"""
    try:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        await redis_client.setex(key, expire, value)
        return True
    except Exception as e:
        print(f"设置缓存失败：{e}")
        return False
