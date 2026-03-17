FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖（curl_cffi 需要 libcurl）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码与模板配置（data/ 和 config.json 由 volume 挂载）
COPY app/ ./app/
COPY static/ ./static/
COPY server.py .
COPY config.example.json .
COPY entrypoint.sh .

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
