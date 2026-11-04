# 区域：RAG 核心逻辑的单元测试
# 作用：不连数据库、不调外部接口，纯逻辑验证（毫秒级跑完）

import crud.rag as rag
from routers.ragchat import build_messages
from utils.splitter import split_text


# ---------- 一、切块 ----------

def test_短文本不切块():
    """比 CHUNK_SIZE 短的文本应该原样返回一块"""
    assert split_text("这是一句很短的话。", chunk_size=500) == ["这是一句很短的话。"]


def test_空输入返回空列表():
    """空字符串 / None / 纯空白都返回空列表，不能报错"""
    assert split_text("") == []
    assert split_text(None) == []


def test_长文本会被切成多块():
    """超长文本切出的每块都不能超过设定大小"""
    chunks = split_text("这是第一句话。" * 60, chunk_size=100, chunk_overlap=0)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


def test_切块不丢内容():
    """关掉重叠时，所有块拼起来应该等于原文（切块是无损的）"""
    text = "人工智能正在改变世界。" * 50
    assert "".join(split_text(text, chunk_size=120, chunk_overlap=0)) == text


# ---------- 二、内容指纹（增量索引的依据） ----------

def test_相同内容指纹相同():
    assert rag.fingerprint("同一段文字") == rag.fingerprint("同一段文字")


def test_内容变了指纹就变():
    """否则改过的新闻不会被重新向量化"""
    assert rag.fingerprint("原文内容") != rag.fingerprint("原文内容改了")


# ---------- 三、检索模式判断（这次改动的核心） ----------

def _fake_retrieve(monkeypatch, score):
    """把"向量化"和"向量库检索"都换成假的，只测模式判断逻辑"""
    monkeypatch.setattr(rag, "embed_texts", lambda texts: [[0.1, 0.2]])
    monkeypatch.setattr(rag.vectorstore, "search", lambda v, k: [{"text": "x", "meta": {}, "score": score}])


async def test_相似度够高走rag模式(monkeypatch):
    """检索到相关新闻 → rag 模式（带上资料回答）"""
    _fake_retrieve(monkeypatch, 0.80)

    result = await rag.retrieve("梅西世界杯")
    assert result["mode"] == "rag"
    assert len(result["hits"]) == 1


async def test_相似度低走ai模式(monkeypatch):
    """没检索到相关内容 → ai 模式（当普通 AI 回答，而不是拒答）"""
    _fake_retrieve(monkeypatch, 0.10)

    result = await rag.retrieve("红烧肉怎么做")
    assert result["mode"] == "ai"
    assert result["hits"][0]["score"] == 0.10


async def test_向量库为空时也走ai模式(monkeypatch):
    """库里一条都没有时不能报错，应该老老实实当普通 AI 用"""
    monkeypatch.setattr(rag, "embed_texts", lambda texts: [[0.1]])
    monkeypatch.setattr(rag.vectorstore, "search", lambda v, k: [])

    result = await rag.retrieve("随便问点什么")
    assert result["mode"] == "ai"
    assert result["top_score"] == 0.0


# ---------- 四、寒暄短路 ----------

def test_打招呼走ai模式():
    """打招呼不用查新闻库（实测"你好"和新闻的相似度能到 0.46，光靠阈值挡不住）"""
    assert rag.is_small_talk("你好") is True
    assert rag.is_small_talk("你好，你是谁") is True
    assert rag.is_small_talk("hello") is True
    assert rag.is_small_talk("谢谢") is True


def test_正常提问不会被当成寒暄():
    """查新闻的问题不能被误判成寒暄，否则永远拿不到资料"""
    assert rag.is_small_talk("梅西在世界杯上表现怎么样") is False
    assert rag.is_small_talk("科技类新闻有什么新进展") is False


async def test_寒暄直接走ai模式(monkeypatch):
    """寒暄时压根不该调向量库（省一次检索）"""
    called = {"count": 0}

    def boom(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("不该走到向量检索")

    monkeypatch.setattr(rag, "embed_texts", boom)
    result = await rag.retrieve("你好")

    assert result["mode"] == "ai"
    assert called["count"] == 0


# ---------- 五、提示词组装 ----------

def test_有资料时提示词带编号和来源():
    """资料要编号，模型才能写"根据资料1"这种可追溯的引用"""
    hits = [
        {"text": "梅西梅开二度", "meta": {"title": "梅西晋级决赛", "category": "体育"}, "score": 0.8},
        {"text": "C罗回归曼联", "meta": {"title": "C罗首秀破门", "category": "体育"}, "score": 0.7},
    ]
    messages = build_messages("梅西表现", hits, None)

    assert messages[0]["role"] == "system"
    assert "[资料1]" in messages[0]["content"]
    assert "[资料2]" in messages[0]["content"]
    assert "梅西晋级决赛" in messages[0]["content"]
    assert messages[-1]["content"] == "梅西表现"


def test_没有资料时提示词不带资料段():
    """走 ai 模式时不该出现"新闻资料"，否则模型会去硬扯"""
    messages = build_messages("你好", [], None)

    assert "新闻资料" not in messages[0]["content"]
    assert len(messages) == 2


def test_多轮历史顺序正确():
    """顺序必须是 system → 历史 → 本次问题"""
    from schemas.ai import ChatMessage

    history = [
        ChatMessage(role="user", content="上一句"),
        ChatMessage(role="assistant", content="上一答"),
    ]
    messages = build_messages("这一句", [], history)

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "这一句"


# 结构化意图识别（2026-09-20 新增）
def test_统计类问题能识别出来():
    """问数量、问最长、问分类分布，都要被认出来"""
    assert rag.detect_structured_intent("你一共有多少篇新闻") == "count"
    assert rag.detect_structured_intent("一共有多少条新闻") == "count"
    assert rag.detect_structured_intent("科技类有多少篇新闻") == "count"

    assert rag.detect_structured_intent("最长的一篇新闻是什么") == "longest"
    assert rag.detect_structured_intent("现在最长的呢") == "longest"

    assert rag.detect_structured_intent("各分类分别有多少篇") == "distribution"


def test_最长优先于多少():
    """
    "最长的那篇新闻有多少字"同时含"最长"和"多少"，必须按"最长"处理。

    ★ 这条钉的是【判断顺序】—— 偏好更具体的意图。
      顺序写反的话，它会被当成"问总数"，答出"一共 403 篇"这种驴唇不对马嘴的结果。
    """
    assert rag.detect_structured_intent("最长的那篇新闻有多少字") == "longest"


def test_普通问题不能误判成统计():
    """
    ★ 反向验证：这些是能走向量检索的正常问题，不能被抢走。

    最要盯的是"一共有哪些科技新闻" —— 它含"一共"两个字，
    但问的是【列表】不是【数量】。早期版本的判断里有"一共"，
    就会把它误判成统计，答非所问。
    """
    assert rag.detect_structured_intent("一共有哪些科技新闻") == ""
    assert rag.detect_structured_intent("帮我找人工智能的新闻") == ""
    assert rag.detect_structured_intent("介绍几个体育新闻") == ""
    assert rag.detect_structured_intent("你好") == ""
    assert rag.detect_structured_intent("今天天气怎么样") == ""
    assert rag.detect_structured_intent("") == ""
