from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict

from schemas.base import NewsItemBase


# 添加历史记录: 请求模型（前端传的）
class HistoryAddRequest(BaseModel):
    """
    添加历史记录请求
    """
    # alias: 前端传 newsId（驼峰）也能识别，不会422
    news_id: int = Field(..., alias="newsId")

    # populate_by_name: 让 news_id 和 newsId 两种写法都能收
    #   不加的话，传 {"news_id": 1} 会直接 422 —— 和收藏接口行为不一致（收藏接口两种都收）
    model_config = ConfigDict(populate_by_name=True)


# 浏览历史列表中的单条新闻项: 继承新闻基础字段 + 加浏览记录信息
class HistoryNewsItemResponse(NewsItemBase):
    """
    浏览历史列表中的新闻项响应
    """
    history_id: int = Field(alias="historyId")
    view_time: datetime = Field(alias="viewTime")

    # populate_by_name: 同时接受别名和字段名; from_attributes: 能从ORM对象取值
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True)


# 浏览历史列表: 响应模型
class HistoryListResponse(BaseModel):
    list: list[HistoryNewsItemResponse]
    total: int
    has_more: bool = Field(alias="hasMore")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True
    )
