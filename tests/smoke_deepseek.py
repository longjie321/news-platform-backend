# 区域：DeepSeek 连通性冒烟脚本（手动执行，不进 pytest）
# 作用：单独验证「openai 库 + DeepSeek 接口 + .env 里的 key」这条链路是通的

import os
import sys

# 把项目根目录加进模块搜索路径，这样从任何目录执行本脚本都能 import 到项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI

from config.ai_conf import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEFAULT_MODEL


def main():
    if not DEEPSEEK_API_KEY:
        print("[跳过] 没读到 DEEPSEEK_API_KEY")
        print("       请在项目根目录的 .env 文件里填：DEEPSEEK_API_KEY=sk-你的key")
        return
    # 先判空再建 client：否则会抛一个很难看懂的 OpenAI 鉴权异常

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )
    # base_url 和模型都从 config/ai_conf.py 取，保证「这里测通 = 业务接口也能通」

    print(f"[信息] 模型={DEFAULT_MODEL}  地址={DEEPSEEK_BASE_URL}")

    resp = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{"role": "user", "content": "用一句话介绍你自己"}],
    )
    print("[成功] DeepSeek 回答:", resp.choices[0].message.content)


if __name__ == "__main__":
    main()
    # 加 __main__ 守卫：被别的代码 import 时不会自动发请求
