# Toutiao News Backend

FastAPI 新闻后端：用户体系 + 新闻业务 + 一个「锦上添花」的 RAG 知识库问答模块。

## 技术栈

FastAPI · SQLAlchemy 2.0 异步 · MySQL · Redis · ChromaDB · bge-m3（硅基流动）· DeepSeek · SSE

## 怎么跑

```bash
pip install -r requirements.txt
cp .env.example .env          # 填两把 key：DEEPSEEK_API_KEY、EMBEDDING_API_KEY

python scripts/ingest_news.py # 把新闻灌进向量库（第一次必须跑）
python main.py                # 启动，接口文档 http://127.0.0.1:8000/docs
```

前端（另一个项目 `xwzx-news`）：`npm run dev` → http://localhost:5173/aichat

跑测试：

```bash
pip install -r requirements-dev.txt

python -m pytest tests -q          # 单元测试：离线、1 秒、不联网
python tests/check_api.py          # 接口冒烟：需要服务已启动
python tests/eval_retrieval.py     # 检索评测：真向量化，几十秒

# 手动冒烟（按需跑，需要 Redis / 网络）
python tests/smoke_cache.py        # 验 Redis 三类缓存的读写
python tests/smoke_deepseek.py     # 验 DeepSeek 连通性

python scripts/redis_visualizer.py # 起 8008，用浏览器看 Redis 里到底存了什么
```

## RAG 模块做了什么

```
用户问题 ─→ 向量化(bge-m3) ─→ ChromaDB 找最像的 4 块新闻 ─→ 判断：
                                                           ├ 相似度达标 → 把新闻当资料，让模型照着答并标来源
                                                           └ 不达标     → 当普通 AI 助手回答（不拒答）
                                                           ↓
前端 ←── SSE 流式（打字机） ←── DeepSeek 生成 ←── 拼提示词(资料+多轮历史)
```

**两种模式自动切换**是这个模块的核心设计：

| 模式 | 什么时候 | 效果 |
|---|---|---|
| `rag` | 问题跟新闻相似度 ≥ 0.55 | 基于新闻资料回答，返回来源卡片 |
| `ai` | 相似度不够 / 打招呼 | 当普通 AI 助手回答，不塞无关新闻 |

**为什么不做"拒答"**：拒答体验很差（用户打招呼都被拦）。改成降级之后，它平时就是个正常助手，
只有问到新闻里的事才自动带上资料 —— 这才是"锦上添花"而不是"只能查库"。

## 目录

```
main.py                  应用入口：注册异常处理、CORS、挂载路由
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
  test_rag.py            15 个单元测试（离线、换掉外部依赖、1 秒跑完）
  check_api.py           接口冒烟（真服务 + 真库 + 真 AI，需先启动 main.py）
  eval_retrieval.py      检索质量评测（真向量化，量 recall@4 与阈值拐点）
  smoke_cache.py         Redis 缓存冒烟（手动执行）
  smoke_deepseek.py      DeepSeek 连通性冒烟（手动执行）
docs/                    个人学习笔记（不入库、不进镜像）
```

**两类测试的分工**（面试容易被追问）：`test_*.py` 是单元测试，把外部依赖换成假的，
只验逻辑分支，毫秒级、不联网；`smoke_*.py`、`check_api.py`、`eval_retrieval.py`
都真连外部服务，验的是"链路通不通"和"效果好不好"，需要手动执行。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/rag/chat` | 智能问答（SSE 流式，自动切模式） |
| GET | `/api/rag/stats` | 知识库有多少块 |
| POST | `/api/aichat` | 普通 AI 问答（不查库，做对比用） |

## 面试可以讲的点

1. **两段式判断为什么比"硬阈值拒答"好** —— 实测发现"库里有答案"和"库里没答案"两类问题的相似度会重叠
   （正样本 0.5624~0.7812，负样本 0~0.6141，在 0.56~0.61 这一段交叉），所以无论阈值取多少都会判错一部分；
   改成"降级"之后，判错的代价从"答不出来"降到"没带来源"，体验差别很大。
   阈值 0.55 不是拍脑袋定的，是 `tests/eval_retrieval.py` 扫出来的拐点
   （20 条正样本 + 18 条负样本，扫 0.40~0.70 共 7 档，0.55 时总正确率 97%）。
   再往上抬到 0.60 就开始杀掉正样本（85%），往下压到 0.50 则负样本误判率翻倍。
2. **增量索引** —— 给每篇新闻算内容指纹（md5），没变就跳过。403 篇全量重建要几分钟，增量几秒。
   两个坑：内容变了要先删旧块（否则留下"僵尸块"）；清理孤儿只能针对 `news:` 前缀（否则会误删用户上传的文档）。
3. **SSE 事件设计** —— 自定义 4 类事件（mode / delta / done / error），
   `mode` 先于正文下发，前端能立刻渲染来源卡片。
4. **Redis 快速失败** —— 缓存不可用时给连接设 0.2 秒超时。
   不设的话用的是系统默认超时，Redis 一挂每个接口要卡 8 秒（实测 8.1s → 0.42s）。
5. **流式接口的错误处理分两层** —— 进流之前用 HTTPException；进流之后只能发 error 事件，
   因为 HTTP 状态码早就发出去了。
6. **分页参数必须设下限** —— `page` 不加 `ge=1`，前端传 `page=0` 时偏移量算成负数，
   SQL 变成 `LIMIT -3, 3`，MySQL 直接语法错误 500；`pageSize=0` 还会除零。
7. **列级 `onupdate` 也会被 `update()` 语句触发** —— 浏览量 +1 时如果没显式带上 `updated_at`，
   "更新时间"会被顺手改掉（实测：看一下新闻，发布时间 2024 的文章"更新时间"变成刚才）。

## 已知限制

- 新闻是静态快照（403 篇），问"今天有什么新闻"答不上来
- 索引靠脚本手动同步，没有定时任务
- RAG 接口没有限流，允许匿名调用
- 表结构用 `create_all` 管理，没接 Alembic（改字段要手工 ALTER）
- 登录 token 只存一份：同一账号再登录会把上一个顶下线
- 新闻配图是前端本地生成的色块（原来用国外图床，加载慢且容易失败）
