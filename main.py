# ============================================================
# 区域：应用入口（main.py）
# 作用：创建 FastAPI 应用 → 注册异常处理 → 配置跨域 → 挂上各业务路由
#   整个项目只有这一处"总装车间"，其他文件都是零件
#
# 关键理解（面试会问）：
#   ① CORS 不能用 allow_origins=["*"] 同时开 allow_credentials=True ——
#      浏览器规范明确禁止这种组合，会让你排查半天"为什么跨域失败"
#   ② uvicorn 用 "main:app" 字符串形式启动（而不是直接传 app 对象），
#      这样 reload=True 热重载才生效；生产环境要关掉 reload 并用多个 worker
#   ③ 异常处理器要"子类在前、父类在后"注册，否则会被父类的兜底逻辑抢先捕获
# ============================================================

from fastapi import FastAPI
# FastAPI：框架主类

from fastapi.middleware.cors import CORSMiddleware
# CORSMiddleware：跨域中间件（前端 5173 要访问后端 8000，必须开）

from routers import news, users, favorite, history, aichat, ragchat
# 六个业务路由模块

from utils.exception_handlers import register_exception_handlers
# 注册全局异常处理器

app = FastAPI(
    title="Toutiao News Backend",
    description=(
        "FastAPI 新闻后端：新闻浏览、用户认证、收藏、浏览历史、"
        "AI 问答、RAG 新闻知识库问答（向量检索 + 带来源回答）"
    ),
    version="1.2.0",
)
# 实例化后端应用

register_exception_handlers(app)
# 注册异常处理器（HTTPException / IntegrityError / SQLAlchemyError / Exception 四层）

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    # 本地前端 dev server 的地址；生产部署时改成线上域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/hello", summary="Health Check", description="Return a success message if the service is running")
async def hello():
    return {"msg": "FastAPI服务启动成功"}


app.include_router(news.router)
app.include_router(users.router)
app.include_router(favorite.router)
app.include_router(history.router)
app.include_router(aichat.router)
app.include_router(ragchat.router)
# RAG 知识库问答：/api/rag/chat（智能问答）、/api/rag/stats（统计）


if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
