# ============================================================
# 区域：检索质量评测（离线评测脚本，不是接口）
# 作用：用一批「有标准答案」的问题去量 RAG 到底准不准，回答三个必问题：
#   ① 检索准不准          -> recall@4（正确答案有没有落在检索出来的前 4 块里）
#   ② 阈值 0.5 定得对不对 -> 对 0.45/0.50/0.55 做扫描，看正负样本的模式判定正确率
#   ③ 改完东西怎么知道变好了 -> 重跑这个脚本，对比两次的数字
#
# 和 tests/test_rag.py 的区别：
#   test_rag.py       是单元测试：把外部依赖换成假的，只验逻辑分支（毫秒级、不联网）
#   eval_retrieval.py 是评测：真调向量化接口、真查向量库，量的是「效果」（几十秒）
#
# 用法（项目根目录执行）：
#   .venv\Scripts\python.exe tests\eval_retrieval.py
#
# 两个指标的区别（面试爱问）：
#   recall@4  -> 只管「检索出来的东西对不对」，与阈值无关，反映向量检索本身的能力
#   模式判定  -> 管「这次该不该用资料」，与阈值强相关，反映阈值定得准不准
# ============================================================

import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 本脚本在 tests/ 子目录里，不加这句 import 不到 config、crud 这些包

from config.db_conf import async_engine
# 脚本退出前要关掉连接池

from crud.rag import retrieve
# retrieve：真实的检索链路（向量化 + 查库 + 判断模式）

TOP_K = 4
# 和线上一致：一次取 4 块

THRESHOLDS = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
# 要扫描的阈值候选：跨度拉大一点，才能看出「提高阈值」的代价在哪一步开始出现

# ------------------------------------------------------------
# 一、正样本：(用户问题, 期望命中的新闻标题关键词)
#   问法是故意「换个说法」的，不照抄标题用词 —— 否则问题里带着标题原词，
#   向量一定很像，分数虚高，测出来的数字没意义
# ------------------------------------------------------------
POSITIVE = [
    # ---------- 直接表述（问句里带关键词，简单样本） ----------
    ("有没有AI看病的系统拿到美国批准了？", "FDA认证"),
    ("谷歌在医疗影像这块发布了什么产品？", "谷歌发布AI医疗影像系统"),
    ("女排打日本那场比赛结果怎么样？", "中国女排"),
    ("谷爱凌最近又添了什么荣誉？", "谷爱凌"),
    ("周杰伦发新专辑了吗？", "周杰伦"),
    ("泰勒·斯威夫特的巡演破了什么纪录？", "斯威夫特"),
    ("哈尔滨今年评的好人都有谁？", "冰城好人"),
    ("火星上是不是探测到水了？", "火星存在液态水"),
    ("《鱿鱼游戏》第二季有消息吗？", "鱿鱼游戏"),
    ("特斯拉的自动驾驶系统有什么更新？", "特斯拉自动驾驶"),
    ("微软推出什么AI写代码的工具了？", "AI编程助手"),
    ("国际空间站最近有什么商业活动？", "商业载人任务"),

    # ---------- 间接表述（问句里不含标题原词，困难样本） ----------
    ("球王带队打进决赛了吗？", "梅西"),
    ("那位瑞士网坛名将退出职业赛场了吗？", "费德勒"),
    ("那部讲高启强的剧，男主拿奖了吗？", "张颂文"),
    ("国产科幻大片续集有新物料吗？", "流浪地球3"),
    ("硅谷那家做手机的公司出AR眼镜了吗？", "AR眼镜"),
    ("做电动车那家的自动辅助驾驶有新版本吗？", "特斯拉自动驾驶"),
    ("有没有针对独居老人吃饭难的解决办法？", "社区食堂"),
    ("量子计算领域最近有什么大新闻？", "量子计算机"),
]

# ------------------------------------------------------------
# 二、负样本：库里根本没有答案的问题
# 作用是量「误判率」—— 这些问题被判成 rag 模式，就说明阈值定低了
# ------------------------------------------------------------
NEGATIVE = [
    "你好",
    "红烧肉怎么做才好吃？",
    "今天天气怎么样？",
    "Python 的装饰器怎么写？",
    "帮我写一首关于春天的诗",
    "明天股市会涨吗？",
    "北京到上海的高铁要坐多久？",
    "怎么保养笔记本电脑电池？",
    "1 加 1 等于几？",
    "推荐几本适合入门的小说",
    "我有点感冒，该吃什么药？",
    "介绍一下相对论",
    "怎么申请护照？",
    "现在几点了？",
    "教我用 Excel 做数据透视表",
    "什么是量子纠缠？",
    "附近哪里有好吃的火锅？",
    "帮我把这段话翻译成英文",
]
# 注意「什么是量子纠缠」是故意留的困难负样本：库里科技类有「量子计算机」，
# 两者字面相近，用来观察阈值在边界上会不会误判


async def run_batch(questions):
    """
    把一批问题挨个跑一遍
    返回：[(问题, 最高相似度, 命中的标题列表)]
    """
    out = []
    for q in questions:
        result = await retrieve(q, TOP_K)
        titles = [hit["meta"].get("title", "") for hit in result["hits"]]
        out.append((q, result["top_score"], titles))
        top1 = titles[0] if titles else "（无）"
        print("     %-24s %.4f  |  %s" % (q[:24], result["top_score"], top1))
    return out


async def main():
    print("=" * 74)
    print("  RAG 检索质量评测（真实向量化接口 + 真实向量库）")
    print("=" * 74)

    print("\n[1] 跑正样本（%d 条）" % len(POSITIVE))
    pos = await run_batch([q for q, _ in POSITIVE])

    print("\n[2] 跑负样本（%d 条）" % len(NEGATIVE))
    neg = await run_batch(NEGATIVE)

    # ---------- 指标一：recall@4（和阈值无关） ----------
    print("\n" + "=" * 74)
    print("[3] 指标一：recall@%d —— 检索出来的前 %d 块里有没有正确答案" % (TOP_K, TOP_K))
    print("-" * 74)
    hit_count = 0
    missed = []
    for (question, keyword), (_, score, titles) in zip(POSITIVE, pos):
        if any(keyword in t for t in titles):
            hit_count += 1
        else:
            missed.append((question, keyword, score, titles[0] if titles else "（无）"))
    print("  命中 %d / %d = %.1f%%" % (hit_count, len(POSITIVE), hit_count / len(POSITIVE) * 100))

    if missed:
        print("\n  没命中的样本（这就是检索能力的真实边界）：")
        for question, keyword, score, top1 in missed:
            print("    问题：%s" % question)
            print("      期望关键词：%s ｜ 实际最高分：%.4f ｜ 实际第一名：%s" % (keyword, score, top1))

    # ---------- 指标二：阈值扫描 ----------
    print("\n" + "=" * 74)
    print("[4] 指标二：阈值扫描 —— 不同阈值下模式判定准不准")
    print("-" * 74)
    print("  %-8s %-20s %-20s %-10s" % ("阈值", "正样本判对（该用资料）", "负样本判对（不该用）", "总正确率"))
    for th in THRESHOLDS:
        pos_ok = sum(1 for _, s, _ in pos if s >= th)
        neg_ok = sum(1 for _, s, _ in neg if s < th)
        total = pos_ok + neg_ok
        print("  %-8.2f %-20s %-20s %.1f%%" % (
            th,
            "%2d/%d (%.0f%%)" % (pos_ok, len(pos), pos_ok / len(pos) * 100),
            "%2d/%d (%.0f%%)" % (neg_ok, len(neg), neg_ok / len(neg) * 100),
            total / (len(pos) + len(neg)) * 100,
        ))

    # ---------- 分数分布：这是「阈值为什么定这里」的直接依据 ----------
    print("\n" + "=" * 74)
    print("[5] 相似度分布（阈值的直接依据）")
    print("-" * 74)
    pos_scores = sorted(s for _, s, _ in pos)
    neg_scores = sorted(s for _, s, _ in neg)
    print("  正样本（问题答案在库里）：最低 %.4f ｜ 中位 %.4f ｜ 最高 %.4f"
          % (pos_scores[0], pos_scores[len(pos_scores) // 2], pos_scores[-1]))
    print("  负样本（库里没有答案）　：最低 %.4f ｜ 中位 %.4f ｜ 最高 %.4f"
          % (neg_scores[0], neg_scores[len(neg_scores) // 2], neg_scores[-1]))

    overlap = [s for s in neg_scores if s >= pos_scores[0]]
    print("  重叠区间：负样本里有 %d 条分数 >= 正样本最低分（%.4f）"
          % (len(overlap), pos_scores[0]))
    if overlap:
        print("    这就是「无论阈值取多少都会判错一部分」的直接证据：%.4f ~ %.4f"
              % (min(overlap), max(overlap)))

    # ---------- 结论 ----------
    best = max(THRESHOLDS,
               key=lambda th: sum(1 for _, s, _ in pos if s >= th)
               + sum(1 for _, s, _ in neg if s < th))
    print("\n" + "=" * 74)
    print("  结论：在这 %d 条正样本 + %d 条负样本上，阈值 %.2f 的总正确率最高。"
          % (len(POSITIVE), len(NEGATIVE), best))
    print("  注意：样本量小且由自己构造，这个结论只用来验证「量级」和「选阈值的思路」，")
    print("        不能当成生产环境的准确率指标（面试时也要这样如实说明）。")
    print("=" * 74)

    await async_engine.dispose()
    # 关掉数据库连接池，让脚本干净退出


if __name__ == "__main__":
    asyncio.run(main())
