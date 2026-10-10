from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config.db_conf import get_db
from crud import news
# 2026-09-15：原来这里是 news + news_cache 两个模块（各有一半同名死代码），
#   现在已经合并回 crud/news.py 一个模块了

# 创建 APIRouter 实例
# prefix 路由前缀（API 接口规范文档）
# tags 分组 标签
router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/categories", summary="List news categories")
async def get_categories(skip: int = 0, limit: int = Query(100, ge=1, le=200),
                         db: AsyncSession = Depends(get_db)):
    categories = await news.get_categories(db, skip, limit)
    # 只返回前端要用的字段，顺便统一成驼峰（原来返回的是 created_at / sort_order 这种下划线字段）
    return {
        "code": 200,
        "message": "获取新闻分类成功",
        "data": [{"id": c["id"], "name": c["name"]} for c in categories]
    }


@router.get("/list", summary="List news (paginated)")
async def get_news_list(
        category_id: int = Query(..., alias="categoryId"),
        page: int = Query(1, ge=1),
        # ge=1 必须加！原来没加，前端传 page=0 时 offset 会算成负数，
        # SQL 变成 "LIMIT -3, 3" → MySQL 语法错误 → 接口 500
        page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
        # ge=1 也必须加：pageSize=0 时算页数会除零，同样会 500
        db: AsyncSession = Depends(get_db)
):
    offset = (page - 1) * page_size
    news_list = await news.get_news_list(db, category_id, offset, page_size)
    total = await news.get_news_count(db, category_id)
    # (跳过的 + 当前列表里面的数量) < 总量
    has_more = (offset + len(news_list)) < total

    items = [{
        "id": n.id,
        "title": n.title,
        "description": n.description,
        "image": n.image,
        "author": n.author,
        "views": n.views,
        # 下面两个字段前端用的是驼峰（之前直接返回 ORM 对象，给出去的是 publish_time 下划线），
        # 字段名对不上 → 前端页面上"发布时间"一直是空的
        "categoryId": n.category_id,
        "publishTime": n.publish_time,
    } for n in news_list]
    # 顺便不再返回 content（正文全文）：列表页用不到，
    #   一页 10 条的全文加起来好几 KB，纯属浪费流量

    return {
        "code": 200,
        "message": "获取新闻列表成功",
        "data": {
            "list": items,
            "total": total,
            "hasMore": has_more
        }
    }


@router.get("/detail", summary="Get news detail (with related news)")
async def get_news_detail(news_id: int = Query(..., alias="id"), db: AsyncSession = Depends(get_db)):
    # 获取新闻详情 + 浏览量+1 + 相关新闻
    news_detail = await news.get_news_detail(db, news_id)
    if not news_detail:
        raise HTTPException(status_code=404, detail="新闻不存在")

    views_res = await news.increase_news_views(db, news_detail.id)
    if not views_res:
        raise HTTPException(status_code=404, detail="新闻不存在")

    # 浏览量必须在自增之后**重新查一次**，不能直接用 news_detail.views：
    #   详情走的是 300 秒缓存，缓存里那份浏览量是旧快照（现已从缓存里排除）。
    #   不重查的话，用户看到的是"点进去 12579、刷新变 12578、然后 5 分钟不动"。
    views = await news.get_news_views(db, news_detail.id)

    related_news = await news.get_related_news(db, news_detail.id, news_detail.category_id)

    return {
      "code": 200,
      "message": "success",
      "data": {
        "id": news_detail.id,
        "title": news_detail.title,
        "content": news_detail.content,
        "image": news_detail.image,
        "author": news_detail.author,
        "publishTime": news_detail.publish_time,
        "categoryId": news_detail.category_id,
        "views": views,
        "relatedNews": related_news
      }
    }
