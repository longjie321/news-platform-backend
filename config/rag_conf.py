"""RAG 的集中配置：向量化、切块、检索参数。

换 EMBEDDING_MODEL 必须重建索引（python scripts/ingest_news.py --rebuild）——
不同模型算出来的向量不在同一个坐标系，新旧混着比距离是错的。
"""

import os

from dotenv import load_dotenv

load_dotenv()

# 向量化走硅基流动的 API，不在本地跑模型（原因见 utils/embedding.py 文件头）
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

# 项目根目录，后面所有路径都基于它
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_chroma_dir() -> str:
    """算出向量库目录，返回绝对路径。

    必须用绝对路径：chromadb 拿到相对路径时，是按「当前工作目录」解析的；
    而 scripts/ingest_news.py 支持从任意目录执行。两件事一撞就会出现
    「索引提示新增成功、服务读的却是另一个库」这种假成功。
    """
    env_dir = os.getenv("CHROMA_PERSIST_DIR")
    if not env_dir:
        return os.path.join(BASE_DIR, "rag_data")
    if os.path.isabs(env_dir):
        return env_dir
    # 环境变量给的是相对路径时，按「相对项目根」处理，而不是相对当前目录
    return os.path.join(BASE_DIR, env_dir)


CHROMA_DIR = _resolve_chroma_dir()

# 向量库里的集合名（地位等于 MySQL 的表）。改这个字符串 = 服务去读一个空集合
COLLECTION_NAME = "news_chunks"

# 切块：新闻正文最长 192 字，400 已经够用；重叠 50 防止句子正好断在边界上
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50
# 优先按段落切，再按行、句子、词，最后的空串表示「按字数硬切」
CHUNK_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]

# 每次检索取前 4 块。3~5 是常见区间，再多既费钱又会干扰模型
TOP_K = 4

# 相似度阈值：低于它就当「库里没有相关内容」，降级成普通 AI 回答，而不是拒答。
# 0.55 是扫出来的：20 条正样本 + 18 条负样本，逐档测——
#   0.45 → 82% / 0.50 → 87% / 0.55 → 97%（拐点）/ 0.60 → 90%
# 正样本最低 0.5624，取 0.55 一条都不丢；负样本最高 0.6141，所以仍有重叠带，
# 判错的部分靠提示词「资料无关就用自己的知识答」和寒暄短路兜底。
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.55"))
