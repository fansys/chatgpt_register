"""文件存储：账号 & Token 读写"""

import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.config import BASE_DIR, read_config

_accounts_file_lock = threading.Lock()


# ── 路径辅助 ──────────────────────────────────────────────────

def get_accounts_file() -> Path:
    cfg = read_config()
    p = cfg.get("output_file", "registered_accounts.json")
    return Path(p) if Path(p).is_absolute() else BASE_DIR / p


def get_token_dir() -> Path:
    cfg = read_config()
    p = cfg.get("token_json_dir", "codex_tokens")
    return Path(p) if Path(p).is_absolute() else BASE_DIR / p


# ── Token 映射 ────────────────────────────────────────────────

def load_token_map() -> dict:
    """返回 email -> {last_refresh, expired} 的映射。"""
    token_dir = get_token_dir()
    result: dict = {}
    if not token_dir.exists():
        return result
    for jf in token_dir.glob("*.json"):
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
            email = data.get("email", jf.stem)
            result[email] = {
                "last_refresh": data.get("last_refresh", ""),
                "expired": data.get("expired", ""),
            }
        except Exception:
            pass
    return result


# ── 账号文件（JSON 格式） ──────────────────────────────────────

def _read_accounts_json(path: Path) -> list:
    """从 JSON 文件读取账号列表，并兼容旧 txt 格式自动迁移。"""
    if not path.exists():
        # 尝试从旧 txt 文件迁移
        txt_path = path.with_suffix(".txt")
        if txt_path.exists():
            return _migrate_from_txt(txt_path, path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _migrate_from_txt(txt_path: Path, json_path: Path) -> list:
    """将旧 ---- 分隔的 txt 账号文件迁移到 JSON 格式。"""
    accounts: list = []
    with open(txt_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            parts = line.split("----")
            email = parts[0].strip() if parts else ""
            if not email:
                continue
            accounts.append({
                "email": email,
                "chatgpt_pwd": parts[1].strip() if len(parts) > 1 else "",
                "email_pwd": parts[2].strip() if len(parts) > 2 else "",
                "status": parts[3].strip() if len(parts) > 3 else "",
            })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)
    txt_path.rename(txt_path.with_name(txt_path.name + ".bak"))
    return accounts


def _write_accounts_json(path: Path, accounts: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


def parse_accounts() -> list:
    path = get_accounts_file()
    token_map = load_token_map()
    raw_accounts = _read_accounts_json(path)
    result: list = []
    for acc in raw_accounts:
        email = acc.get("email", "")
        tok = token_map.get(email, {})
        result.append({
            "email": email,
            "chatgpt_pwd": acc.get("chatgpt_pwd", ""),
            "email_pwd": acc.get("email_pwd", ""),
            "status": acc.get("status", ""),
            "token_last_refresh": tok.get("last_refresh", ""),
            "token_expired": tok.get("expired", ""),
        })
    return result


def update_account_status(email: str, new_status: str) -> None:
    path = get_accounts_file()
    with _accounts_file_lock:
        accounts = _read_accounts_json(path)
        for acc in accounts:
            if acc.get("email") == email:
                acc["status"] = new_status
        _write_accounts_json(path, accounts)


def delete_account(email: str) -> None:
    path = get_accounts_file()
    with _accounts_file_lock:
        accounts = _read_accounts_json(path)
        accounts = [a for a in accounts if a.get("email") != email]
        _write_accounts_json(path, accounts)
    token_path = get_token_dir() / f"{email}.json"
    if token_path.exists():
        token_path.unlink()


# ── Token 文件列表 ────────────────────────────────────────────

def parse_tokens() -> list:
    token_dir = get_token_dir()
    tokens: list = []
    if not token_dir.exists():
        return tokens
    for jf in sorted(token_dir.glob("*.json")):
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
            updated_str = datetime.fromtimestamp(
                jf.stat().st_mtime, tz=timezone(timedelta(hours=8))
            ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
            tokens.append({
                "email": data.get("email", jf.stem),
                "access_token": data.get("access_token", ""),
                "refresh_token": data.get("refresh_token", ""),
                "id_token": data.get("id_token", ""),
                "account_id": data.get("account_id", ""),
                "expired": data.get("expired", ""),
                "last_refresh": data.get("last_refresh", ""),
                "updated": updated_str,
            })
        except Exception:
            pass
    return tokens
