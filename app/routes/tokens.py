import json

from fastapi import APIRouter, HTTPException

from app.storage import parse_tokens, get_token_dir

router = APIRouter(prefix="/api", tags=["tokens"])


@router.get("/tokens")
def get_tokens():
    return parse_tokens()


@router.get("/tokens/{email:path}")
def get_token_detail(email: str):
    token_path = get_token_dir() / f"{email}.json"
    if not token_path.exists():
        raise HTTPException(status_code=404, detail="token file not found")
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
