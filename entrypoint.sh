#!/bin/sh
set -e

CONFIG=/app/config.json
EXAMPLE=/app/config.example.json

if [ ! -f "$CONFIG" ]; then
    echo "[entrypoint] config.json 不存在，从 config.example.json 自动生成..."
    cp "$EXAMPLE" "$CONFIG"
    echo "[entrypoint] 已生成 config.json，请挂载宿主机文件或进入容器修改配置后重启。"
fi

mkdir -p /app/data/tokens

exec uvicorn server:app --host 0.0.0.0 --port 8000
