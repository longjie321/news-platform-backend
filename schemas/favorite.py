from pydantic import BaseModel, Field, ConfigDict
from schemas.base import NewsItemBase
from datetime import datetime


class FavoriteCheckResponse(BaseModel):
    is_favorite: bool = Field(..., alias="isFavorite")


class FavoriteAddRequest(BaseModel):
    # 让模型同时接受 news_id 和 newsId，前端发驼峰不会 422
    model_config = ConfigDict(populate_by_name=True)

    news_id: int = Field(..., alias="newsId")


# 规划两个类： 一个是新闻模型类 + 收藏的模型类
class FavoriteNewsItemResponse(NewsItemBase):
    favorite_id: int = Field(alias="favoriteId")
    favorite_time: datetime = Field(alias="favoriteTime")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True
    )


# 收藏列表接口响应模型类
class FavoriteListResponse(BaseModel):
    list: list[FavoriteNewsItemResponse]
    total: int
    has_more: bool = Field(alias="hasMore")

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True
    )

