"""新闻数据访问。

2026-09-15 合并说明：原来是 news.py 和 news_cache.py 两个文件、各有一套同名函数，
而且各有一半是死代码，读代码的人会以为有两套实现。现在合并到这里：
带缓存的那份留下，纯数据库的那份只留在不该缓存的地方。

缓存策略：分类、列表、详情、相关新闻走缓存；
新闻总数和浏览量 +1 故意不走（前者容易和列表对不上，后者本来就是写操作）。
"""

from fastapi.encoders import jsonable_encoder

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cache.news_cache import (
    get_cached_categories, set_cache_categories,
    get_cache_news_list, set_cache_news_list,
    get_cached_news_detail, cache_news_detail,
    get_cached_related_news, cache_related_news,
)
# 缓存读写统一走 cache 层，crud 层不直接碰 Redis

from models.news import Category, News
from schemas.base import NewsItemBase
from schemas.news import NewsDetailResponse, RelatedNewsResponse


async def get_categories(db: AsyncSession, skip: int = 0, limit: int = 100):
    cached_categories = await get_cached_categories()
    if cached_categories:
        return cached_categories

    # 表里本来就有 sort_order，之前没写 → 顺序靠运气，现在改成确定的
    stmt = select(Category).order_by(Category.sort_order).offset(skip).limit(limit)
    result = await db.execute(stmt)
    categories = result.scalars().all()

    if categories:
        categories = jsonable_encoder(categories)
        await set_cache_categories(categories)

    return categories


async def get_news_list(db: AsyncSession, category_id: int, skip: int = 0, limit: int = 10):
    page = skip // limit + 1
    cached_list = await get_cache_news_list(category_id, page, limit)
    if cached_list:
        return [News(**item) for item in cached_list]

    # order_by 之前是缺的，两个问题：
    #   ① 分页会错：没有 ORDER BY 的 LIMIT/OFFSET，MySQL 不保证顺序，翻页可能重复或漏
    #   ② 语义不对：新闻列表应该最新在前
    # 第二排序键 id 是为了 publish_time 相同时也有确定顺序
    stmt = (
        select(News)
        .where(News.category_id == category_id)
        .order_by(News.publish_time.desc(), News.id.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    news_list = result.scalars().all()

    if news_list:
        # by_alias=False 保持数据库字段名，Redis 里是给后端自己读的
        news_data = [NewsItemBase.model_validate(item).model_dump(mode="json", by_alias=False) for item in news_list]
        await set_cache_news_list(category_id, page, limit, news_data)

    return news_list


async def get_news_count(db: AsyncSession, category_id: int):
    stmt = select(func.count(News.id)).where(News.category_id == category_id)
    result = await db.execute(stmt)
    return result.scalar_one()


async def get_news_detail(db: AsyncSession, news_id: int):
    cached_news = await get_cached_news_detail(news_id)
    if cached_news:
        return News(**cached_news)

    stmt = select(News).where(News.id == news_id)
    result = await db.execute(stmt)
    news = result.scalar_one_or_none()

    if news:
        # exclude related_news：它是单独查、单独缓存的，News 模型也没有这个字段
        # exclude views（2026-09-21）：浏览量每看一次 +1，而缓存活 300 秒，
        #   存进去那一刻就已经旧了 —— 以前会返回同一个浏览数 5 分钟不动
        news_dict = NewsDetailResponse.model_validate(news).model_dump(
            by_alias=False, mode="json", exclude={'related_news', 'views'}
        )
        await cache_news_detail(news_id, news_dict)

    return news


async def increase_news_views(db: AsyncSession, news_id: int):
    # 必须显式写 updated_at=News.updated_at：模型上配了 onupdate=datetime.now，
    # 不覆盖的话，用户每看一次新闻「更新时间」就被改一次
    stmt = (
        update(News)
        .where(News.id == news_id)
        .values(views=News.views + 1, updated_at=News.updated_at)
    )
    result = await db.execute(stmt)
    await db.commit()

    return result.rowcount > 0


async def get_news_views(db: AsyncSession, news_id: int):
    # 故意不走缓存：这是全项目变化最快的字段，长 TTL 必然返回旧值。
    # 也不能复用 UPDATE 的 rowcount —— 那只是「改了几行」，不是「改完是多少」
    stmt = select(News.views).where(News.id == news_id)
    return await db.scalar(stmt)


async def get_related_news(db: AsyncSession, news_id: int, category_id: int, limit: int = 5):
    cached_related = await get_cached_related_news(news_id, category_id)
    if cached_related:
        return cached_related

    # 同分类、排除自己；先热门再新
    stmt = select(News).where(
        News.category_id == category_id,
        News.id != news_id
    ).order_by(
        News.views.desc(),
        News.publish_time.desc()
    ).limit(limit)
    result = await db.execute(stmt)
    related_news = result.scalars().all()

    if related_news:
        related_data = [
            RelatedNewsResponse.model_validate(news).model_dump(by_alias=False, mode="json")
            for news in related_news
        ]
        await cache_related_news(news_id, category_id, related_data)
        return related_data

    return []
