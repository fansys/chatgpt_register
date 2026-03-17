"""
FastAPI 管理服务 - ChatGPT 批量注册工具
依赖: pip install fastapi uvicorn aiofiles
"""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from app.workers import install_tee_stream
from app.routes.config import router as config_router
from app.routes.accounts import router as accounts_router
from app.routes.tokens import router as tokens_router
from app.routes.tasks import router as tasks_router
from app.routes.operations import router as operations_router

# 安装 stdout 路由（将工作线程 print 捕获到任务日志）
install_tee_stream()

app = FastAPI(title="ChatGPT Register Manager")

for _router in [config_router, accounts_router, tokens_router, tasks_router, operations_router]:
    app.include_router(_router)

STATIC_DIR = BASE_DIR / "static"


def _serve_page(filename: str) -> HTMLResponse:
    path = STATIC_DIR / filename
    if not path.exists():
        return HTMLResponse(f"<h1>{filename} not found</h1>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
def page_config():
    return _serve_page("index.html")


@app.get("/register", response_class=HTMLResponse)
def page_register():
    return _serve_page("register.html")


@app.get("/accounts", response_class=HTMLResponse)
def page_accounts():
    return _serve_page("accounts.html")


@app.get("/tokens", response_class=HTMLResponse)
def page_tokens():
    return _serve_page("tokens.html")


# 静态文件挂载（必须在页面路由之后）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8009, reload=True)
