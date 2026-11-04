"""建索引脚本：把 MySQL 里的新闻切块、向量化，写进 ChromaDB。

用法（在项目根目录执行）：
    python scripts/ingest_news.py             增量：只处理新增和内容变化的
    python scripts/ingest_news.py --rebuild   全量：先清空向量库再全部重灌

能反复跑，靠两重保证：内容指纹比对（没变就跳过）+ upsert 幂等（同 id 覆盖不重复）。
默认走增量是因为向量化要调外部接口，403 篇全量要几分钟，增量通常几秒。
"""

import argparse
import asyncio
import os
import sys

# 脚本在 scripts/ 子目录里，不加这行 import 找不到 config / crud；加了就能从任意目录执行
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.db_conf import AsyncSessionLocal, async_engine
from crud.rag import build_index
from utils.vectorstore import count


async def main():
    parser = argparse.ArgumentParser(description="把 MySQL 新闻同步进向量库")
    # store_true：命令行带了这个开关就是 True，不带就是 False
    parser.add_argument("--rebuild", action="store_true", help="全量重建（会先清空向量库）")
    args = parser.parse_args()

    before = count()

    # 脚本不在 FastAPI 里，没有依赖注入，得自己开会话；async with 会在结束时自动关
    async with AsyncSessionLocal() as db:
        stats = await build_index(db, rebuild=args.rebuild)

    after = count()

    # 打印模式很重要：增量跑出来「新增 0」是正常的，不说清楚用户会以为失败了
    mode = "全量重建" if args.rebuild else "增量索引"
    print(f"索引构建完成（{mode}）：")
    print(f"  新闻总数：{stats['total']} 篇")
    # added = 新入库；updated = 指纹变了，先删旧块再写新的
    # skipped = 没变，跳过（增量省下来的时间在这）；removed = MySQL 已删、向量库残留的孤儿块
    print(f"  新增 {stats['added']} ｜ 更新 {stats['updated']} ｜ 未变跳过 {stats['skipped']} ｜ 清理 {stats['removed']}")
    print(f"  本次写入 {stats['chunks']} 块")
    print(f"  向量库：{before} 块 → {after} 块")

    # 脚本干完就该收摊；不 dispose 的话退出时连接没归还，会打印警告
    await async_engine.dispose()


if __name__ == "__main__":
    # 只有直接运行才执行；被 import（比如 pytest 收集）时不会把整个流程跑起来
    asyncio.run(main())
