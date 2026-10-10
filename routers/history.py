from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from config.db_conf import get_db
from crud import history
from models.users import User
from schemas.history import HistoryAddRequest, HistoryNewsItemResponse, HistoryListResponse
from utils.auth import get_current_user
from utils.response import success_response

# 路由前缀: /api/history
router = APIRouter(prefix="/api/history", tags=["history"])


# 添加历史记录: 需要登录(token) + 请求体传 newsId
@router.post("/add", summary="Add browsing history")
async def add_history(data: HistoryAddRequest,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """
    添加历史记录
    """
    # user.id: 当前登录用户ID; data.news_id: 前端传的新闻ID
    result = await history.add_history(db, user.id, data.news_id)
    return success_response(message="添加成功", data=result)


# 获取历史记录列表: 分页
@router.get("/list", summary="List browsing history (paginated)")
async def get_history_list(page: int = Query(1, ge=1),
                           page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
                           user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """
    获取历史记录列表
    """
    # rows: (新闻对象, 浏览时间, 历史记录ID) 元组列表; total: 总条数
    rows, total = await history.get_history_list(db, user.id, page, page_size)

    # 是否还有下一页: 总条数 > 当前页已展示的条数
    has_more = total > page * page_size

    # 把每行组装成响应模型: 新闻字段 + 浏览时间 + 历史记录ID
    history_list = [HistoryNewsItemResponse.model_validate({
        **news.__dict__,
        "view_time": view_time,
        "history_id": history_id
    }) for news, view_time, history_id in rows]

    # 封装响应数据: 列表 + 总数 + 是否还有更多
    data = HistoryListResponse(list=history_list, total=total, hasMore=has_more)

    return success_response(message="获取浏览历史成功", data=data)


# 删除单条历史记录: 路径参数 history_id
@router.delete("/delete/{history_id}", summary="Delete one history record")
async def delete_history(history_id: int,
                         user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """
    删除历史记录
    """
    result = await history.delete_history(db, user.id, history_id)
    if not result:
        # 没删到 -> 记录不存在, 返回404
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="历史记录不存在")
    return success_response(message="删除成功")


# 清空历史记录
@router.delete("/clear", summary="Clear all browsing history")
async def clear_history(user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """
    清空历史记录
    """
    result = await history.clear_history(db, user.id)
    # result 是删掉的条数，顺手告诉用户"清掉了多少条"，比干巴巴的"清空成功"有用
    return success_response(message=f"已清空 {result} 条浏览记录")
