from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import read_config, write_config

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    model_config = {"extra": "allow"}


@router.get("")
def get_config():
    return read_config()


@router.post("")
def update_config(body: ConfigUpdate):
    try:
        current = read_config()
        current.update(body.model_dump())
        write_config(current)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
