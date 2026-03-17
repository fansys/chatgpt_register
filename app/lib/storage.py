"""数据读取和存储类：账号文件、Token JSON"""

import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.config import read_config, BASE_DIR
from app.lib.utils import Utils
from app.lib.upload import CpaUploader


class DataStorage:
    """账号与 Token 的持久化读写"""

    _file_lock = threading.Lock()

    def __init__(self):
        cfg = read_config()
        td = cfg.get("token_json_dir", "codex_tokens")

        self.token_dir = Path(td) if Path(td).is_absolute() else BASE_DIR / td
        self.upload_api_url = cfg.get("upload_api_url", "")
        self.upload_api_token = cfg.get("upload_api_token", "")
        self.proxy = cfg.get("proxy", "")

    # ── 账号文件 ──────────────────────────────────────────────

    def load_registered_accounts(self, registered_file: str) -> dict:
        """读取账号 JSON 文件，返回 email -> (chatgpt_password, email_pwd, raw_dict)"""
        accounts: dict = {}
        path = Path(registered_file)
        if not path.exists():
            return accounts
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return accounts
            for acc in data:
                email = acc.get("email", "")
                if email and email not in accounts:
                    accounts[email] = (
                        acc.get("chatgpt_pwd", ""),
                        acc.get("email_pwd", ""),
                        acc,
                    )
        except Exception:
            pass
        return accounts

    def write_account_line(
        self,
        output_file: str,
        email: str,
        chatgpt_pwd: str,
        email_pwd: str,
        oauth_ok: bool,
    ) -> None:
        """追加一条账号记录到注册结果 JSON 文件"""
        entry = {
            "email": email,
            "chatgpt_pwd": chatgpt_pwd,
            "email_pwd": email_pwd,
            "status": f"oauth={'ok' if oauth_ok else 'fail'}",
        }
        path = Path(output_file)
        with self._file_lock:
            accounts: list = []
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if isinstance(existing, list):
                        accounts = existing
                except Exception:
                    pass
            accounts.append(entry)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(accounts, f, ensure_ascii=False, indent=2)

    # ── Token 文件 ────────────────────────────────────────────

    def save_codex_tokens(self, email: str, tokens: dict) -> None:
        """将 OAuth tokens 写入 codex_tokens/<email>.json，按配置上传"""
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        id_token = tokens.get("id_token", "")

        if not access_token:
            return

        payload = Utils.decode_jwt_payload(access_token)
        auth_info = payload.get("https://api.openai.com/auth", {})
        account_id = auth_info.get("chatgpt_account_id", "")

        exp_timestamp = payload.get("exp")
        expired_str = ""
        if isinstance(exp_timestamp, int) and exp_timestamp > 0:
            exp_dt = datetime.fromtimestamp(exp_timestamp, tz=timezone(timedelta(hours=8)))
            expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        now = datetime.now(tz=timezone(timedelta(hours=8)))
        token_data = {
            "type": "codex",
            "email": email,
            "expired": expired_str,
            "id_token": id_token,
            "account_id": account_id,
            "access_token": access_token,
            "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "refresh_token": refresh_token,
        }

        self.token_dir.mkdir(parents=True, exist_ok=True)
        token_path = self.token_dir / f"{email}.json"
        with self._file_lock:
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, ensure_ascii=False)

        if self.upload_api_url:
            CpaUploader(self.upload_api_url, self.upload_api_token, self.proxy).upload_token_json(
                str(token_path)
            )
