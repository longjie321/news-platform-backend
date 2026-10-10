import json
import os
from typing import Any

import redis.asyncio as redis

# Redis 配置：优先读环境变量（生产/容器部署用），本地开发用默认值
# 例：docker run -e REDIS_HOST=host.docker.internal
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))


# 创建 Redis 的连接对象
redis_client = redis.Redis(
    host=REDIS_HOST,  # Redis 服务器的主机地址
    port=REDIS_PORT,  # Redis 端口号
    db=REDIS_DB,  # Redis 数据库编号，0~15
    decode_responses=True,  # 是否将字节数据解码为字符串
    protocol=2,  # redis-py 8.x 连 Redis 5.x 必须指定，否则报 unknown command HELLO

    socket_connect_timeout=0.2,
    # 连接超时：Redis 没启动时，最多等 0.2 秒就放弃
    #   为什么必须设这个（这是一个真实的性能坑）：
    #   不设的话用的是系统默认超时（实测 4 秒左右），而一次接口调用会读写缓存两次，
    #   于是 Redis 一挂，每个新闻接口就要卡 8 秒，前端表现成"打开特别慢"
    #   加了之后：连不上就 0.2 秒快速失败 → 走数据库兜底，接口恢复正常速度

    socket_timeout=0.5,
    # 读写超时：连上了但响应很慢时，0.5 秒也放弃，同样是为了"快速失败、走兜底"
)


# 设置 和 读取（字符串 和 列表或字典）"[{}]"
# 读取：字符串
async def get_cache(key: str):
    # return await redis_client.get(key)
    try:
        return await redis_client.get(key)
    except Exception as e:
        print(f"获取缓存失败：{e}")
        return None


# 读取：列表或字典
async def get_json_cache(key: str):
    try:
        data = await redis_client.get(key)
        if data:
            return json.loads(data)  # 序列化
        return None
    except Exception as e:
        print(f"获取 JSON 缓存失败：{e}")
        return None


# 设置缓存 setex(key, expire, value)
async def set_cache(key: str, value: Any, expire: int = 3600):
    try:
        if isinstance(value, (dict, list)):
            # 转字符串再存
            value = json.dumps(value, ensure_ascii=False)  # 中文正常保存
        await redis_client.setex(key, expire, value)
        return True
    except Exception as e:
        print(f"设置缓存失败：{e}")
        return False
