# ============================================================
# 区域：新闻数据访问（crud 层）
# 作用：把"读新闻"这件事集中在一个模块，对外提供 6 个函数
#   get_categories / get_news_list / get_news_count
#   get_news_detail / get_related_news / increase_news_views
#
# 【合并说明 · 2026-09-15】
#   原来这里是 crud/news.py 和 crud/news_cache.py 两个文件，各有 6 个**同名**函数，
#   而且各有一半是死代码（news.py 里 4 个没人调用，news_cache.py 里 2 个没人调用），
#   routers/news.py 同时 import 两个模块、一个函数从这儿取、另一个从那儿取。
#   后果是读代码的人会以为"有两个不同的实现"，改错地方就白发一次版。
#   现在合并成本文件：**带缓存的那份留下，纯数据库那份只在"不该缓存"的地方留下**。
#
# 【缓存策略】只有 3 个函数带缓存，另外 3 个故意不带：
#   带缓存（读多写少）：分类、新闻列表、新闻详情、相关新闻
#   不带缓存：
#     新闻总数   —— 会随增删变，缓存了容易和列表对不上，而且 count 本身很快
#     浏览量 +1  —— 它就是个写操作，缓存毫无意义
# ============================================================

from fastapi.encoders import jsonable_encoder
# jsonable_encoder：把 ORM 对象转成能塞进 Redis 的字典

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
    # 先尝试从缓存中获取数据
    cached_categories = await get_cached_categories()
    if cached_categories:
        return cached_categories

    stmt = select(Category).order_by(Category.sort_order).offset(skip).limit(limit)
    # order_by(sort_order)：表里本来就有这个字段专门用来排序，之前没写 → 顺序不可控。
    #   现在数据的 sort_order 和 id 顺序刚好一致，所以这个改动不会改变现有显示，
    #   但它把"顺序"从"碰运气"变成"确定的"
    result = await db.execute(stmt)
    categories = result.scalars().all()  # ORM

    # 写入缓存
    if categories:
        categories = jsonable_encoder(categories)
        await set_cache_categories(categories)

    # 返回数据
    return categories


async def get_news_list(db: AsyncSession, category_id: int, skip: int = 0, limit: int = 10):
    # 先尝试从缓存获取新闻列表
    # 跳过的数量skip = (页码 -1) * 每页数量 → 页码 = 跳过的数量 // 每页数量 + 1
    page = skip // limit + 1
    cached_list = await get_cache_news_list(category_id, page, limit)  # 缓存数据 json
    if cached_list:
        return [News(**item) for item in cached_list]

    # 查询的是指定分类下的新闻
    stmt = (
        select(News)
        .where(News.category_id == category_id)
        .order_by(News.publish_time.desc(), News.id.desc())
        .offset(skip)
        .limit(limit)
    )
    # ⚠️ order_by 之前是缺的，这是个真问题，分两点说：
    #   ① 分页会错：MySQL 对"没有 ORDER BY 的 LIMIT/OFFSET"**不保证顺序**，
    #      数据量变大、执行计划变化（换了索引）时，同一页可能重复出现某条、
    #      另一条却永远翻不到。现在数据少看不出问题，属于"埋着的雷"。
    #   ② 顺序语义不对：新闻列表应该"最新在前"，不写就是按底层扫描顺序（实际约等于 id 升序 = 最旧在前）。
    #   用 id 做第二排序键，是为了让 publish_time 相同的记录也有确定顺序（否则仍不稳定）。
    result = await db.execute(stmt)
    news_list = result.scalars().all()

    # 写入缓存
    if news_list:
        # ORM 转成 Pydantic，再转为字典（by_alias=False 保持数据库字段名，Redis 里给后端自己用）
        news_data = [NewsItemBase.model_validate(item).model_dump(mode="json", by_alias=False) for item in news_list]
        await set_cache_news_list(category_id, page, limit, news_data)

    return news_list


async def get_news_count(db: AsyncSession, category_id: int):
    # 查询的是指定分类下的新闻数量
    stmt = select(func.count(News.id)).where(News.category_id == category_id)
    result = await db.execute(stmt)
    return result.scalar_one()  # 只能有一个结果，否则报错


async def get_news_detail(db: AsyncSession, news_id: int):
    # 先尝试从缓存获取
    cached_news = await get_cached_news_detail(news_id)
    if cached_news:
        return News(**cached_news)

    stmt = select(News).where(News.id == news_id)
    result = await db.execute(stmt)
    news = result.scalar_one_or_none()

    # 如果查询到数据，存入缓存（不使用别名，保持数据库字段名）
    if news:
        news_dict = NewsDetailResponse.model_validate(news).model_dump(
            by_alias=False, mode="json", exclude={'related_news', 'views'}
        )
        # exclude={'related_news'}：相关新闻是单独查、单独缓存的，
        #   不排除的话会缓存一份冗余数据（而且 News 模型没有这个字段，回读时会报错）
        # exclude={'views'}（2026-09-21 加）：浏览量每被看一次就 +1，而这份缓存要活 300 秒，
        #   两者天生冲突 —— 存进去的那一刻它就已经旧了。以前没排除，
        #   接口返回的就是这个旧值（点进去 12579、刷新变 12578、然后 5 分钟不动）。
        #   现在浏览量不进缓存，由 get_news_views() 实时查。
        await cache_news_detail(news_id, news_dict)

    return news


async def increase_news_views(db: AsyncSession, news_id: int):
    # 浏览量 +1。
    # 这里必须显式带上 updated_at=News.updated_at，原因是：
    #   models/news.py 里给 updated_at 配了 onupdate=datetime.now，
    #   而列级的 onupdate 对 update() 语句也同样生效 ——
    #   不写这一句的话，用户每看一次新闻，"更新时间"就被改一次（语义完全错了）
    #   显式给了值之后，SQLAlchemy 就按我们给的值写，不再触发 onupdate
    stmt = (
        update(News)
        .where(News.id == news_id)
        .values(views=News.views + 1, updated_at=News.updated_at)
    )
    result = await db.execute(stmt)
    await db.commit()

    # 更新 → 检查数据库是否真的命中了数据 → 命中了返回 True
    return result.rowcount > 0


async def get_news_views(db: AsyncSession, news_id: int):
    # 只查浏览量，**故意不走缓存**（2026-09-21 新增）。
    # 为什么必须单独查、而不是用 increase_news_views 的结果：
    #   它执行的是 UPDATE，MySQL 的 UPDATE 没有 RETURNING，
    #   拿到的 rowcount 只是"改了几行"，不是"改完是多少"。
    # 为什么浏览量不能进缓存：它是全项目变化最快的字段（每看一次 +1），
    #   而详情缓存有 300 秒 TTL —— 高频字段 + 长 TTL = 必然返回旧值。
    #   标题、正文这些不常变的仍然走缓存，缓存收益一点没丢。
    stmt = select(News.views).where(News.id == news_id)
    return await db.scalar(stmt)


async def get_related_news(db: AsyncSession, news_id: int, category_id: int, limit: int = 5):
    cached_related = await get_cached_related_news(news_id, category_id)
    if cached_related:
        # 缓存数据是字典列表，直接返回
        return cached_related

    # 同分类、排除自己；先按浏览量（热门优先），再按发布时间
    stmt = select(News).where(
        News.category_id == category_id,
        News.id != news_id
    ).order_by(
        News.views.desc(),  # 默认是升序，desc 表示降序
        News.publish_time.desc()
    ).limit(limit)
    result = await db.execute(stmt)
    related_news = result.scalars().all()

    # 转换为字典格式用于缓存和返回（不使用别名，保持数据库字段名）
    if related_news:
        related_data = [
            RelatedNewsResponse.model_validate(news).model_dump(by_alias=False, mode="json")
            for news in related_news
        ]
        await cache_related_news(news_id, category_id, related_data)
        return related_data

    # 没有相关新闻，返回空列表
    return []
