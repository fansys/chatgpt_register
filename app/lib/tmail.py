"""临时邮箱客户端：tmail API 封装"""

import re
import string
import random
import hashlib
import time

from curl_cffi import requests as curl_requests

from app.lib.utils import print_lock


class TmailClient:
    """tmail 临时邮箱操作：创建邮箱、登录、收取验证码"""

    def __init__(
        self,
        api_base: str,
        admin_auth: str,
        domain: str,
        custom_auth: str = "",
        proxy: str = "",
        ua: str = "",
        impersonate: str = "chrome131",
        tag: str = "",
    ):
        self.api_base = api_base.rstrip("/")
        self.admin_auth = admin_auth
        self.domain = domain
        self.custom_auth = custom_auth
        self.proxy = proxy
        self.ua = ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self.impersonate = impersonate
        self.tag = tag

    def _print(self, msg: str) -> None:
        prefix = f"[{self.tag}] " if self.tag else ""
        with print_lock:
            print(f"{prefix}{msg}")

    def _session(self) -> curl_requests.Session:
        session = curl_requests.Session()
        session.headers.update({
            "User-Agent": self.ua,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}
        return session

    def _admin_headers(self) -> dict:
        headers = {"x-admin-auth": self.admin_auth, "Content-Type": "application/json"}
        if self.custom_auth:
            headers["x-custom-auth"] = self.custom_auth
        return headers

    def _user_headers(self, mail_token: str) -> dict:
        headers = {"Authorization": f"Bearer {mail_token}"}
        if self.custom_auth:
            headers["x-custom-auth"] = self.custom_auth
        return headers

    def create_temp_email(self) -> tuple:
        """创建临时邮箱，返回 (email, email_password, mail_token)"""
        if not self.admin_auth:
            raise Exception("tmail_admin_auth 未设置，无法创建临时邮箱")

        chars = string.ascii_lowercase + string.digits
        email_local = "f3c" + "".join(random.choice(chars) for _ in range(random.randint(5, 10)))
        email = f"{email_local}@{self.domain}"

        try:
            res = self._session().post(
                f"{self.api_base}/admin/new_address",
                json={"enablePrefix": False, "name": email_local, "domain": self.domain},
                headers=self._admin_headers(),
                timeout=15,
                impersonate=self.impersonate,
            )
            if res.status_code not in [200, 201]:
                raise Exception(f"创建邮箱失败: {res.status_code} - {res.text[:200]}")
            data = res.json()
            mail_token = data.get("jwt")
            if not mail_token:
                raise Exception(f"创建邮箱未返回 jwt: {data}")
            return email, data.get("password", ""), mail_token
        except Exception as e:
            raise Exception(f"tmail 创建邮箱失败: {e}")

    def login_for_token(self, email: str, email_password: str) -> str:
        """使用邮箱密码登录 tmail，返回 jwt"""
        hashed = hashlib.sha256(email_password.encode("utf-8")).hexdigest()
        headers = {"Content-Type": "application/json"}
        if self.custom_auth:
            headers["x-custom-auth"] = self.custom_auth
        try:
            res = self._session().post(
                f"{self.api_base}/api/address_login",
                json={"email": email, "password": hashed, "cf_token": ""},
                headers=headers,
                timeout=15,
                impersonate=self.impersonate,
            )
            if res.status_code not in [200, 201]:
                raise Exception(f"tmail 登录失败: {res.status_code} - {res.text[:200]}")
            data = res.json()
            jwt_token = data.get("jwt")
            if not jwt_token:
                raise Exception(f"tmail 登录未返回 jwt: {data}")
            return jwt_token
        except Exception as e:
            raise Exception(f"tmail 登录失败: {e}")

    def fetch_emails(self, mail_token: str, email: str = None) -> list:
        """获取邮件列表"""
        try:
            params = {"limit": "20", "offset": "0"}
            if email:
                params["address"] = email
            res = self._session().get(
                f"{self.api_base}/api/mails",
                headers=self._user_headers(mail_token),
                params=params,
                timeout=15,
                impersonate=self.impersonate,
            )
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list):
                    return data
                return (
                    data.get("mails") or data.get("data") or
                    data.get("results") or data.get("items") or []
                )
            return []
        except Exception:
            return []

    def fetch_email_detail(self, mail_token: str, msg_id) -> dict:
        """获取单封邮件详情"""
        try:
            res = self._session().get(
                f"{self.api_base}/user_api/mails/{msg_id}",
                headers=self._user_headers(mail_token),
                timeout=15,
                impersonate=self.impersonate,
            )
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return None

    @staticmethod
    def extract_verification_code(content: str):
        """从邮件内容提取 6 位验证码"""
        if not content:
            return None
        patterns = [
            r"Verification code:?\s*(\d{6})",
            r"code is\s*(\d{6})",
            r"代码为[:：]?\s*(\d{6})",
            r"验证码[:：]?\s*(\d{6})",
            r">\s*(\d{6})\s*<",
            r"(?<![#&])\b(\d{6})\b",
        ]
        for pattern in patterns:
            for code in re.findall(pattern, content, re.IGNORECASE):
                if code != "177010":
                    return code
        return None

    def wait_for_verification_email(
        self, mail_token: str, timeout: int = 120, email: str = None
    ):
        """轮询邮件直到获取验证码，返回验证码或 None"""
        self._print(f"[OTP] 等待验证码邮件 (最多 {timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            messages = self.fetch_emails(mail_token, email)
            if messages:
                first = messages[0]
                content = first.get("text") or first.get("html") or first.get("raw") or ""
                if not content:
                    msg_id = first.get("id") or first.get("message_id")
                    if msg_id:
                        detail = self.fetch_email_detail(mail_token, msg_id)
                        if detail:
                            content = detail.get("text") or detail.get("html") or detail.get("raw") or ""
                code = self.extract_verification_code(content)
                if code:
                    self._print(f"[OTP] 验证码: {code}")
                    return code
            elapsed = int(time.time() - start)
            self._print(f"[OTP] 等待中... ({elapsed}s/{timeout}s)")
            time.sleep(3)
        self._print(f"[OTP] 超时 ({timeout}s)")
        return None
