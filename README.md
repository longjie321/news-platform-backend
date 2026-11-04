# 新闻资讯平台后端（含 RAG 知识库问答）

FastAPI 写的新闻后端，包含用户体系、新闻业务和一个 RAG 知识库问答模块。

## 技术栈

FastAPI · SQLAlchemy 2.0 异步 · MySQL · Redis · ChromaDB · bge-m3（硅基流动）· DeepSeek · SSE

## 怎么跑

```bash
pip install -r requirements.txt
cp .env.example .env          # 填两把 key：DEEPSEEK_API_KEY、EMBEDDING_API_KEY

python scripts/ingest_news.py # 把新闻灌进向量库（第一次必须跑）
python main.py                # 启动，接口文档 http://127.0.0.1:8000/docs
```

前端在另一个项目 `xwzx-news`：`npm run dev` → http://localhost:5173/aichat

跑测试：

```bash
pip install -r requirements-dev.txt

python -m pytest tests -q          # 单元测试：离线、1 秒、不联网
python tests/check_api.py          # 接口冒烟：需要服务已启动
python tests/eval_retrieval.py     # 检索评测：真向量化，几十秒

# 手动冒烟（按需跑，需要 Redis / 网络）
python tests/smoke_cache.py        # 验 Redis 三类缓存的读写
python tests/smoke_deepseek.py     # 验 DeepSeek 连通性

python scripts/redis_visualizer.py # 起 8008，用浏览器看 Redis 里存了什么
```

## RAG 模块

```
用户问题 → 向量化(bge-m3) → ChromaDB 找最像的 4 块新闻 → 判断：
                                                        ├ 相似度达标 → 拿新闻当资料回答，并标来源
                                                        └ 不达标     → 按普通 AI 回答
                                                        ↓
前端 ←── SSE 流式 ←── DeepSeek 生成 ←── 拼提示词（资料 + 多轮历史）
```

两种模式自动切换：

| 模式 | 判断条件 | 效果 |
|---|---|---|
| `rag` | 相似度 ≥ 0.55 | 基于新闻回答，返回来源卡片 |
| `ai` | 相似度不够 / 打招呼 | 按普通 AI 回答，不塞无关新闻 |

阈值不够时选择降级而不是拒答，原因是拒答体验差（用户打招呼也会被拦）。
降级之后，它平时就是个普通助手，只有问到新闻时才带上资料。

## 目录

```
main.py                  应用入口：异常处理、CORS、挂载路由
config/                  配置层（db_conf / ai_conf / rag_conf / cache_conf）
crud/                    数据层：SQL 与业务查询（news / users / favorite / history / rag）
models/                  ORM 模型（news / user / favorite / history）
routers/                 接口层（news / users / favorite / history / aichat / ragchat）
schemas/                 Pydantic 出入参校验
utils/                   通用工具（auth / exception / exception_handlers / embedding / splitter / vectorstore）
cache/                   Redis 缓存封装
scripts/
  ingest_news.py         建索引脚本（离线，支持增量 / --rebuild 全量重建）
  redis_visualizer.py    缓存可视化面板（调试用，只读，起 8008 端口）
tests/
  conftest.py            测试公共配置（把项目根加进 import 路径）
  test_rag.py            18 个单元测试（离线、换掉外部依赖、1 秒跑完）
  check_api.py           接口冒烟（需要服务已启动）
  eval_retrieval.py      检索质量评测（真向量化，量 recall@4 和阈值拐点）
  smoke_cache.py         Redis 缓存冒烟（手动执行）
  smoke_deepseek.py      DeepSeek 连通性冒烟（手动执行）
docs/                    个人学习笔记（不入库、不进镜像）
```

测试分两类：`test_*.py` 把外部依赖换成假的，只验逻辑，毫秒级、不联网；
`smoke_*.py`、`check_api.py`、`eval_retrieval.py` 真连外部服务，验链路和效果，手动执行。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/rag/chat` | 智能问答（SSE 流式，自动切模式） |
| GET | `/api/rag/stats` | 知识库有多少块 |
| POST | `/api/aichat` | 普通 AI 问答（不查库，做对比用） |

## 几个实现细节

**检索阈值 0.55**：正样本最低 0.5624，负样本最高 0.6141，两类有重叠，阈值定在哪里都会判错一部分。
低于阈值时降级为普通回答，代价只是没带来源，不是答不出来。
0.55 是拿 20 条正样本、18 条负样本扫出来的，总正确率 97%。

**增量索引**：按内容 md5 判断是否变化，没变就跳过。403 篇全量重建要几分钟，增量几秒。
有两个地方要注意：内容变了必须先删旧块，否则会留下旧版本的块；清理孤儿数据只能针对 `news:` 前缀，
不然会误删用户上传的文档。

**SSE 事件**：自定义 4 类事件（mode / delta / done / error），`mode` 先于正文下发，前端可以先渲染来源卡片。

**Redis 快速失败**：连接超时设 0.2 秒。不设的话走系统默认超时，Redis 一挂每个接口要卡 8 秒
（单请求计时 8.1s → 0.42s）。

**流式接口的错误处理分两层**：进流之前抛 HTTPException；进流之后只能发 error 事件，
因为响应头已经发出去了，状态码改不了。

**分页参数要设下限**：`page` 不加 `ge=1` 时，前端传 `page=0` 会算出负偏移，
SQL 变成 `LIMIT -3, 3`，MySQL 直接语法错误；`pageSize=0` 还会除零。

**列级 `onupdate` 也会被 `update()` 触发**：浏览量 +1 时如果不显式带上 `updated_at`，
"更新时间"会被一起改掉，一篇发布时间 2024 的文章点一下就变成今天更新。

## 已知限制

- 新闻是静态快照（403 篇），问"今天有什么新闻"答不上来
- 索引靠脚本手动同步，没有定时任务
- RAG 接口没有限流，允许匿名调用
- 表结构用 `create_all` 管理，没接 Alembic（改字段要手工 ALTER）
- 登录 token 只存一份：同一账号再登录会把上一个顶下线
- 新闻配图是前端本地生成的色块（原来用国外图床，加载慢且容易失败）
