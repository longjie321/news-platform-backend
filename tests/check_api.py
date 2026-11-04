# 区域：接口冒烟测试（真实服务、真实数据库、真实 AI 接口）
# 作用：验证整条链路真的跑得通

import asyncio
import json
import os
import sys

import httpx

# 把项目根目录加进模块搜索路径，这样从任何目录执行本脚本都能 import 到项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:8000"
passed, failed = [], []


def check(name, ok, detail=""):
    """记录一条检查结果"""
    (passed if ok else failed).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


async def ask(client, question, history=None):
    """
    调一次 /api/rag/chat，收完整个 SSE 流
    返回：{"mode":..., "sources":[...], "answer": 回答全文, "events": 收到的事件名列表}
    """
    body = {"question": question}
    if history:
        body["history"] = history

    info = {"mode": None, "sources": [], "top_score": 0, "answer": "", "events": []}

    async with client.stream("POST", f"{BASE}/api/rag/chat", json=body, timeout=120) as resp:
        if resp.status_code != 200:
            return info

        buffer = ""
        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                raw, buffer = buffer.split("\n\n", 1)

                event, data = "message", ""
                for line in raw.split("\n"):
                    if line.startswith("event: "):
                        event = line[7:].strip()
                    elif line.startswith("data: "):
                        data += line[6:]

                if not data:
                    continue
                info["events"].append(event)
                payload = json.loads(data)

                if event == "mode":
                    info["mode"] = payload["mode"]
                    info["sources"] = payload["sources"]
                    info["top_score"] = payload["top_score"]
                elif event == "delta":
                    info["answer"] += payload["content"]

    return info


async def main():
    print("=" * 62)
    print("  接口冒烟测试（真实服务 + 真实数据库 + 真实 AI 接口）")
    print("=" * 62)

    async with httpx.AsyncClient(timeout=60) as client:
        # ---------- 1. 服务活着吗 ----------
        print("\n[1] 服务健康检查")
        r = await client.get(f"{BASE}/hello")
        check("GET /hello", r.status_code == 200)

        # ---------- 2. 原有业务没被改坏 ----------
        print("\n[2] 原有业务回归")
        r = await client.get(f"{BASE}/api/news/categories")
        check("GET /api/news/categories", r.status_code == 200 and len(r.json()["data"]) == 8)
        r = await client.get(f"{BASE}/api/news/list", params={"categoryId": 1, "pageSize": 3})
        check("GET /api/news/list", r.status_code == 200 and len(r.json()["data"]["list"]) == 3)

        # ---------- 3. 知识库统计 ----------
        print("\n[3] 知识库统计")
        r = await client.get(f"{BASE}/api/rag/stats")
        chunks = r.json()["data"]["chunk_count"]
        check("GET /api/rag/stats", r.status_code == 200 and chunks > 0, f"{chunks} 块")

        # ---------- 4. 问新闻里的事 → 应该是 rag 模式，带来源 ----------
        print("\n[4] 问新闻里的事（应该带来源回答）")
        news_q = await ask(client, "梅西在世界杯上表现怎么样")
        check("走 rag 模式（带资料）", news_q["mode"] == "rag",
              f"mode={news_q['mode']} 相似度={news_q['top_score']}")
        check("返回了来源列表", len(news_q["sources"]) > 0,
              "、".join(s["title"][:12] for s in news_q["sources"][:3]))
        check("回答非空", len(news_q["answer"]) > 10, f"{len(news_q['answer'])} 字")
        check("回答里标注了来源", "资料" in news_q["answer"])
        check("事件顺序是 mode → delta → done",
              news_q["events"][0] == "mode" and news_q["events"][-1] == "done"
              and news_q["events"].count("delta") > 3,
              f"{news_q['events'].count('delta')} 个正文碎片")

        # ---------- 5. 闲聊 → 应该是 ai 模式，能正常回答（关键改动） ----------
        print("\n[5] 闲聊（关键：不能再拒答，要正常回答）")
        chat_q = await ask(client, "你好，你是谁")
        check("走 ai 模式（不带资料）", chat_q["mode"] == "ai",
              f"mode={chat_q['mode']} 相似度={chat_q['top_score']}")
        check("正常回答了，没有拒答", len(chat_q["answer"]) > 10, chat_q["answer"][:50].replace("\n", " "))
        check("ai 模式不返回来源", len(chat_q["sources"]) == 0)

        # ---------- 6. 多轮对话 ----------
        print("\n[6] 多轮对话")
        history = [
            {"role": "user", "content": "梅西在世界杯上表现怎么样"},
            {"role": "assistant", "content": news_q["answer"]},
        ]
        multi = await ask(client, "那法国队呢", history=history)
        check("多轮提问能正常返回", len(multi["answer"]) > 5, multi["answer"][:50].replace("\n", " "))

        # ---------- 7. 参数校验 ----------
        print("\n[7] 参数校验")
        r = await client.post(f"{BASE}/api/rag/chat", json={"question": ""})
        check("空问题被拦下（422）", r.status_code == 422)
        r = await client.post(f"{BASE}/api/rag/chat", json={"question": "x", "top_k": 999})
        check("top_k 超上限被拦下（422）", r.status_code == 422)

        # ---------- 8. 旧 bug 回归（这次一起修掉的） ----------
        print("\n[8] 旧 bug 回归")

        r = await client.get(f"{BASE}/api/news/list", params={"categoryId": 1, "page": 0})
        check("page=0 被拦下（原来 500）", r.status_code == 422,
              f"{r.status_code} {r.json().get('message', '')}")

        body = r.json()
        check("422 也走统一响应体（原来只返回 detail）",
              {"code", "message", "data"} <= set(body.keys()) and body["code"] == 422,
              str(list(body.keys())))

        r = await client.get(f"{BASE}/api/news/list", params={"categoryId": 1, "pageSize": 0})
        check("pageSize=0 被拦下（原来除零 500）", r.status_code == 422)

        r = await client.get(f"{BASE}/api/user/info")
        check("没带 token 返回 401（原来 422）", r.status_code == 401,
              f"{r.status_code} {r.json().get('message', '')}")

        r = await client.get(f"{BASE}/api/news/list", params={"categoryId": 1, "page": 1, "pageSize": 1})
        item = r.json()["data"]["list"][0]
        check("列表返回 publishTime（原来给的是 publish_time，前端发布时间一直是空的）",
              "publishTime" in item, "、".join(item.keys()))
        check("列表不再返回 content 全文", "content" not in item)

        # 注册一个临时用户来测收藏和改密
        import random
        username = f"checker{random.randint(100000, 999999)}"
        await client.post(f"{BASE}/api/user/register", json={"username": username, "password": "abc123456"})
        r = await client.post(f"{BASE}/api/user/login", json={"username": username, "password": "abc123456"})
        auth = {"Authorization": f"Bearer {r.json()['data']['token']}"}

        await client.post(f"{BASE}/api/favorite/add", json={"newsId": 2}, headers=auth)
        r = await client.post(f"{BASE}/api/favorite/add", json={"newsId": 2}, headers=auth)
        message = r.json().get("message", "")
        check("重复收藏提示正确（原来错报'用户名已存在'）", "用户名已存在" not in message, message)

        r = await client.put(f"{BASE}/api/user/password",
                             json={"oldPassword": "wrongpass", "newPassword": "new123456"}, headers=auth)
        check("旧密码错误返回 400（原来 500）", r.status_code == 400,
              f"{r.status_code} {r.json().get('message', '')}")

        r = await client.post(f"{BASE}/api/history/add", json={"news_id": 3}, headers=auth)
        check("history/add 收下划线字段（原来 422）", r.status_code == 200, str(r.status_code))

        # 浏览量 +1 不应该顺手改掉 updated_at
        from sqlalchemy import text
        from config.db_conf import AsyncSessionLocal

        async def read_updated_at(news_id):
            async with AsyncSessionLocal() as session:
                result = await session.execute(text("SELECT updated_at FROM news WHERE id = :i"), {"i": news_id})
                return result.scalar_one()

        target = 5
        before_time = await read_updated_at(target)
        await client.get(f"{BASE}/api/news/detail", params={"id": target})
        after_time = await read_updated_at(target)
        check("看一次新闻不会改 updated_at（原来会被改掉）", before_time == after_time,
              f"{before_time} -> {after_time}")

    print("\n" + "=" * 62)
    print(f"  结果：通过 {len(passed)} 项，失败 {len(failed)} 项")
    for name in failed:
        print(f"    - {name}")
    print("=" * 62)

    from config.db_conf import async_engine
    await async_engine.dispose()
    # 关闭数据库连接池再退出，否则事件循环先关、连接后关，会打一堆无关的告警

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
