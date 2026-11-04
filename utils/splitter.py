"""递归切块：把一篇长文切成若干小块，不用 LangChain。

思路是「先按段落切，块还大就换句号，再不行按字数硬切」。
注意：本项目 403 篇新闻最长只有 192 字，实际全部走「够短就直接返回」那一支，
递归切块这段代码没在真实数据上执行过。面试被问到要这么说，别吹。
"""

from config.rag_conf import CHUNK_SIZE, CHUNK_OVERLAP, CHUNK_SEPARATORS


def split_text(text: str,
               chunk_size: int = CHUNK_SIZE,
               chunk_overlap: int = CHUNK_OVERLAP,
               separators: list = None) -> list:
    """把文本切成小块。返回列表，调用方不用判断返回的到底是字符串还是列表。"""
    # 默认值写 None 而不是直接写 CHUNK_SEPARATORS：函数默认值只在定义时求值一次，
    # 如果默认是个列表、函数里又改过它，改动会被后面的调用继承
    if separators is None:
        separators = CHUNK_SEPARATORS

    # 传进来 None 或纯空白时先统一成空串，后面才能安全调 .strip()
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    # 从高优先级往后找第一个「文本里真的存在」的分隔符；
    # 一个都找不到时停在最后一项（空字符串），表示只能按字数硬切
    sep_index = len(separators) - 1
    for i, sep in enumerate(separators):
        if sep and sep in text:
            sep_index = i
            break
    separator = separators[sep_index] if separators else ""

    if separator:
        pieces = text.split(separator)
        # split 会把分隔符吃掉，这里拼回去，保住句末标点
        pieces = [p + separator for p in pieces[:-1]] + [pieces[-1]]
    else:
        # 没有任何符号可用，按固定长度硬切
        pieces = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    chunks = []
    current = ""
    for piece in pieces:
        if len(piece) > chunk_size:
            # 这一段自己就超长，先把手里攒的交出去，再换下一级分隔符递归
            if current:
                chunks.append(current)
                current = ""
            # 必须用 extend，append 会把整个列表塞成嵌套的一层
            chunks.extend(
                split_text(piece, chunk_size, chunk_overlap, separators[sep_index + 1:])
            )
        elif len(current) + len(piece) <= chunk_size:
            current += piece
        else:
            chunks.append(current)
            current = piece
    # 循环结束时手上还可能有最后一块
    if current:
        chunks.append(current)

    # 给相邻块加重叠：一句话正好被切在边界上时，下一块还能看到它的前半截
    if chunk_overlap > 0 and len(chunks) > 1:
        with_overlap = [chunks[0]]
        for i in range(1, len(chunks)):
            with_overlap.append(chunks[i - 1][-chunk_overlap:] + chunks[i])
        chunks = with_overlap

    return [c.strip() for c in chunks if c.strip()]
