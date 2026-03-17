from fastapi import APIRouter, HTTPException

from app.tasks import get_task, list_tasks

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def get_tasks():
    return list_tasks()


@router.get("/{tid}")
def get_task_by_id(tid: str):
    task = get_task(tid)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task
