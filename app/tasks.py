"""任务存储（内存 + 文件持久化）"""

import json
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent.parent.resolve()
TASKS_FILE = BASE_DIR / "data/tasks.json"

_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()
_LOG_KEEP = 500  # 持久化时每个任务最多保留的日志行数


# ── 内部工具 ──────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _save_tasks() -> None:
    """将已完成任务持久化到文件（在锁外调用）。"""
    try:
        snapshot: list = []
        for t in _tasks.values():
            t_copy = dict(t)
            logs = t_copy.get("logs", [])
            if len(logs) > _LOG_KEEP:
                t_copy = {**t_copy, "logs": logs[-_LOG_KEEP:]}
            snapshot.append(t_copy)
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_tasks() -> None:
    """启动时从文件加载历史任务。"""
    if not TASKS_FILE.exists():
        return
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            items: list = json.load(f)
        for task in items:
            _tasks[task["id"]] = task
    except Exception:
        pass


# ── 公开 API ──────────────────────────────────────────────────

def create_task(task_type: str) -> dict:
    tid = uuid.uuid4().hex[:8]
    task: dict = {
        "id": tid,
        "type": task_type,
        "status": "pending",
        "logs": [],
        "started": _now_str(),
        "ended": None,
        "result": None,
    }
    with _tasks_lock:
        _tasks[tid] = task
    return task


def task_log(task: dict, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    task["logs"].append(f"[{ts}] {msg}")


def finish_task(task: dict, status: str, result: Any = None) -> None:
    task["status"] = status
    task["ended"] = _now_str()
    task["result"] = result
    with _tasks_lock:
        _save_tasks()


def get_task(tid: str) -> dict | None:
    with _tasks_lock:
        return _tasks.get(tid)


def list_tasks() -> list:
    with _tasks_lock:
        return list(_tasks.values())


# 启动时加载历史任务
_load_tasks()
