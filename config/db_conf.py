from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine

import os

# 数据库URL：优先读环境变量（生产/容器部署用），本地开发用默认值
# 例：docker run -e DATABASE_URL="mysql+aiomysql://root:密码@host.docker.internal:3306/news_app?charset=utf8mb4"
ASYNC_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+aiomysql://root:123456@localhost:3306/news_app?charset=utf8mb4"
)

# 是否打印 SQL 日志
#   默认 **关**（安全 + 性能优先）：每条 SQL 都打印很影响性能，
#   而且日志里会带上真实的表名和字段名，等于把表结构写进日志文件。
#   想看 SQLAlchemy 到底生成了什么 SQL 时，在 .env 里显式写 SQL_ECHO=true。
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"

# 创建异步引擎
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=SQL_ECHO,    # 是否输出SQL日志（见上面 SQL_ECHO）
    pool_size=10,     # 设置连接池中保持的持久连接数
    max_overflow=20   # 设置连接池允许创建的额外连接数
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)


# 依赖项，用于获取数据库会话
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
