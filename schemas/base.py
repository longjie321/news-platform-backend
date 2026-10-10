from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class NewsItemBase(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    image: Optional[str] = None
    author: Optional[str] = None
    category_id: int = Field(alias="categoryId")
    views: int
    publish_time: Optional[datetime] = Field(None, alias="publishTime")
    # ⚠️ 别名必须是 publishTime（以前写的是 publishedTime，是个真 bug）：
    #   新闻列表接口手写字典里的字段是 publishTime，而收藏/历史列表走这个模型，
    #   于出现"同一个字段两个名字"——前端 Favorite.vue / History.vue 读的都是
    #   item.publishTime，所以这两个页面的"发布时间"一直是空的。
    #   （这和他之前修过的"新闻列表发布时间为空"是同一类问题，只是漏在了这两个接口）
    # 另外：jsonable_encoder 默认 by_alias=True，这里填什么，接口就返回什么。

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )
