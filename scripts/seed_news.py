# 往 news 表里灌"本地生成的演示数据"：8 个分类，共 403 条。
# seed_news.sql   能把**当前库里那 403 条**原样复现（逐字段一致）

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta

# 让脚本在 scripts/ 目录下运行时也能 import 到项目根目录的模块
# 【来源】标准库 sys.path，写法同 scripts/ingest_news.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.db_conf import ASYNC_DATABASE_URL
from models.news import Category, News

# 固定随机种子：保证每次生成的数据一模一样（排查问题时好复现）
# 【来源】标准库 random.Random 支持传入种子
RANDOM_SEED = 20261010

# 分类清单与每类条数。
# 口径对齐当前库里的实际分布（头条50 社会51 国内51 国际57 娱乐46 体育44 科技50 财经54 = 403）
# 元组含义：(分类名, 排序号, 生成条数)
CATEGORY_PLAN = [
    ("头条", 1, 50),
    ("社会", 2, 51),
    ("国内", 3, 51),
    ("国际", 4, 57),
    ("娱乐", 5, 46),
    ("体育", 6, 44),
    ("科技", 7, 50),
    ("财经", 8, 54),
]

# 每个分类的标题模板。
# 刻意只写中性题材（赛事、产业、民生、文娱），不涉及真实人物与真实事件。
TITLE_TEMPLATES = {
    "头条": [
        "多地同步启动城市更新专项行动",
        "全国性便民服务清单完成新一轮扩容",
        "重点项目集中开工带动就业岗位增加",
        "跨区域交通网络新增两条运营线路",
        "城乡供水一体化工程进入收尾阶段",
        "夏粮收购工作全面展开",
        "基层医疗服务能力提升计划落地",
        "多部门联合部署汛期安全排查",
        "老旧小区改造样本经验对外发布",
        "职业技能培训补贴范围进一步扩大",
        "公共文化场馆暑期延时开放",
        "社区养老服务站点覆盖率稳步提高",
    ],
    "社会": [
        "外卖骑手权益保障新规开始试行",
        "多所高校公布秋季学期教学安排",
        "城市公园绿地面积连续三年增长",
        "志愿服务组织数量突破新的量级",
        "暑期托管班在多个街道同步开班",
        "菜市场改造升级后重新开门营业",
        "地铁新线开通首周客流平稳",
        "社区食堂推出老年助餐优惠方案",
        "垃圾分类督导员队伍完成轮训",
        "中小学课后服务质量评估结果公布",
        "快递进村覆盖率再创新高",
        "无偿献血点增设夜间服务时段",
    ],
    "国内": [
        "多项便民新政本月起正式施行",
        "全国性行业标准发布实施",
        "重大科技基础设施通过验收",
        "区域协调发展战略取得新进展",
        "重点民生工程提前完成年度目标",
        "高质量发展调研行活动启动",
        "公共服务均等化水平持续提升",
        "规模性设备更新政策效果显现",
        "重点产业链供应链韧性增强",
        "城乡融合发展试点成效初显",
        "全国性职业技能大赛落下帷幕",
        "绿色低碳转型取得明显成效",
    ],
    "国际": [
        "多国代表就气候议题举行磋商",
        "国际航运运价指数小幅回落",
        "区域贸易协定新一轮谈判开启",
        "全球粮食价格月度报告发布",
        "多边开发机构上调增长预期",
        "国际旅游市场复苏势头延续",
        "跨境电子商务规则研讨会在线上举行",
        "多国联合开展极地科考合作",
        "国际标准组织发布新版技术规范",
        "全球半导体设备出货量环比回升",
        "多国港口完成智能化改造试点",
        "国际能源署发布季度市场展望",
    ],
    "娱乐": [
        "暑期档电影市场票房表现亮眼",
        "多部现实题材剧集定档播出",
        "音乐节巡演在多个城市落地",
        "博物馆文创产品上新受关注",
        "国产动画电影公布制作进度",
        "戏曲进校园活动覆盖面扩大",
        "综艺节目模式创新引发讨论",
        "网络文学出海作品数量增长",
        "城市音乐厅推出公益演出季",
        "纪录片创作扶持计划公布入选名单",
        "非遗技艺短视频传播效果显著",
        "影视基地升级改造完成并开放",
    ],
    "体育": [
        "全国田径锦标赛男子百米决赛爆冷夺冠",
        "女排世界联赛取得三连胜",
        "中超联赛第十八轮焦点战握手言和",
        "CBA 新赛季揭幕战主队大胜",
        "马拉松赛事报名人数创下新高",
        "乒乓球世界杯国乒包揽四强",
        "青少年足球训练营在多地同时开营",
        "羽毛球公开赛混双组合晋级决赛",
        "篮球名宿谈青训体系改革方向",
        "城市业余联赛参赛队伍突破千支",
        "冰雪项目国家集训队完成夏训",
        "省运会筹备工作进入冲刺阶段",
    ],
    "科技": [
        "国产大模型开源新版本参数规模翻倍",
        "新一代国产芯片完成流片验证",
        "自动驾驶测试里程突破新纪录",
        "量子计算原型机取得阶段性进展",
        "开源社区发布年度生态报告",
        "低空经济试点城市范围扩大",
        "国产数据库在金融行业完成替换",
        "AI 辅助诊断系统通过临床评估",
        "新型储能电池循环寿命明显提升",
        "卫星互联网完成首次组网测试",
        "工业机器人产量连续三季度增长",
        "脑机接口临床试验启动招募",
    ],
    "财经": [
        "社会融资规模数据发布",
        "制造业采购经理指数重回扩张区间",
        "上半年外贸进出口总值实现增长",
        "多家银行下调存款挂牌利率",
        "新能源车出口量创下单月新高",
        "一线城市二手房成交量回暖",
        "民营经济发展支持政策细则落地",
        "跨境电商综合试验区新增城市",
        "消费市场暑期档表现好于预期",
        "上市公司半年报披露接近尾声",
        "人民币汇率双向波动保持稳定",
        "城市商业综合体空置率环比下降",
    ],
}

# 标题后缀：同一件事被不同媒体、不同时间报道，标题上常带点小尾巴。
# 让 403 条里"标题完全一样"的情况变少；故意留两个空串，保留一部分自然标题。
TITLE_SUFFIXES = [
    "",
    "",
    "（附现场图）",
    "（视频报道）",
    "（详情）",
    "（更新）",
    "（组图）",
]

# 正文结尾的几种说法（随机拼，避免正文完全雷同）
CONTENT_TAILS = [
    "相关部门表示，下一步将继续完善配套措施，确保各项部署落实到位。",
    "业内人士分析认为，这一变化将对行业格局产生持续影响。",
    "据悉，相关工作已在多个地区同步启动，后续进展将持续向社会公布。",
    "有关负责人介绍，此轮调整充分考虑了各方意见，力求稳妥推进。",
    "专家指出，从长期看这一趋势符合预期，但仍需关注执行层面的细节。",
]

# 摘要前缀（拼 description 用）
SUMMARY_PREFIX = "记者了解到，"

# 作者/来源：均为虚构署名或公开媒体名，不代表真实报道
AUTHORS = [
    "新华社", "人民日报", "央视新闻", "中国新闻网", "经济日报", "科技日报",
    "第一财经", "澎湃新闻", "光明日报", "工人日报",
]

# 占位图服务的图片 id 上限（picsum.photos 支持按 id 取固定图）
# 当前库里 403 条用的就是这个格式，保持一致
IMAGE_ID_MAX = 1000


def make_content(title: str, rng: random.Random) -> str:
    """按标题拼一段 100 字上下的正文（模板化，不含真实报道内容）"""
    parts = [
        title + "。",
        "记者了解到，相关情况已于近日公布，各方反应总体积极。",
        rng.choice(CONTENT_TAILS),
        "更多细节仍在跟进中。",
    ]
    return "".join(parts)


def make_one_news(category_id: int, category_name: str, rng: random.Random,
                  now: datetime) -> News:
    """造一条新闻（字段口径对齐现有数据）"""
    # 先挑一个标题模板，再随机加个后缀（也可能不带后缀）
    title = rng.choice(TITLE_TEMPLATES[category_name]) + rng.choice(TITLE_SUFFIXES)

    content = make_content(title, rng)

    # 摘要取正文前 60 字，控制在新表 description 字段长度内
    description = content[:60]

    # 浏览量分三档，让"排行榜"类查询有区分度
    #   【来源】标准库 random.choices 支持按权重抽样
    level = rng.choices(population=[1, 2, 3], weights=[60, 30, 10])[0]
    if level == 1:
        views = rng.randint(200, 3000)
    elif level == 2:
        views = rng.randint(3000, 8000)
    else:
        views = rng.randint(8000, 20000)

    # 发布时间：过去 24 个月内随机某个时间点（现有数据跨 2023-08 ~ 2025-09，量级接近）
    publish_time = now - timedelta(
        days=rng.randint(0, 730),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
    )

    return News(
        title=title,
        description=description,
        content=content,
        image="https://picsum.photos/id/" + str(rng.randint(1, IMAGE_ID_MAX)) + "/200/200",
        author=rng.choice(AUTHORS),
        category_id=category_id,
        views=views,
        publish_time=publish_time,
    )


async def main():
    parser = argparse.ArgumentParser(description="往 news / news_category 表灌演示数据")
    parser.add_argument("--reset", action="store_true", help="先清空两张表再灌")
    args = parser.parse_args()

    if not ASYNC_DATABASE_URL:
        print("[失败] 没有读到数据库连接串")
        return

    engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        news_count_stmt = select(func.count()).select_from(News)
        existing = await session.scalar(news_count_stmt) or 0

        if args.reset and existing > 0:
            # 有外键：先删 news，再删 news_category
            await session.execute(delete(News))
            await session.execute(delete(Category))
            await session.commit()
            print("已清空 news 与 news_category（原有 " + str(existing) + " 条新闻）")
            existing = 0

        if existing > 0:
            print("表里已经有 " + str(existing) + " 条数据了，跳过灌数据。")
            print("想重新灌：python scripts/seed_news.py --reset")
            await engine.dispose()
            return

        # 分类：按名字查，缺哪个补哪个（不重复插）
        cat_id_map = {}
        for name, sort_order, _ in CATEGORY_PLAN:
            found = await session.scalar(select(Category).where(Category.name == name))
            if found is None:
                found = Category(name=name, sort_order=sort_order)
                session.add(found)
                await session.flush()   # flush 后才有自增 id
            cat_id_map[name] = found.id
        await session.commit()

        rng = random.Random(RANDOM_SEED)
        now = datetime.now()

        rows = []
        for name, _sort_order, per_count in CATEGORY_PLAN:
            for _ in range(per_count):
                rows.append(make_one_news(cat_id_map[name], name, rng, now))

        session.add_all(rows)
        await session.commit()

        total = await session.scalar(news_count_stmt)

    await engine.dispose()

    print("灌数据完成：共 " + str(total) + " 条（" + str(len(CATEGORY_PLAN)) + " 个分类）")
    print("")
    print("验证一下，可以在 MySQL 里跑：")
    print("  SELECT c.name, COUNT(*) FROM news_app.news n")
    print("  JOIN news_app.news_category c ON c.id = n.category_id GROUP BY c.name;")


# 【来源】标准库 asyncio.run —— 启动事件循环跑异步主函数
if __name__ == "__main__":
    asyncio.run(main())
