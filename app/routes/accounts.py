from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.storage import parse_accounts, delete_account

router = APIRouter(prefix="/api", tags=["accounts"])


class BatchDeleteRequest(BaseModel):
    emails: list[str]


@router.get("/accounts")
def get_accounts():
    return parse_accounts()


@router.delete("/accounts/{email:path}")
def delete_account_route(email: str):
    try:
        delete_account(email)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/delete-batch")
def delete_accounts_batch(req: BatchDeleteRequest):
    errors: dict[str, str] = {}
    for email in req.emails:
        try:
            delete_account(email)
        except Exception as e:
            errors[email] = str(e)
    return {"deleted": len(req.emails) - len(errors), "errors": errors}
