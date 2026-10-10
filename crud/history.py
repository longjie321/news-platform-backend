from datetime import datetime

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.history import History
from models.news import News


# 添加历史记录: 先查有没有浏览过 -> 浏览过更新浏览时间；没浏览过新增一条
async def add_history(db: AsyncSession, user_id: int, news_id: int):
    """
    添加历史记录
    """
    # 查一下: 当前用户是否浏览过这条新闻
    query = select(History).where(History.user_id == user_id, History.news_id == news_id)
    result = await db.execute(query)
    existing_history = result.scalar_one_or_none()

    if existing_history:
        # 浏览过 -> 只更新浏览时间（同一用户对同一新闻只保留一条记录）
        existing_history.view_time = datetime.now()
        await db.commit()
        await db.refresh(existing_history)
        return existing_history
    else:
        # 没浏览过 -> 新增一条浏览记录
        history = History(user_id=user_id, news_id=news_id)
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return history


# 获取浏览历史列表: 分页 + 联表查询
async def get_history_list(db: AsyncSession, user_id: int, page: int = 1, page_size: int = 10):
    # 分页偏移量: 第1页从0开始，第2页跳过10条
    offset = (page - 1) * page_size

    # 统计当前用户的浏览历史总数（前端用于判断是否还有下一页）
    count_query = select(func.count(History.id)).where(History.user_id == user_id)
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    # 联表查询: News JOIN History -> 取出 新闻对象 + 浏览时间 + 历史记录ID
    # 按浏览时间倒序(最新浏览的排最前) + 分页
    query = (select(News, History.view_time.label("view_time"), History.id.label("history_id"))
             .join(History, History.news_id == News.id)
             .where(History.user_id == user_id)
             .order_by(History.view_time.desc())
             .offset(offset).limit(page_size))

    result = await db.execute(query)
    rows = result.all()
    return rows, total


# 删除历史记录: 按 用户ID + 历史记录ID 删除（history_id 是 history 表的主键）
async def delete_history(db: AsyncSession, user_id: int, history_id: int):
    """
    删除历史记录
    """
    # delete: 按 用户ID + 历史记录ID 两个条件删除
    # 注意: 条件是 History.id，不是 news_id！之前这里写错会导致删错记录
    query = delete(History).where(History.user_id == user_id, History.id == history_id)
    result = await db.execute(query)
    await db.commit()

    # rowcount: 删除命中了多少行 -> 大于0说明确实删到了
    return result.rowcount > 0


# 清空历史记录: 删除当前用户的所有浏览记录
async def clear_history(db: AsyncSession, user_id: int):
    """
    清空历史记录
    """
    # delete: 只按 用户ID 删除，不限制新闻
    query = delete(History).where(History.user_id == user_id)
    result = await db.execute(query)
    await db.commit()

    # rowcount or 0: 返回删了多少条，没有删到就返回0
    return result.rowcount or 0
