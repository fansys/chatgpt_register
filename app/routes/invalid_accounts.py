from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.storage import parse_invalid_accounts, remove_invalid_account, delete_account

router = APIRouter(prefix="/api", tags=["invalid-accounts"])


class InvalidBatchDeleteRequest(BaseModel):
    emails: list[str]
    delete_local: bool = True


@router.get("/invalid-accounts")
def get_invalid_accounts():
    return parse_invalid_accounts()


@router.delete("/invalid-accounts/{email:path}")
def delete_invalid_account_route(email: str, delete_local: bool = True):
    try:
        if delete_local:
            delete_account(email)
        else:
            remove_invalid_account(email)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/invalid-accounts/delete-batch")
def delete_invalid_accounts_batch(req: InvalidBatchDeleteRequest):
    errors: dict[str, str] = {}
    for email in req.emails:
        try:
            if req.delete_local:
                delete_account(email)
            else:
                remove_invalid_account(email)
        except Exception as e:
            errors[email] = str(e)
    return {"deleted": len(req.emails) - len(errors), "errors": errors}
