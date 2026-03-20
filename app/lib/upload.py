"""CPA 平台上传类"""

import json
import os
import threading
import urllib.parse
from pathlib import Path

from app.config import read_config

_print_lock = threading.Lock()
DEFAULT_CPA_PROBE_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"


class CpaUploader:
    """上传 Token JSON 文件到 CPA 管理平台"""

    def __init__(self, upload_url: str, upload_token: str, proxy: str = ""):
        self.upload_url = upload_url
        self.upload_token = upload_token
        self.proxy = proxy

    @staticmethod
    def _safe_json_text(text: str) -> dict:
        try:
            return json.loads(text)
        except Exception:
            return {}

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.upload_token}",
            "Accept": "application/json",
        }

    def _build_probe_payload(self, auth_index: str, user_agent: str, chatgpt_account_id: str = "") -> dict:
        call_header = {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": user_agent or DEFAULT_CPA_PROBE_UA,
        }
        if chatgpt_account_id:
            call_header["Chatgpt-Account-Id"] = chatgpt_account_id

        return {
            "authIndex": auth_index,
            "method": "GET",
            "url": "https://chatgpt.com/backend-api/wham/usage",
            "header": call_header,
        }

    @classmethod
    def from_config(cls) -> "CpaUploader":
        cfg = read_config()
        return cls(
            upload_url=cfg.get("upload_api_url", ""),
            upload_token=cfg.get("upload_api_token", ""),
            proxy=cfg.get("proxy", ""),
        )

    def fetch_auth_files(self, timeout: int = 30) -> list[dict]:
        if not self.upload_url:
            raise ValueError("upload_api_url not configured")
        if not self.upload_token:
            raise ValueError("upload_api_token not configured")

        from curl_cffi import requests as curl_requests

        session = curl_requests.Session()
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}

        resp = session.get(
            self.upload_url,
            headers=self._headers(),
            verify=False,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"fetch auth-files failed: {resp.status_code} {resp.text[:200]}")

        data = self._safe_json_text(resp.text)
        files = data.get("files")
        return files if isinstance(files, list) else []

    def probe_auth_index(
        self,
        auth_index: str,
        chatgpt_account_id: str = "",
        user_agent: str = DEFAULT_CPA_PROBE_UA,
        timeout: int = 30,
    ) -> dict:
        if not auth_index:
            return {"status_code": None, "invalid_401": False, "error": "missing auth_index"}
        if not self.upload_url:
            return {"status_code": None, "invalid_401": False, "error": "upload_api_url not configured"}
        if not self.upload_token:
            return {"status_code": None, "invalid_401": False, "error": "upload_api_token not configured"}

        from curl_cffi import requests as curl_requests

        session = curl_requests.Session()
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}

        payload = self._build_probe_payload(auth_index, user_agent, chatgpt_account_id)
        api_call_url = self.upload_url.replace("/auth-files", "/api-call", 1)
        headers = {**self._headers(), "Content-Type": "application/json"}

        try:
            resp = session.post(
                api_call_url,
                json=payload,
                headers=headers,
                verify=False,
                timeout=timeout,
            )
            if resp.status_code >= 400:
                return {
                    "status_code": None,
                    "invalid_401": False,
                    "error": f"management api-call http {resp.status_code}: {resp.text[:200]}",
                }
            data = self._safe_json_text(resp.text)
            sc = data.get("status_code")
            return {
                "status_code": sc,
                "invalid_401": sc == 401,
                "error": None if sc is not None else "missing status_code in api-call response",
            }
        except Exception as e:
            return {"status_code": None, "invalid_401": False, "error": str(e)}

    def delete_auth_file_by_name(self, name: str, timeout: int = 30) -> dict:
        if not name:
            return {"deleted": False, "error": "missing name"}
        if not self.upload_url:
            return {"deleted": False, "error": "upload_api_url not configured"}
        if not self.upload_token:
            return {"deleted": False, "error": "upload_api_token not configured"}

        from curl_cffi import requests as curl_requests

        session = curl_requests.Session()
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}

        encoded = urllib.parse.quote(name, safe="")
        url = f"{self.upload_url}?name={encoded}"
        try:
            resp = session.delete(
                url,
                headers=self._headers(),
                verify=False,
                timeout=timeout,
            )
            data = self._safe_json_text(resp.text)
            ok = resp.status_code == 200 and data.get("status") == "ok"
            return {
                "deleted": ok,
                "status_code": resp.status_code,
                "error": None if ok else f"delete failed: {resp.status_code} {(resp.text or '')[:200]}",
            }
        except Exception as e:
            return {"deleted": False, "error": str(e)}

    def delete_auth_file_by_email(self, email: str, timeout: int = 30) -> dict:
        email_norm = str(email or "").strip().lower()
        if not email_norm:
            return {"deleted": False, "error": "missing email"}

        try:
            files = self.fetch_auth_files(timeout=timeout)
        except Exception as e:
            return {"deleted": False, "error": f"fetch auth-files failed: {e}"}

        for item in files:
            item_email = self.get_file_email(item).lower()
            if item_email != email_norm:
                continue
            name = str(item.get("name") or item.get("id") or item.get("email") or item.get("account") or "").strip()
            if not name:
                return {"deleted": False, "error": "matched item missing name"}
            result = self.delete_auth_file_by_name(name, timeout=timeout)
            return {**result, "name": name}

        return {"deleted": False, "error": "email not found in cpa auth-files"}

    @staticmethod
    def get_file_email(item: dict) -> str:
        if not isinstance(item, dict):
            return ""
        for key in ("email", "account", "name", "id"):
            val = str(item.get(key) or "").strip()
            if "@" in val:
                return val
        return ""

    @staticmethod
    def get_file_account_id(item: dict) -> str:
        if not isinstance(item, dict):
            return ""
        for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
            val = str(item.get(key) or "").strip()
            if val:
                return val
        return ""

    @staticmethod
    def read_account_id_from_token_file(filepath: str) -> str:
        try:
            with open(Path(filepath), "r", encoding="utf-8") as f:
                data = json.load(f)
            return str((data or {}).get("account_id") or "").strip()
        except Exception:
            return ""

    def upload_token_json(self, filepath: str) -> None:
        """上传 Token JSON 文件；未配置 upload_url 时静默跳过"""
        if not self.upload_url:
            return

        mp = None
        filename = None
        try:
            from curl_cffi import CurlMime, requests as curl_requests

            filename = os.path.basename(filepath)
            mp = CurlMime()
            mp.addpart(
                name="file",
                content_type="application/json",
                filename=filename,
                local_path=filepath,
            )

            session = curl_requests.Session()
            if self.proxy:
                session.proxies = {"http": self.proxy, "https": self.proxy}

            resp = session.post(
                self.upload_url,
                multipart=mp,
                headers=self._headers(),
                verify=False,
                timeout=30,
            )

            if resp.status_code == 200:
                with _print_lock:
                    print("  [CPA] Token JSON 已上传到 CPA 管理平台")
            else:
                with _print_lock:
                    print(f"  [CPA] 上传失败: {resp.status_code} - {resp.text[:200]}")
        except Exception as e:
            with _print_lock:
                print(f"  [CPA] 上传异常: {filename} - {e}")
        finally:
            if mp:
                mp.close()
