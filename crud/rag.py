# ============================================================
# 区域：RAG 核心逻辑（crud 层）
# 作用：串起"切块 → 向量化 → 存库 → 检索"，对外提供 2 个函数
#   ① build_index  ：把 MySQL 的新闻同步进向量库（离线跑，支持增量）
#   ② retrieve     ：拿问题去向量库找相关新闻（在线跑，每次问答都调）
#
# 关键理解（面试会问）：
#   ① 增量索引：给每篇新闻算一个"内容指纹"（md5），指纹没变就不重复向量化。
#      403 篇全量重建要几分钟，增量只要几秒。
#   ② 内容变了必须先删旧块再写新块：如果新版切出的块变少了，
#      只 upsert 会留下旧版的"僵尸块"，检索时会命中已经改掉的内容。
#   ③ 为什么用 asyncio.to_thread：向量化要调网络接口、Chroma 读写是同步操作，
#      直接在协程里跑会把整个服务的其他请求卡住；丢到线程池就不会。
#
# ============================================================
# 【学习版说明书】
# ============================================================
# 本文件是"逐词精讲版"。每行代码下面固定两段：
#   # 做什么：  一句话说清这行在干什么
#   # 逐词：    把这行里每个"词"拆开 —— 它是什么、从哪来、原意是什么
#
# 标签（用来分清"这是自己起的名字还是某个库的方法"）：
#   【内置】  Python 自带，不用 import 就用（None、len、点号、括号、for……）
#   【标准库】Python 自带模块，要 import 但不用 pip 装（asyncio、hashlib）
#   【三方库】pip 装进来的（sqlalchemy）
#   【本地】  本项目自己写的（会写清是哪个文件的哪个函数）
#   【自起名】只是别人随手起的名字，不是任何 API —— 这种最容易让你以为"是不是我没学过的方法"
#
# 两条规矩：
#   ① 跨文件引用写"文件 + 函数名"，不写行号（插注释会让行号失效）
#   ② 一个名字可能在别处也被用（比如 count），看代码时要先确认"这一处是谁的"
#
# 这个文件是全项目 RAG 的"大脑"：只有 2 个对外函数，但每行都值得看懂。
# ============================================================

# ------------------------------------------------------------
# 第一部分：导包（8 个 import，来自三种不同来源）
# ------------------------------------------------------------

import asyncio
# 做什么：把 Python 自带的 asyncio 模块拿进来
# 逐词：
#   import   【内置】"导入"关键字。两种写法别混：
#              import A          → 后面写 A.xxx（本行就是这种）
#              from A import B   → 后面直接写 B（下面那些是这种）
#   asyncio  【标准库】async（异步）+ io（输入输出）→ "异步 IO"。
#            它是 Python 自带的异步框架，管着"事件循环"（一个可以同时照看很多请求的调度器）
# 本文件只用它一个功能：asyncio.to_thread(...) —— 把同步阻塞的活丢到线程池
# 为什么非它不可：见下面 build_index 里第一次用到它的地方，有详细解释

import hashlib
# 做什么：拿进 Python 自带的哈希库
# 逐词：
#   hashlib  【标准库】hash（哈希/剁碎）+ lib（library 库）
#            hash 的原意是"把东西剁碎做成杂烩"，在计算机里指"把任意内容压成固定长度的指纹"
# 本文件只用它的 hashlib.md5(...) —— 算内容指纹
# 对比：存密码用的 bcrypt 在 utils/security.py，那才是安全场景；这里只是"内容比没变"

from sqlalchemy import func, select
# 做什么：从 sqlalchemy 里取出 func 和 select 这两个东西
# 【2026-09-20 补进来的 func】它是"调用数据库自带函数"的工具：
#   func.count()        → SQL 里的 COUNT()，用来数条数
#   func.char_length()  → SQL 里的 CHAR_LENGTH()，用来算正文有多少字
#   统计类问题（"一共有多少篇""哪一篇最长"）就是靠它俩直查数据库的，见本文件末尾那一段
# 做什么：从 sqlalchemy 里取出 select 这一个函数
# 逐词：
#   from       【内置】固定搭配 from A import B = "从 A 里取出 B"
#   sqlalchemy 【三方库】pip 装的 ORM 框架。拆开：
#              SQL（结构化查询语言）+ alchemy（炼金术）
#              → "用 Python 写 SQL 的炼金术"，意思是"不用手写 SQL 字符串，
#                用 Python 对象拼查询，它帮你转成 SQL"
#   import     【内置】"取出"
#   select     【三方库】SQLAlchemy 的查询构造器 —— 一个函数。
#              ⚠️ 别和 SQL 里的 SELECT 关键字搞混：这里是 Python 里的函数，用来"拼一条查询"，
#                 用法是 select(要查什么).join(...).where(...)，像搭积木
#   ORM 是什么意思：Object-Relational Mapping（对象-关系映射）
#              → 把数据库的"表"映射成 Python 的"类"，把"行"映射成"对象"。
#                这样你就能用 news.title 代替手写 news.title 这个列名

from sqlalchemy.ext.asyncio import AsyncSession
# 做什么：取出异步版的"数据库会话"类，只用它做类型标注
# 逐词：
#   sqlalchemy.ext   【三方库】ext = extension（扩展）→ 主库里放不下的东西放在 ext 子包里
#   .asyncio         【三方库】这个子包是专门给 asyncio 用的（同步版在别处）
#   AsyncSession     【三方库】拆开：Async（异步）+ Session（会话）。
#                    会话 = "一次数据库对话的上下文"，你通过它执行查询、提交事务
#   ⚠️ 本行只是拿来**做类型标注**用的（写在函数参数后面，告诉别人"这里要传一个会话"），
#      真正 new 出一个会话的地方是 config/db_conf.py
# AsyncSession：异步数据库会话（这里只用来做类型标注）

from config.rag_conf import SCORE_THRESHOLD, TOP_K
# 做什么：从配置中心取出两个常量
# 逐词：
#   config      【本地】项目里的 config 包（放各种配置文件的文件夹）
#   .rag_conf   【本地】config 包里的 rag_conf.py（rag + conf）
#   SCORE_THRESHOLD  【本地】"相似度阈值"，值 0.55，用来判断"这次要不要带资料"
#   TOP_K            【本地】"检索取前几块"，值 4
#   ⚠️ 为什么放配置里而不写死：调效果时只改 config 一个文件，不用翻遍代码（面试可以讲这点）
# 相似度阈值、默认检索条数

from models.news import Category, News
# 做什么：取出 news 表对应的两个 ORM 类
# 逐词：
#   models      【本地】项目里的 models 包（ORM 模型都放这儿，一个文件对应一张表）
#   .news       【本地】models/news.py
#   Category    【本地】分类表对应的类。category（分类）
#   News        【本地】新闻表对应的类。这是@核心数据表：403 条新闻都在里面
#   ⚠️ 注意这两个类不是"数据"，是"表的形状"；真正的一行数据（一条新闻）是 News 的实例
# 新闻表、分类表的 ORM 模型

from utils.embedding import embed_texts
# 做什么：取出"把文字批量转成向量"的函数
# 逐词：
#   utils          【本地】项目里的 utils 包（工具函数）
#   .embedding     【本地】utils/embedding.py
#   embed_texts    【本地】那个文件里定义的函数。拆开：
#                  embed（嵌入）+ texts（文本，复数）
#                  embed 在 AI 领域是"把文字映射到高维数字空间里"的标准叫法
#   ⚠️ 名字末尾的 s 表示"一次处理多个文本"，所以它收的是列表、返回的也是列表
# embed_texts：把文字批量转成向量

from utils.splitter import split_text
# 做什么：取出"把长文本切成小块"的函数
# 逐词：
#   splitter   【本地】utils/splitter.py。split（切分）+ ter（表示"做这件事的东西"）→ "切分器"
#   split_text 【本地】那个文件里定义的函数：把一段文本切成一个列表
#               ⚠️ 注意它是**自研的**（自己写的递归切块），不是 LangChain 那种第三方库
# split_text：把长文本切成小块

from utils import vectorstore
# 做什么：把 utils/vectorstore.py 这个**模块本身**拿进来
# 逐词：
#   utils        【本地】包名
#   vectorstore  【本地】模块名（一个 .py 文件就是一个模块）。vector（向量）+ store（存储）
#   ⚠️ 写法关键：from 包 import 模块 和 from 模块 import 函数，看起来像，结果不一样！
#        from utils import vectorstore   → 拿到的"模块"，下面写 vectorstore.reset
#        from utils.vectorstore import reset → 拿到的"函数"，下面只能写 reset
#      这里故意用**模块**写法，好处是"一眼能看出每个函数是哪个文件里的"——
#      如果写成 from ... import reset / add / search，代码里就只剩光秃秃的 reset，
#      你就看不出它来自哪里了（这正是你看代码时最容易被绊住的地方）
# vectorstore：向量库的封装（add / search / get_hashes / delete_news / reset / count）


# ============================================================
# 第二部分：工具函数
# ============================================================


def fingerprint(text: str) -> str:
    # 做什么：定义一个函数，输入一段文字，输出它的"内容指纹"
    # 逐词：
    #   def          【内置】define（定义）+ 函数名 → "定义函数"的关键字
    #   fingerprint  【自起名】拆开：finger（手指）+ print（印）→ "指纹"。
    #                这个词是项目自己起的，不是任何库的 API
    #   text: str    【自起名 + 内置】text 是参数名（自己起的）；: str 是类型标注，
    #                表示"这里应该传一个字符串"。⚠️ 只是写给人/IDE 看的，运行时不会检查
    #   -> str       【内置】箭头的部分是返回类型标注："这个函数会返回字符串"
    #   :            【内置】冒号 = 下面缩进的那几行都属于这个函数
    """算内容指纹：内容一样指纹就一样，用来判断这篇新闻要不要重新向量化"""
    # 做什么：docstring（文档字符串）
    # 逐词：
    #   """..."""  【内置】三引号字符串。写在函数第一句 → 自动变成"说明文档"，
    #              IDE 鼠标悬停时显示的就是它。⚠️ 必须紧跟函数定义那行，中间不能插代码
    return hashlib.md5(text.encode("utf-8")).hexdigest()
    # 做什么：把 text 转成字节 → 算 md5 → 转成 32 位十六进制字符串 → 返回
    # 逐词（这一行是从左往右、一层套一层执行的）：
    #   return        【内置】"返回"：把右边算出来的东西交给调用方，函数到此结束
    #   hashlib       【标准库】上面 import 进来的哈希库
    #   .             【内置】点号 = "调用它里面的东西"。
    #                 hashlib.md5 连起来读 = "hashlib 库里的 md5 函数"
    #   md5(...)      【标准库】一种哈希算法。名字来自它的设计者 Ron Rivest，
    #                 输出固定 128 位（写成十六进制就是 32 个字符）。
    #                 ⚠️ md5 的抗碰撞性已经被攻破，所以**不能用于安全场景**；
    #                    我们这里只是"比对内容变没变"，输入来自自己的数据库、攻击者不可控，
    #                    属于非对抗场景，用 md5 是合适的（面试被问到要这么答）
    #   text.encode("utf-8")  【内置】把字符串转成**字节数组**。
    #                 ⚠️ 为什么必须转：md5 只吃字节，不吃字符串。
    #                 utf-8 是编码格式（中文一个字占 3 个字节）。不写它就用系统默认编码，
    #                 换台机器跑出来的指纹可能不一样 —— 所以显式写 utf-8 保证跨平台一致
    #   .hexdigest()  【标准库】hex（十六进制）+ digest（摘要）
    #                 → 把算出来的 16 字节二进制转成 32 个字符的十六进制文本，
    #                   这样好存进数据库、也好直接比字符串。
    #                 对比 .digest()：那个给的是原始 bytes（b'\x9d\xd4...'，带一堆转义符，不好用）
    # 输入变一点点，这 32 个字符会全变 —— 所以它能当"内容身份证"
    # md5 在这里只做"内容变没变"的比对，不涉及安全（安全场景要用 sha256）


def is_small_talk(question: str) -> bool:
    # 做什么：定义函数，判断一个问题是"闲聊/打招呼"还是"真问题"
    # 逐词：
    #   is_           【命名惯例】布尔函数常以 is_ 开头，读起来像英语句子：
    #                 is_small_talk(question) = "这个问题是小聊吗"
    #   small_talk    【自起名】small（小）+ talk（谈话）→ 英语里的固定说法"闲聊、寒暄"
    #   question      【自起名】参数名，代表"用户问的那句话"
    #   -> bool       【内置】bool = boolean（布尔值）→ 说明返回 True 或 False
    """
    判断是不是"打招呼"这类问题
    是的话直接当普通 AI 回答，不用去查新闻库（省一次检索，回答也更自然）
    为什么需要它：实测发现"你好"跟新闻的相似度能到 0.46，
      光靠阈值挡不住，会被误判成"有相关资料"
    """
    q = question.strip().lower()
    # 做什么：把用户的问题"清洗"一下，存进短名 q
    # 逐词：
    #   q                 【自起名】就是"把 question 缩短成 q"，省点字。
    #                     ⚠️ 起短名字是自由，但别到处用单字母，会看不懂；这里只在函数内用一下
    #   =                 【内置】赋值
    #   question          【自起名】上面那个参数
    #   .strip()          【内置】字符串方法：去掉**首尾**的空白（空格、换行、制表符）。
    #                     → 用户输入" 你好 "（带空格）也能匹配上
    #   .lower()          【内置】字符串方法：把英文字母全转成小写。
    #                     → 这样 "Hello"、"HELLO" 都能匹配到下面列表里的 "hello"
    #   ⚠️ 两个方法可以连着写（"链式调用"）：strip() 返回的还是字符串，所以能接着 .lower()
    for word in ("你好", "您好", "hello", "hi", "在吗", "你是谁", "谢谢", "再见"):
        # 做什么：把这些词挨个拿出来试
        # 逐词：
        #   for ... in ...  【内置】"遍历"循环：把右边那个东西里的元素一个一个取出来
        #   word            【自起名】循环变量，每一轮装一个候选词
        #   ( ... )         【内置】小括号包起来的一串 = **元组**（tuple）。
        #                   可以理解成"不能修改的列表"，适合放这种"固定的一组值"。
        #                   ⚠️ 元组和列表都写成一串值，区别是元组用 ()、列表用 []；
        #                      元组不可改，所以更省内存、更适合当常量用
        #   "你好" 等       【字符串】注意中文和英文的都要放：用户可能用任意一种打招呼
        #   :               【内置】冒号 = 下面缩进的是循环体
        if q.startswith(word):
            # 做什么：判断清洗过的问题是不是"以这个词开头"
            # 逐词：
            #   if              【内置】"如果"
            #   q               【自起名】上面清洗过的问题
            #   .startswith(x)  【内置】字符串方法：start（开始）+ s + with（用）→ "以 x 开头"。返回 True/False
            #   ⚠️ 为什么用"开头"而不是"包含"：
            #      用 in（包含）的话，"你说的谢谢是什么意思"这种正常提问会被误杀；
            #      判断开头就精准得多 —— 打招呼的人通常一上来就是"你好…"
            return True
            # 做什么：命中一个就立刻返回 True
            # 逐词：return 【内置】返回；True 【内置】Python 的"真"（注意首字母大写，不是 true）
            # 提前返回的好处：不用再看后面剩下的词，代码也短
    return False
    # 做什么：循环走完一个都没命中 → 返回 False
    # 逐词：
    #   return  【内置】返回
    #   False   【内置】Python 的"假"（首字母大写）
    #   ⚠️ 这一行在 for 循环**外面**（看缩进：它和 for 对齐）。
    #      这点很关键：写在循环里的话，第一个词没命中就会直接返回 False，
    #      后面的词永远试不到 —— 这是新手最经典的缩进 bug


# ============================================================
# 第三部分：① 入库 build_index —— 把 MySQL 新闻同步进向量库
# ============================================================


async def build_index(db: AsyncSession, rebuild: bool = False) -> dict:
    # 做什么：定义异步函数，把 MySQL 里的新闻切块 + 向量化 + 写进向量库
    # 逐词：
    #   async def     【内置】"定义协程函数"。加了 async 之后：
    #                 ① 调用它不会立刻执行，只得到一个"协程对象"
    #                 ② 必须用 await 或 asyncio.run 才会真的跑
    #                 ⚠️ 忘了 await 是新手最常见的"代码不执行还不报错"的原因
    #   build_index   【自起名】build（构建）+ index（索引）→ "建索引"。
    #                 index 在这里指"向量库里的那套数据"，不是数据库的索引
    #   db            【自起名】参数名，代表"一个数据库会话"（由调用方传进来）
    #   : AsyncSession【三方库】类型标注，说明 db 该传一个会话对象
    #   rebuild       【自起名】re（重新）+ build（建）→ "是否重建"。
    #                 ：bool 【内置】说明它是 True/False
    #   = False       【内置】默认参数：调用方不传时就是 False（也就是"默认走增量"）
    #   -> dict       【内置】说明返回一个字典
    """
    把新闻同步进向量库
    rebuild=False（默认）：增量，只处理新增和内容变化的新闻
    rebuild=True         ：全量重建，先清空向量库再全部重灌
    返回统计：{total, added, updated, skipped, removed, chunks}
    """
    if rebuild:
        # 做什么：如果调用方要求"全量重建"，走这个分支
        # 逐词：
        #   if       【内置】"如果"
        #   rebuild  【自起名】上面那个参数（True 或 False）
        #   ⚠️ 直接写 if rebuild 而不是 if rebuild == True —— Python 的惯例是
        #      "布尔值直接放进 if"，因为它本来就是真/假
        await asyncio.to_thread(vectorstore.reset)
        # 做什么：把向量库整个清空（删掉那张表）
        # 逐词：
        #   await          【内置】"等待" —— 等右边那个异步操作跑完，再继续往下走。
        #                  ⚠️ 没有 await，这行就只是"创建了一个任务"，东西不会被清干净，
        #                     而且不报错 —— 又是那种最难查的静默错误
        #   asyncio        【标准库】上面 import 的异步框架
        #   .to_thread     【标准库】to（到）+ thread（线程）→ "丢到线程里去跑"。
        #                  它会把后面那个**同步阻塞函数**挪到一个旁路线程执行，
        #                  这样主线程（事件循环）不会被卡住
        #   vectorstore    【本地】utils/vectorstore.py 这个模块
        #   .reset         【本地】那个文件里定义的 reset() 函数
        #   ⚠️⚠️ 最关键的细节：这里写的是 vectorstore.reset（**不带括号**）！
        #        为什么不能带括号：
        #          不带括号 → 传的是"函数本身"，to_thread 会在**线程里**去调用它 ✅
        #          带括号   → 会先在**主线程**执行完，再把结果传进去 → 等于没挪走 ❌
        #        而且带括号的那种写法**不会报错**，只是性能悄悄退化，非常难发现
        #   为什么必须用 to_thread：ChromaDB 的接口全是同步的（串行阻塞），
        #     直接在协程里调用会把唯一的主线程占住 —— 那期间其他所有请求都得排队
        old_hashes = {}
        # 做什么：给 old_hashes 一个空字典
        # 逐词：
        #   old_hashes  【自起名】old（旧的）+ hashes（哈希值/指纹，复数）→ "库里原有的指纹表"
        #   {}          【内置】花括号 = 空字典（⚠️ 空列表是 []、空集合是 set()）
        # 为什么要给空字典：因为库刚被清空，没有旧指纹可比。
        #   给空字典后，下面那句 "if old_hashes.get(news.id) == fp" 永远不成立
        #   → 所有新闻都会被当成"新增"处理，这正是"全量重建"该有的行为
        # 全量重建：先清空，然后所有新闻都当成"新增"
    else:
        # 做什么：不是全量重建，就按"增量"处理
        # 逐词：else 【内置】"否则"（和上面的 if 配对）
        old_hashes = await asyncio.to_thread(vectorstore.get_hashes)
        # 做什么：先把库里现有的"新闻id → 内容指纹"取出来，供下面逐个比对
        # 逐词：
        #   await               【内置】等它跑完
        #   asyncio.to_thread   【标准库】丢到线程池（原因同上：Chroma 是同步库）
        #   vectorstore.get_hashes  【本地】utils/vectorstore.py 的 get_hashes()，同样不带括号
        #   返回的是一个字典：{新闻id: 内容指纹}
        # 增量：先取出已有新闻的指纹，用来比对

    stmt = select(News, Category.name).join(Category, News.category_id == Category.id)
    # 做什么：拼一条"联表查询"（新闻 + 它的分类名），存进变量但不执行
    # 逐词：
    #   stmt   【自起名】statement（语句）的缩写 → 代表"一条还没执行的 SQL"
    #   select 【三方库】上面 import 的查询构造器。逐段读：
    #       select(News, Category.name)
    #         → 我要查"News 整行 + Category 表的 name 这一列"（一次可以查多个东西）
    #       .join(Category, 连接条件)
    #         → join = 连接（联表）。意思"把分类表接进来"
    #   News.category_id == Category.id
    #         → 连接条件：新闻表里的 category_id 等于分类表的 id
    #         → 这段是"翻译成人话"的等价 SQL：
    #              FROM news JOIN news_category ON news.category_id = news_category.id
    #         ⚠️ 注意是 ==（两个等号 = 判断相等），写成一个 = 会变成赋值、语法就错了
    #         ⚠️ . 点号在这里表示"这个类的某个字段/列"，不是"调用方法"
    # 为什么要联表：向量库的档案袋里要存"分类名"（前端来源卡片上要显示），
    #   而 MySQL 里新闻表只存了 category_id（一个数字），分类名在另一张表里
    rows = (await db.execute(stmt)).all()
    # 做什么：真正把 SQL 发给数据库，把结果全部取出来
    # 逐词：
    #   rows        【自起名】row 是"行"，rows 就是"很多行"
    #   ( ... )     【内置】最外层这对括号没有别的含义，只是为了让 .all() 能接在后面
    #   await       【内置】等数据库返回（这是**真异步**：aiomysql 驱动，等待时不占线程）
    #   db          【自起名】函数参数，数据库会话对象
    #   .execute(stmt)  【三方库】"执行"这条语句。注意它接收的是我们拼好的语句对象，不是 SQL 字符串
    #   .all()      【三方库】Result 对象的方法：把结果**全部**取成一个列表。
    #               对比 .first() 只取第一行；数据量大时 .all() 会占内存（要留意）
    # 结果长这样：[ (News对象, "科技"), (News对象, "体育"), ... ] —— 每项是个元组
    # 联表查出所有新闻 + 它的分类名（分类名后面要当来源展示）

    ids, texts, metas = [], [], []
    # 做什么：一次定义三个空列表（链式赋值的一种形式）
    # 逐词：
    #   ids     【自起名】每块的唯一编号，比如 "news_7_0"
    #   texts   【自起名】每块的正文（标题 + 块内容）
    #   metas   【自起名】metadata 的缩写 → 每块的"档案袋"
    #   =       【内置】赋值。这一行等价于三行：
    #             ids = []; texts = []; metas = []
    #   []      【内置】空列表
    # ⚠️ 为什么这三个必须"平行"：Chroma 要求 ids/documents/embeddings/metadatas
    #    四个列表**一样长、位置一一对应**。所以它们的 append 必须成对出现，
    #    中间不能有 continue/break 打断 —— 这也是下面循环里那几个计数分支要小心的原因
    # 攒这次要写入向量库的块

    added = updated = skipped = removed = 0
    # 做什么：一次定义四个计数器，都从 0 开始
    # 逐词：
    #   added    【自起名】"新增"：指纹表里没有的新闻
    #   updated  【自起名】"更新"：指纹变了、需要先删旧块再写新块的
    #   skipped  【自起名】"跳过"：指纹一样、不用处理的（增量省时间就省在这里）
    #   removed  【自起名】"清理"：MySQL 里已删、向量库里还留着的孤儿块
    #   = 0 （连着写）  【内置】这叫"链式赋值"：四个变量同时等于 0。
    #     ⚠️ 它**不是**元组，别和 a, b = 0, 0 搞混
    # 这四个数最后会打包进返回值，给 scripts/ingest_news.py 打印出来
    seen = set()
    # 做什么：准备一个空集合，用来记录"MySQL 里还存在的新闻 id"
    # 逐词：
    #   seen  【自起名】see 的过去分词 → "见过的（id）"
    #   set() 【内置】建一个**空集合**。
    #         ⚠️ 这里很容易写错：空集合必须写 set()，不能写 {}（{} 是空字典！）
    #   集合的特点：① 自动去重 ② 判断"某元素在不在里面"特别快（比列表快得多）
    # 为什么要它：下面循环结束时，用它和"向量库里的 id"对比，
    #   找出"向量库有、MySQL 没有"的那些 —— 也就是该清掉的孤儿数据

    for news, category_name in rows:
        # 做什么：一行一行遍历查询结果，并把每行"拆成两部分"
        # 逐词：
        #   for ... in rows  【内置】遍历列表里的每一项
        #   news, category_name
        #         → 【内置】这叫"元组解包"：因为每项是 (News对象, "科技") 两项，
        #           所以可以一次写两个名字把里面的两项分别接住。
        #           等价于 item = 那一项; news = item[0]; category_name = item[1]
        #   news           【自起名】一个 News 对象，可以点出 .id / .title / .content
        #   category_name  【自起名】分类名的字符串（来自联表查出的 Category.name）
        #   ⚠️ 顺序必须和 select(News, Category.name) 里的顺序一致，反了就接错了
        seen.add(news.id)
        # 做什么：把这篇新闻的 id 记进"见过的集合"
        # 逐词：
        #   seen    【自起名】上面那个集合
        #   .add(x) 【内置】集合的方法：往集合里放一个元素
        #           ⚠️ 集合用 .add，列表用 .append —— 混了会 AttributeError
        #   news.id 【本地】News 对象身上的属性（对应数据库的 id 列）
        #               ⚠️ 这里的点号表示"取这个对象的属性"，和"调用方法"共用一个点号，
        #                  区别在于后面有没有括号：id 没括号 = 属性；add() 有括号 = 方法
        text = f"{news.title}\n{news.content}"
        # 做什么：把标题和正文拼成一段，供下面算指纹
        # 逐词：
        #   text         【自起名】拼好的整段文字
        #   f"..."       【内置】f-string：字符串前面加 f，里面用 { } 就能直接写变量，
        #                运行时会被替换成变量的值。
        #                ⚠️ 没有 f 那个前缀的话，{news.title} 会被当成普通文字原样输出
        #   {news.title} 【本地】取出新闻的标题
        #   \n           【内置】换行符（一个字符，不是两个）。
        #                放在中间是为了"标题和正文之间有个换行"，让两部分不黏在一起
        #   {news.content}  【本地】新闻的正文
        fp = fingerprint(text)
        # 做什么：给这段文字算指纹，存进 fp
        # 逐词：
        #   fp            【自起名】fingerprint 的缩写，"指纹"
        #   fingerprint() 【本地】本文件上面定义的那个函数
        # 指纹依然按"标题+正文"算 —— 和改动前保持一致，
        # 这样老库里的指纹不会因为这次改动全部失效
        # （如果改成"只按正文算"，那 403 篇的指纹会全部变掉，下次建索引就会全量重算一遍）

        if old_hashes.get(news.id) == fp:
            # 做什么：判断"库里原有的指纹"和"刚算出来的指纹"是不是一样
            # 逐词：
            #   old_hashes        【自起名】库里的旧指纹字典
            #   .get(news.id)     【内置】字典的 get(键)：**有就返回对应的值，没有就返回 None**。
            #                     ⚠️ 为什么用 .get 而不是 old_hashes[news.id]：
            #                        新新闻的 id 在旧字典里不存在，用方括号会直接 KeyError 崩掉；
            #                        用 .get 就安全地返回 None，而 None == 字符串 一定不成立
            #   == fp             【内置】判断相等（两个等号）
            #   ⚠️ 这句同时覆盖了两种情况：
            #        新新闻（get 给 None）→ 不相等 → 不会跳过
            #        内容变了（指纹不同）→ 不相等 → 不会跳过
            skipped += 1
            # 做什么：跳过计数器 +1
            # 逐词：
            #   +=  【内置】复合赋值：skipped += 1 等价于 skipped = skipped + 1
            #   ⚠️ Python 没有 i++ 这种写法，自增只能写 += 1
            continue
            # 做什么：跳过本轮循环剩下的代码，直接进入下一条新闻
            # 逐词：
            #   continue  【内置】"继续" —— 结束**这一次**循环，开始下一次。
            #             ⚠️ 和 break 的区别：break 是"结束整个循环"；
            #                continue 只是"这一轮到此为止"，后面的新闻照样处理
            # 放在这里的效果：内容没变的新闻，既不重新向量化也不重新入库 ——
            #   这就省下了最大的一笔开销（向量化要调外部付费接口）
        # 指纹一样 = 内容没变，跳过（这就是"增量"省时间的地方）

        if news.id in old_hashes:
            # 做什么：判断"这个 id 是否已经在库里"（用来区分"更新"和"新增"）
            # 逐词：
            #   in  【内置】用在字典上时，判断的是"这个**键**在不在字典里"。
            #       ⚠️ 不是判断"值在不在" —— 这点特别容易记错
            #   能走到这一行说明什么：上面那句"指纹一样就 continue"已经把没变的排除掉了，
            #     所以现在能到这里 = 指纹确实变了。那再分两种：
            #       id 在旧表里 → 属于"更新"（库里已有这篇，内容改了）
            #       id 不在旧表里 → 属于"新增"（库里从没有过）
            await asyncio.to_thread(vectorstore.delete_news, news.id)
            # 做什么：先把这篇新闻的旧块删干净
            # 逐词：
            #   await                【内置】等它跑完
            #   asyncio.to_thread    【标准库】丢线程池
            #   vectorstore.delete_news  【本地】utils/vectorstore.py 的 delete_news()
            #   ⚠️ 注意 to_thread 传**多个**参数时的写法：
            #        to_thread(函数, 参数1, 参数2)
            #      函数名后面直接用逗号跟参数 —— 同样**不要加括号**，也不要写成
            #      to_thread(vectorstore.delete_news(news.id))（那样就变成在主线程执行了）
            #   为什么必须先删：块 id 是 news_{id}_{第几块}。如果新版切出来的块变少了，
            #      只做 upsert 的话多出来的旧 id 不会被覆盖，会变成"僵尸块"留在库里，
            #      检索时命中已经改掉的内容 —— 用户看到的是过期信息
            updated += 1
            # 做什么：更新计数器 +1
            # 逐词：+= 【内置】自增
            # 内容变了：先删旧块，避免新旧内容混在一起被检索到
        else:
            # 做什么：库里原来没有这个 id → 属于新增
            # 逐词：else 【内置】"否则"
            added += 1
            # 做什么：新增计数器 +1
            # 逐词：+= 【内置】自增
            # 指纹表里没有 = 新加的新闻

        chunks = split_text(news.content) or [news.title]
        # 做什么：把正文切成小块；如果切出来是空的，就退化成"只有标题的一块"
        # 逐词：
        #   chunks         【自起名】chunk（块）+ s → 切出来的所有块（一个列表）
        #   split_text     【本地】utils/splitter.py 的 split_text()，接收一段文本、返回块列表
        #   (news.content) 【本地】只把**正文**传进去切，标题不参与切块 ——
        #                  这样标题就不会占掉"每块最多 400 字"的预算
        #   or [news.title]  【内置】or 在这里做"兜底"：
        #                  Python 的 or 是"左边为假就取右边"，空列表 [] 是假值，
        #                  所以"切出来是空的"时会取右边那个 [news.title]（只含标题的列表）
        #                  ⚠️ 为什么需要兜底：正文为空时 split_text 会返回 []，
        #                     那样这篇新闻在向量库里就彻底不存在了（搜不到）。
        #                     兜底成"只索引标题"，至少还能被搜到
        # 只切正文，标题不参与切块 —— 这样标题就不会占掉块的字数预算
        # 兜底：正文为空时（content 是 not null，理论上不会）退化成"只索引标题"，
        #   否则这篇新闻在向量库里会彻底不存在

        for i, chunk in enumerate(chunks):
            # 做什么：遍历每一块，同时拿到它的序号
            # 逐词：
            #   for ... in ...  【内置】遍历
            #   enumerate(chunks)  【内置】"枚举"：它会把列表变成"（序号, 元素）"这样一对一对地给。
            #                ⚠️ 为什么不用普通的 for chunk in chunks：因为下面
            #                   拼块 id 时要用到"第几块"，所以必须拿到序号。
            #                   enumerate 比"自己写个 i = 0 然后 i += 1"干净得多
            #   i        【自起名】序号（默认从 0 开始）
            #   chunk    【自起名】这一块的文本
            ids.append(f"news_{news.id}_{i}")
            # 做什么：给这一块生成唯一编号，加进 ids 列表
            # 逐词：
            #   ids     【自起名】上面那个空列表
            #   .append(...)  【内置】列表方法：往**末尾追加一个元素**（一次加一个）。
            #                 ⚠️ 别和 .extend 混：extend 是把另一个列表里的元素逐个并进来
            #   f"news_{news.id}_{i}"  【内置 f-string + 自起名格式】拼出来长这样："news_7_0"
            #                 = "news_" + 新闻id + "_" + 第几块
            #   这个"可预测的 id"很关键：
            #     ① 重复写同一个 id 是覆盖（幂等），所以脚本能反复跑
            #     ② 内容变了我们能算出旧 id 去把它删掉（就是上面那句 delete_news）
            #     ③ 看 id 就知道这块属于哪篇新闻、是第几块
            texts.append(f"{news.title}\n{chunk}")
            # 做什么：把"标题 + 换行 + 这一块内容"加进 texts 列表
            # 逐词：
            #   texts   【自起名】上面那个空列表
            #   .append 【内置】追加一个元素
            #   f"{news.title}\n{chunk}"  【内置 f-string】标题 + 换行 + 块内容
            # 标题补到**每一块**前面，而不是只补在第一块：
            #   改之前是"标题+正文一起切"，结果标题只落在第 0 块里，
            #   后面几块光看正文根本不知道属于哪篇新闻，检索时白白丢掉线索。
            #   标题是整篇语义密度最高的部分，每块都带上，命中率明显不同。
            metas.append({
                # 做什么：往 metas 列表里追加一个字典 —— 这一块的"档案袋"
                # 逐词：
                #   .append( { ... } )  【内置】append 里放一个花括号 = 追加一个字典
                #   { }                 【内置】花括号 = 字典（键值对集合）
                #   为什么每块都要给一个档案袋：因为 Chroma 要求"每块内容都配一份元数据"，
                #     数量必须一一对应
                "news_id": news.id,
                # 逐词：
                #   "news_id"   【自起名】键名（我们自己定的），意思是"这篇新闻在 MySQL 里的 id"
                #               ⚠️ 这个名字后面很多地方要用到（get_hashes 会读它、delete_news 会按它删），
                #                  改名字要全局搜一遍
                #   :           【内置】字典里"键: 值"的分隔符
                #   news.id     【本地】News 对象身上的 id 属性
                # 哪篇新闻（检索时用来去重/展示）
                "title": news.title,
                # 逐词：
                #   "title"     【自起名】键名 → 标题
                #   news.title  【本地】新闻的标题
                # 标题（前端来源卡片上显示的就是它）
                "category": category_name,
                # 逐词：
                #   "category"       【自起名】键名 → 分类
                #   category_name    【自起名】上面元组解包出来的那个变量
                #   ⚠️ 注意它**不是** news.category（News 对象上没有这个属性）——
                #      分类名是从**另一张表**联表查出来的，所以是个独立的变量
                "content_hash": fp,
                # 逐词：
                #   "content_hash"  【自起名】键名。content（内容）+ hash（哈希/指纹）
                #   fp              【自起名】上面算出来的指纹
                # 它的用途：下次建索引时，get_hashes() 会把它读出来比对，
                #   一样就跳过 —— 这就是"增量索引"能省时间的原理
            })
            # 每块的"档案袋"：命中时靠它告诉用户答案出自哪篇新闻

    for news_id in old_hashes:
        # 做什么：遍历"库里已有指纹"的所有键，找出该清理的孤儿数据
        # 逐词：
        #   for ... in old_hashes  【内置】直接 for 一个字典时，遍历到的是它的**键**（keys）。
        #                          ⚠️ 不是值！想同时拿键和值要写 old_hashes.items()
        #   所以这里的 news_id 每轮就是一个"向量库里存在的新闻 id"
        if news_id not in seen:
            # 做什么：如果这个 id 不在"MySQL 里还存在的 id 集合"里 → 它已经被删了
            # 逐词：
            #   not in  【内置】"不在里面"
            #   seen    【自起名】上面循环里记下的"MySQL 里还存在的 id"集合
            #   两个集合的含义对照：
            #      old_hashes 的键 = 向量库里有的 id
            #      seen            = MySQL 里有的 id
            #      "向量库有、MySQL 没有" = 孤儿数据 → 该删
            await asyncio.to_thread(vectorstore.delete_news, news_id)
            # 做什么：把这个孤儿 id 在向量库里的块删掉
            # 逐词：await / asyncio.to_thread / vectorstore.delete_news 同上（不带括号）
            removed += 1
            # 做什么：清理计数器 +1
            # 逐词：+= 【内置】自增
    # MySQL 里已经删掉、向量库里还留着的新闻，顺手清掉
    # （不做这一步的后果：向量库里留着已删除新闻的内容，用户搜到会看到"不存在的新闻"）

    if texts:
        # 做什么：判断"这次到底有没有东西要写"
        # 逐词：
        #   if texts  【内置】非空列表在条件里算"真" → 所以这句等于"如果 texts 里有元素"
        #   为什么要判断：如果一篇都没变化，texts 是空列表，
        #     那就没必要调向量化接口（省钱、省时间），也没必要写库
        embeddings = await asyncio.to_thread(embed_texts, texts)
        # 做什么：把这一批文本全部转成向量
        # 逐词：
        #   embeddings  【自起名】"嵌入向量"的复数 → 一批向量
        #   await            【内置】等它跑完
        #   asyncio.to_thread【标准库】丢线程池 —— 因为 embed_texts 内部是
        #                    同步的 httpx.post（等着网络返回时会把主线程卡住）
        #   embed_texts      【本地】utils/embedding.py 的函数，**不带括号**
        #   texts            【自起名】要转的文本列表（作为参数跟在函数名后面）
        # 返回的向量个数和 texts 个数一样多，顺序也一一对应
        await asyncio.to_thread(vectorstore.add, ids, texts, embeddings, metas)
        # 做什么：把四个列表一起写进向量库
        # 逐词：
        #   vectorstore.add  【本地】utils/vectorstore.py 的 add()（不带括号）
        #   ids / texts / embeddings / metas
        #         → 四个列表，**必须一样长、位置一一对应**（ids[0] 描述的就是 texts[0]）
        #   ⚠️ 为什么整批一起写、而不是一条条写：
        #       ① 减少"打开连接、发请求"的开销，快得多
        #       ② 保证这一批是"原子的"语义（要么一起写进去，要么都没写）
        # 批量向量化 + 写库（批量比一条条快得多）

    return {
        # 做什么：返回一个字典，把本次统计结果交给调用方
        # 逐词：
        #   return          【内置】返回
        #   { ... }         【内置】花括号 = 字典
        #   ⚠️ 注意这里**没有 return 类型标注**的问题（函数签名上写了 -> dict）
        # 调用方：【本地】scripts/ingest_news.py 会把这几项打印出来
        "total": len(rows),
        # 逐词：
        #   "total"  【自起名】键名 → "总条数"
        #   len(rows)  【内置】len = length → 数一数 rows 里有几项（也就是新闻总条数）
        "added": added,
        # 新增了几篇
        "updated": updated,
        # 更新了几篇（内容变了、先删旧块再写新块的）
        "skipped": skipped,
        # 跳过了几篇（指纹没变，增量省下来的工作量就看这个数）
        "removed": removed,
        # 清理了几个孤儿（MySQL 已删、向量库还留着的）
        "chunks": len(texts),
        # 逐词：
        #   "chunks"     【自起名】键名 → "这次写入的块数"
        #   len(texts)   【内置】数一数要写的文本条数（因为一块对应一条 texts）
        # 本次真正写入向量库的块数
    }


# ============================================================
# 第四部分：② 检索 retrieve —— 拿问题去找相关新闻，并判断"要不要带资料"
# ============================================================


async def retrieve(question: str, top_k: int = None) -> dict:
    # 做什么：定义异步函数，输入问题，输出"检索结果 + 模式判断"
    # 逐词：
    #   async def  【内置】定义协程函数（必须 await 调用）
    #   retrieve   【自起名】"检索、取回"。这个词在检索领域很常用（information retrieval）
    #   question   【自起名】参数：用户的问题
    #   top_k: int = None
    #         → 【自起名 + 内置】top_k = top（前几名）+ k（个数）；
    #           : int 类型标注说"该传整数"；= None 表示"可以不传，不传就是 None"
    #         ⚠️ 为什么默认值写 None 而不是 4：
    #             写 None 才能区分"调用方没传"（用配置默认值）和"调用方明确传了 4"。
    #             这两个意图不同，写死 4 就分不出来了
    #   -> dict    【内置】返回一个字典
    """
    检索并判断"这次要不要带资料"
    question：用户问题
    top_k：检索几块（不传用配置里的默认值）
    返回：{"hits": 资料列表, "top_score": 最高相似度, "mode": "rag" 或 "ai"}
    """
    k = top_k or TOP_K
    # 做什么：确定"这次要取几块"
    # 逐词：
    #   k      【自起名】沿用检索领域的习惯字母 k（= 个数）；这里只是把 top_k 缩写成 k，下面好写
    #   top_k  【自起名】函数参数
    #   or     【内置】"左边为假就取右边"。None 是假值，所以：
    #             调用方没传（None）→ 取 TOP_K（配置里的 4）
    #             调用方传了 3      → 取 3
    #           ⚠️ 注意 or 的一个坑：如果调用方传 0，0 也是假值，会被当成"没传"而换成 4。
    #              这里传 0 本来也没意义（取 0 块没法回答），所以可以接受
    #   TOP_K  【本地】config/rag_conf.py 里的常量，值 4
    # 没传就按默认条数

    if is_small_talk(question):
        # 做什么：先判断这是不是"打招呼"这类闲聊
        # 逐词：
        #   is_small_talk  【本地】本文件上面定义的那个函数（返回 True/False）
        #   question       【自起名】函数参数，原样传进去
        #   ⚠️ 为什么这个判断要放在最前面：打招呼的问题根本不需要查新闻库，
        #      提前返回能省掉一次向量化 API 调用（省钱）+ 一次向量库检索（省时间），
        #      而且回答更自然（不会被硬塞的新闻带歪）
        return {"hits": [], "top_score": 0.0, "mode": "ai"}
        # 做什么：直接返回"没有资料、分数 0、走 ai 模式"
        # 逐词：
        #   return  【内置】返回 —— 函数到此结束，后面的向量化、检索都不会执行
        #   hits: []      【自起名 + 内置】空列表 = "这次没有参考资料"
        #   top_score: 0.0 【自起名 + 内置】0.0 是小数（写 .0 是为了说明它是浮点数）。
        #                  给 0 是因为"压根没检索"，不是"检索了但不像"
        #   mode: "ai"    【自起名】字符串 "ai" —— 注意是**字符串**，不是变量，
        #                  所以带引号。取值只有两种："rag" 和 "ai"
        #   这个返回值的形状必须和函数最后那个 return 完全一致，
        #   否则调用方 routers/ragchat.py 取 result["hits"] 时会 KeyError
    # 打招呼之类的问题：不用查库，直接当普通 AI 回答

    vector = await asyncio.to_thread(embed_texts, [question])
    # 做什么：把用户的问题也转成向量
    # 逐词：
    #   vector   【自起名】"向量"，这里装的是"一个列表，里面有一个向量"
    #   await    【内置】等它跑完
    #   asyncio.to_thread  【标准库】丢线程池（embed_texts 内部是同步的 httpx.post）
    #   embed_texts        【本地】utils/embedding.py 的函数（不带括号）
    #   [question]         【内置】注意**套了方括号** —— 因为 embed_texts 要的是"文本列表"，
    #                      所以传 [question] 而不是 question。
    #                      它返回的也就是"向量列表"（哪怕只有一条），所以下面要取 [0]
    # ⚠️ 这一步是必须的：文档和问题必须在**同一个坐标系**里才能比远近。
    #    所以问题必须用**同一个模型**（EMBEDDING_MODEL）转成向量，
    #    换了模型就得重建整个索引（config/rag_conf.py 的注释里专门强调了这点）
    # 把问题也转成向量（必须和入库时用同一个模型，否则不在一个坐标系）

    hits = await asyncio.to_thread(vectorstore.search, vector[0], k)
    # 做什么：拿问题向量去库里找最像的 k 块
    # 逐词：
    #   hits   【自起名】"命中的那些块"，返回一个列表，每项是 {text, meta, score}
    #   await              【内置】等它跑完
    #   asyncio.to_thread  【标准库】丢线程池（Chroma 的 search 是同步阻塞的）
    #   vectorstore.search 【本地】utils/vectorstore.py 的 search()（不带括号）
    #   vector[0]          【内置】取出列表里的第一项 —— 也就是"问题那个向量"。
    #                      ⚠️ 这一步最容易写错：直接传 vector 会报错，
    #                         因为它要的是"一个向量"，不是"装着向量的列表"。
    #                         这个"多套了一层"的坑，根源在 embed_texts 支持批量
    #   k                  【自起名】上面算好的"要几条"
    # 返回的结果已经按相似度从高到低排好序了（排序在 vectorstore.search 里做的）
    # 去库里找最像的 k 块

    top_score = hits[0]["score"] if hits else 0.0
    # 做什么：取"最高相似度"，作为"库里到底有没有相关内容"的判断依据
    # 逐词：
    #   top_score   【自起名】top（最高）+ score（分数）→ 最像的那一块的相似度
    #   hits[0]     【内置】取列表第 0 项（也就是最像的那一块）——
    #               因为 search 已经排好序了，第 0 个就是最高的
    #   ["score"]   【内置】字典取值：取出它里面的 score 字段
    #   A if 条件 else B  【内置】这叫"三元表达式"：条件成立取 A，否则取 B。
    #               等价于：if hits: top_score = hits[0]["score"] else: top_score = 0.0
    #   else 0.0 那个兜底**必须有**：如果向量库是空库，hits 是空列表，
    #     直接写 hits[0] 会 IndexError 崩掉
    # 最高相似度：判断"新闻库里到底有没有相关内容"的依据
    #   为什么看最高分而不是平均分：只要有 1 块真的相关，就该把资料带上；
    #   看平均分会被后面几块不相关的拉低，反而漏掉真正相关的那一篇

    mode = "rag" if top_score >= SCORE_THRESHOLD else "ai"
    # 做什么：决定这次走"带资料"还是"当普通 AI"——整个降级设计就这一行
    # 逐词：
    #   mode    【自起名】"模式"
    #   "rag"   【内置字符串】三个字母，注意带引号（是字符串，不是变量名）。
    #           RAG = Retrieval-Augmented Generation（检索增强生成）
    #   if top_score >= SCORE_THRESHOLD
    #           → 【内置】三元表达式的条件部分。>= 是"大于等于"（注意等号在后面）
    #           → 【本地】SCORE_THRESHOLD 来自 config/rag_conf.py，值 0.55
    #   else "ai"
    #           → 条件不成立时取 "ai"（普通 AI 回答）
    # 达标 → rag 模式（把新闻当参考资料发给模型，让它标注来源）
    # 不达标 → ai 模式（当成普通 AI 助手回答，不硬塞不相关的新闻）
    # 为什么是"降级"而不是"拒答"：
    #   拒答（低于阈值就说"我不知道"）体验很差 —— 用户打招呼都被拦；
    #   降级之后，判错的代价从"答不出来"降到"只是没带来源"。见 config 里那三道保险

    return {"hits": hits, "top_score": top_score, "mode": mode}
    # 做什么：把三个结果打包返回
    # 逐词：
    #   hits       【自起名】命中的块列表（调用方会把它拼进提示词、并挑出标题当来源卡片）
    #   top_score  【自起名】最高相似度（发给前端，方便看"离阈值还差多少"）
    #   mode       【自起名】"rag" 或 "ai"（决定后面走哪条路，也是 SSE 第一条事件的内容）
    # ⚠️ 键名（"hits"/"top_score"/"mode"）是**约定**：调用方 routers/ragchat.py
    #    按这三个名字取值。改名字必须两边一起改，否则 KeyError


# ============================================================
# 结构化问答：向量检索答不了的那一类问题（2026-09-20 新增）
#
# 【为什么要有这一段 —— 一个真实的"答不了"案例】
#   用户问："你一共有多少篇新闻" / "最长的一篇新闻是什么"
#   页面上的回答标签全是"通用回答"，内容是"我没有固定的新闻篇数哦"
#   —— 说明它压根没检索到新闻，直接降级成普通 AI 聊天了。
#
#   根因不是阈值调得不对，而是【向量检索本来就答不了这类问题】：
#     向量检索做的是"找出和问题最像的那几块文本"，
#     而"一共有多少篇""最长的是哪一篇"需要的是 COUNT / ORDER BY ——
#     属于【聚合和排序】，不是【找相似】。
#     就算检索到的 4 块新闻全都相关，模型也无从知道库里的总数是多少。
#
#   所以补一条"直查数据库"的通道：
#     命中统计类问题 → 直接查 SQL → 把查到的数字当成"资料"
#     没命中         → 走原来的向量检索（老逻辑一行都没动）
#
# ★ 和项目二对比着看（面试很好用）
#   项目二（Agent）：调哪个工具、调几次，由【模型】决定
#   项目一（本文件）：判断规则是【我写死的】—— 只能覆盖我预先想到的那几种问法
#   这就是"Agent 里模型做决定、RAG 里我做决定"最具体的体现。
#   反过来看也成立：项目二能答的问题类型是开放的，项目一只覆盖下面这几种。
# ============================================================

# 统计类问题的关键词表。
#
# 【判断顺序：先"最长"，再"分类分布"，最后"总数"】——
# 和上面 is_small_talk 一个思路：优先认更具体的意图。
# 因为"最长的那篇新闻有多少字"同时含"最长"和"多少"，按"最长"处理才对。
LONGEST_KEYWORDS = ("最长", "篇幅最长", "字数最多", "内容最多")

DISTRIBUTION_KEYWORDS = ("各分类", "每个分类", "分类分布", "分类各多少", "哪几类")

# 总数类：只认"多少 / 几篇 / 几条"这种明确的【数量询问】
# ⚠️ 不要写成"一共 / 总共"：用户说"一共有哪些科技新闻"是在要【列表】，
#    向量检索能处理，硬判成"统计"反而答非所问。
COUNT_KEYWORDS = ("多少", "几篇", "几条")


def detect_structured_intent(question: str) -> str:
    """
    判断这个问题是不是"统计 / 排序类"，也就是"向量检索答不了、必须直查数据库"的那一类。

    返回值（返回空串表示不是统计类 → 交回原来的向量检索）：
        "longest"        问哪一篇正文最长
        "distribution"   问各分类分别有多少篇
        "count"          问总数（或某个分类的数量）
    """
    q = (question or "").strip()

    if not q:
        return ""

    for word in LONGEST_KEYWORDS:
        if word in q:
            return "longest"

    for word in DISTRIBUTION_KEYWORDS:
        if word in q:
            return "distribution"

    for word in COUNT_KEYWORDS:
        if word in q:
            return "count"

    return ""


async def build_structured_hits(
    db: AsyncSession,
    intent: str,
    question: str,
) -> list:
    """
    直查数据库，把统计结果拼成一条"资料"。

    返回结构和向量检索的 hits 完全一致：
        [{"text": 资料正文, "meta": {"title": ..., "category": ...}, "score": 1.0}]

    为什么故意做得一模一样：
      这样 routers/ragchat.py 那边【一行都不用改】就能用 ——
      它只认 hits 这个结构，不关心资料是"检索来的"还是"查库来的"。
    """
    if intent == "count":
        # 先看看问题里有没有点名某个分类："科技类有多少篇" → 只数科技这一个
        category_names = (await db.execute(select(Category.name))).scalars().all()

        matched = [name for name in category_names if name in question]

        if len(matched) == 1:
            total = await db.scalar(
                select(func.count())
                .select_from(News)
                .join(Category, News.category_id == Category.id)
                .where(Category.name == matched[0])
            )
            text = f"数据库里【{matched[0]}】分类的新闻一共 {total} 篇。"
            title = f"{matched[0]}分类的篇数"
            category = matched[0]
        else:
            total = await db.scalar(select(func.count()).select_from(News))
            text = f"数据库里一共有 {total} 篇新闻。"
            title = "新闻总数"
            category = "全部"

    elif intent == "distribution":
        rows = (
            await db.execute(
                select(Category.name, func.count())
                .select_from(News)
                .join(Category, News.category_id == Category.id)
                .group_by(Category.name)
            )
        ).all()

        # 按篇数从多到少排一下：模型读起来有条理，也免得它自己乱排
        ordered = sorted(rows, key=lambda item: -item[1])
        parts = [f"{name} {count} 篇" for name, count in ordered]
        total = sum(count for _, count in rows)

        text = f"数据库里一共有 {total} 篇新闻，各分类：" + "、".join(parts) + "。"
        title = "各分类的新闻篇数"
        category = "全部"

    elif intent == "longest":
        # 按【正文字数】降序取第一条 —— 这就是"最长的一篇"
        # ⚠️ 用 char_length 而不是 length：后者按【字节】算，一个汉字算 3 个，
        #    虽然排序结果碰巧一样，但报出来的"字数"会大三倍，模型一开口就说错。
        #    coalesce 是"正文为 NULL 时当空字符串"，免得 NULL 参与排序时跑到最前面。
        length_column = func.char_length(func.coalesce(News.content, ""))

        row = (
            await db.execute(
                select(News, Category.name)
                .join(Category, News.category_id == Category.id)
                .order_by(length_column.desc())
                .limit(1)
            )
        ).first()

        if row is None:
            text = "数据库里目前没有任何新闻。"
            title = "最长的一篇"
            category = "全部"
        else:
            news, category_name = row
            length = len(news.content or "")

            text = (
                f"正文最长的一篇是《{news.title}》"
                f"（新闻编号 {news.id}，分类 {category_name}，正文 {length} 字）。"
            )
            title = f"最长的一篇：《{news.title}》"
            category = category_name

    else:
        # 传进来的 intent 不在支持范围内 → 什么都不给，让调用方走原来的路
        return []

    return [
        {
            "text": text,
            "meta": {"title": title, "category": category},
            # score 固定 1.0：表示"这条资料是直查数据库得到的，不是检索来的"。
            # 前端会把它显示在来源卡片上，方便一眼看出这次是查库不是检索。
            "score": 1.0,
        }
    ]
