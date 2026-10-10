import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件（敏感信息放 .env，不写死在代码里）
# .env 文件已被 .gitignore 忽略，不会提交到 git
load_dotenv()

# DeepSeek AI 配置：key 从环境变量读取
# 本地开发：在项目根目录创建 .env 文件，写入 DEEPSEEK_API_KEY=sk-你的key
# 生产/容器：用环境变量传入（docker run -e DEEPSEEK_API_KEY=xxx）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
