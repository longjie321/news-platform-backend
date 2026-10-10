# toutiao_backend Dockerfile
# 把 FastAPI 项目打包成镜像，让任何机器 "docker run" 就能跑起来

# 1. 基础镜像：Python 3.13 精简版（体积小、够用）
FROM python:3.13-slim

# 2. 设置工作目录（容器里的 /app 目录）
WORKDIR /app

# 3. 先单独复制 requirements.txt 进来
#    好处：依赖文件不变时，Docker 缓存这层，build 更快
COPY requirements.txt .

# 4. 安装 Python 依赖
#    --no-cache-dir：不缓存 pip 包，让镜像更小
RUN pip install --no-cache-dir -r requirements.txt

# 5. 复制整个项目代码到容器
COPY . .

# 6. 告诉 Docker 容器会监听 8000 端口（FastAPI 默认）
EXPOSE 8000

# 7. 容器启动时执行的命令
#    uvicorn 是 FastAPI 的 ASGI 服务器
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
