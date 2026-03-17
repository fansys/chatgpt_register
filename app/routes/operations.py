from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import read_config
from app.storage import get_token_dir
from app.tasks import create_task
from app.workers import run_register, run_refresh, run_batch_refresh
from app.lib.upload import CpaUploader

router = APIRouter(prefix="/api", tags=["operations"])
_executor = ThreadPoolExecutor(max_workers=8)


class RegisterRequest(BaseModel):
    count: int = 1
    concurrency: int = 1


class RefreshRequest(BaseModel):
    email: str


class BatchRefreshRequest(BaseModel):
    emails: list[str]


class UploadTokenRequest(BaseModel):
    email: str


class BatchUploadRequest(BaseModel):
    emails: list[str]


@router.post("/register")
def start_register(req: RegisterRequest):
    if req.count < 1:
        raise HTTPException(status_code=400, detail="count must be >= 1")
    task = create_task("register")
    _executor.submit(run_register, task, req.count, max(1, req.concurrency))
    return {"task_id": task["id"]}


@router.post("/refresh")
def start_refresh(req: RefreshRequest):
    if not req.email:
        raise HTTPException(status_code=400, detail="email required")
    task = create_task("refresh")
    _executor.submit(run_refresh, task, req.email)
    return {"task_id": task["id"]}


@router.post("/refresh-batch")
def start_batch_refresh(req: BatchRefreshRequest):
    if not req.emails:
        raise HTTPException(status_code=400, detail="emails required")
    task = create_task("refresh-batch")
    _executor.submit(run_batch_refresh, task, req.emails)
    return {"task_id": task["id"]}


@router.post("/upload-token")
def upload_token(req: UploadTokenRequest):
    token_path = get_token_dir() / f"{req.email}.json"
    if not token_path.exists():
        raise HTTPException(status_code=404, detail="token file not found")
    cfg = read_config()
    if not cfg.get("upload_api_url"):
        raise HTTPException(status_code=400, detail="upload_api_url not configured")
    try:
        CpaUploader.from_config().upload_token_json(str(token_path))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-batch")
def upload_batch(req: BatchUploadRequest):
    cfg = read_config()
    if not cfg.get("upload_api_url"):
        raise HTTPException(status_code=400, detail="upload_api_url not configured")
    uploader = CpaUploader.from_config()
    token_dir = get_token_dir()
    results: dict[str, str] = {}
    for email in req.emails:
        token_path = token_dir / f"{email}.json"
        if not token_path.exists():
            results[email] = "not_found"
            continue
        try:
            uploader.upload_token_json(str(token_path))
            results[email] = "ok"
        except Exception as e:
            results[email] = str(e)
    return results
