# 区域：Redis 缓存冒烟脚本（手动执行，不进 pytest）
# 作用：验证 分类 / 新闻列表 / 新闻详情 三类缓存的「写入 → 读取」真的通

import asyncio
import os
import sys

# 把项目根目录加进模块搜索路径，这样从任何目录执行本脚本都能 import 到项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cache.news_cache import (
    set_cache_categories, get_cached_categories,
    set_cache_news_list, get_cache_news_list,
    cache_news_detail, get_cached_news_detail,
)
# 注意：import 写在 sys.path 之后是故意的 —— 必须先把项目根加进搜索路径，
#   否则这行会报 ModuleNotFoundError: No module named 'cache'


async def main():
    # 1. 分类缓存
    categories = [{"id": 1, "name": "时政"}, {"id": 2, "name": "财经"}]
    print("[1] 写入分类缓存:", await set_cache_categories(categories, expire=120))
    print("[1] 读取分类缓存:", await get_cached_categories())

    # 2. 新闻列表缓存（key = news_list:all:1:10）
    news_list = [{"id": 101, "title": "测试新闻A"}, {"id": 102, "title": "测试新闻B"}]
    print("[2] 写入列表缓存:", await set_cache_news_list(None, 1, 10, news_list, expire=120))
    print("[2] 读取列表缓存:", await get_cache_news_list(None, 1, 10))

    # 3. 新闻详情缓存
    detail = {"id": 101, "title": "测试新闻A", "content": "这是一条测试内容..."}
    print("[3] 写入详情缓存:", await cache_news_detail(101, detail, expire=120))
    print("[3] 读取详情缓存:", await get_cached_news_detail(101))

    print()
    print("[提示] 想看 Redis 里实际存了什么，可以再跑：")
    print("       .venv\\Scripts\\python.exe scripts\\redis_visualizer.py")


if __name__ == "__main__":
    asyncio.run(main())
    # 加 __main__ 守卫：被别的代码 import 时不会自动连 Redis
