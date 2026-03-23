"""ChatGPT 接口类：注册流程 + PKCE OAuth 登录（含 Sentinel Token PoW）"""

import re
import json
import random
import time
import uuid
import secrets as _secrets
from urllib.parse import urlencode

from curl_cffi import requests as curl_requests

from app.config import read_config
from app.lib.utils import Utils, print_lock
from app.lib.tmail import TmailClient


# ═══════════════════════════════════════════════════════════════
#  Sentinel Token 生成器（OpenAI PoW 机制）
# ═══════════════════════════════════════════════════════════════

class SentinelTokenGenerator:
    """纯 Python Sentinel Token 生成器（PoW）"""

    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id=None, user_agent=None):
        self.device_id = device_id or str(uuid.uuid4())
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )
        self.requirements_seed = str(random.random())
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= (h >> 16)
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= (h >> 13)
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= (h >> 16)
        h &= 0xFFFFFFFF
        return format(h, "08x")

    def _get_config(self) -> list:
        now_str = time.strftime(
            "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()
        )
        perf_now = random.uniform(1000, 50000)
        time_origin = time.time() * 1000 - perf_now
        nav_prop = random.choice([
            "vendorSub", "productSub", "vendor", "maxTouchPoints", "scheduling",
            "userActivation", "doNotTrack", "geolocation", "connection", "plugins",
            "mimeTypes", "pdfViewerEnabled", "webkitTemporaryStorage",
            "webkitPersistentStorage", "hardwareConcurrency", "cookieEnabled",
            "credentials", "mediaDevices", "permissions", "locks", "ink",
        ])
        return [
            "1920x1080", now_str, 4294705152, random.random(), self.user_agent,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None, None, "en-US", "en-US,en", random.random(),
            f"{nav_prop}-undefined",
            random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
            random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
            perf_now, self.sid, "", random.choice([4, 8, 12, 16]), time_origin,
        ]

    @staticmethod
    def _b64(data) -> str:
        raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        import base64
        return base64.b64encode(raw).decode("ascii")

    def _run_check(self, start_time, seed, difficulty, config, nonce):
        config[3] = nonce
        config[9] = round((time.time() - start_time) * 1000)
        data = self._b64(config)
        if self._fnv1a_32(seed + data)[: len(difficulty)] <= difficulty:
            return data + "~S"
        return None

    def generate_token(self, seed=None, difficulty=None) -> str:
        seed = seed if seed is not None else self.requirements_seed
        difficulty = str(difficulty or "0")
        start_time = time.time()
        config = self._get_config()
        for i in range(self.MAX_ATTEMPTS):
            result = self._run_check(start_time, seed, difficulty, config, i)
            if result:
                return "gAAAAAB" + result
        return "gAAAAAB" + self.ERROR_PREFIX + self._b64(str(None))

    def generate_requirements_token(self) -> str:
        config = self._get_config()
        config[3] = 1
        config[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(config)


def _fetch_sentinel_challenge(session, device_id, flow, user_agent, sec_ch_ua, impersonate):
    gen = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        "Origin": "https://sentinel.openai.com",
        "User-Agent": user_agent or "Mozilla/5.0",
        "sec-ch-ua": sec_ch_ua or '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
    }
    kwargs = {
        "data": json.dumps({"p": gen.generate_requirements_token(), "id": device_id, "flow": flow}),
        "headers": headers, "timeout": 20,
    }
    if impersonate:
        kwargs["impersonate"] = impersonate
    try:
        resp = session.post("https://sentinel.openai.com/backend-api/sentinel/req", **kwargs)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


def _build_sentinel_token(session, device_id, flow, user_agent, sec_ch_ua, impersonate):
    challenge = _fetch_sentinel_challenge(session, device_id, flow, user_agent, sec_ch_ua, impersonate)
    if not challenge:
        return None
    c_value = challenge.get("token", "")
    if not c_value:
        return None
    pow_data = challenge.get("proofofwork") or {}
    gen = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)
    if pow_data.get("required") and pow_data.get("seed"):
        p_value = gen.generate_token(seed=pow_data["seed"], difficulty=pow_data.get("difficulty", "0"))
    else:
        p_value = gen.generate_requirements_token()
    return json.dumps(
        {"p": p_value, "t": "", "c": c_value, "id": device_id, "flow": flow},
        separators=(",", ":"),
    )


# ═══════════════════════════════════════════════════════════════
#  ChatGPT 注册 & OAuth 登录（完整，不可再拆分）
# ═══════════════════════════════════════════════════════════════

class ChatGPTRegister:
    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"

    def __init__(self, proxy: str = None, tag: str = ""):
        cfg = read_config()
        self.tag = tag
        self.device_id = str(uuid.uuid4())
        self.auth_session_logging_id = str(uuid.uuid4())
        (
            self.impersonate, self.chrome_major, self.chrome_full,
            self.ua, self.sec_ch_ua,
        ) = Utils.random_chrome_version()

        self.proxy = proxy or ""

        # OTP 等待时间
        self.otp_wait = int(cfg.get("otp_wait_seconds", 10))

        # OAuth 配置
        self.oauth_issuer = cfg.get("oauth_issuer", "https://auth.openai.com").rstrip("/")
        self.oauth_client_id = cfg.get("oauth_client_id", "")
        self.oauth_redirect_uri = cfg.get("oauth_redirect_uri", "")

        # tmail 客户端
        self.tmail = TmailClient(
            api_base=cfg.get("tmail_api_base", ""),
            admin_auth=cfg.get("tmail_admin_auth", ""),
            domain=cfg.get("tmail_domain", "awsl.uk"),
            custom_auth=cfg.get("tmail_custom_auth", ""),
            proxy=self.proxy,
            tag=tag,
        )

        # HTTP 会话
        self.session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": random.choice([
                "en-US,en;q=0.9", "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9", "en-US,en;q=0.8",
            ]),
            "sec-ch-ua": self.sec_ch_ua, "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"', "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": f'"{self.chrome_full}"',
            "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
        })
        self.session.cookies.set("oai-did", self.device_id, domain="chatgpt.com")
        self._callback_url = None
        self.oauth_last_error = ""

    # ── 日志 ─────────────────────────────────────────────────────

    def _log(self, step, method, url, status, body=None):
        prefix = f"[{self.tag}] " if self.tag else ""
        lines = [f"\n{'=' * 60}", f"{prefix}[Step] {step}",
                 f"{prefix}[{method}] {url}", f"{prefix}[Status] {status}"]
        if body:
            try:
                lines.append(f"{prefix}[Response] {json.dumps(body, indent=2, ensure_ascii=False)[:1000]}")
            except Exception:
                lines.append(f"{prefix}[Response] {str(body)[:1000]}")
        lines.append(f"{'=' * 60}")
        with print_lock:
            print("\n".join(lines))

    def _print(self, msg: str):
        prefix = f"[{self.tag}] " if self.tag else ""
        with print_lock:
            print(f"{prefix}{msg}")

    def _set_oauth_error(self, msg: str) -> None:
        self.oauth_last_error = str(msg or "").strip()

    # ── tmail 代理方法（保持外部 API 兼容）───────────────────────

    def create_temp_email(self) -> tuple:
        return self.tmail.create_temp_email()

    def tmail_login_for_token(self, email: str, email_password: str) -> str:
        return self.tmail.login_for_token(email, email_password)

    # ── 注册流程步骤 ──────────────────────────────────────────────

    def visit_homepage(self):
        url = f"{self.BASE}/"
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        self._log("0. Visit homepage", "GET", url, r.status_code,
                  {"cookies_count": len(self.session.cookies)})

    def get_csrf(self) -> str:
        url = f"{self.BASE}/api/auth/csrf"
        r = self.session.get(url, headers={"Accept": "application/json", "Referer": f"{self.BASE}/"})
        data = r.json()
        token = data.get("csrfToken", "")
        self._log("1. Get CSRF", "GET", url, r.status_code, data)
        if not token:
            raise Exception("Failed to get CSRF token")
        return token

    def signin(self, email: str, csrf: str) -> str:
        url = f"{self.BASE}/api/auth/signin/openai"
        params = {
            "prompt": "login", "ext-oai-did": self.device_id,
            "auth_session_logging_id": self.auth_session_logging_id,
            "screen_hint": "login_or_signup", "login_hint": email,
        }
        r = self.session.post(url, params=params,
                              data={"callbackUrl": f"{self.BASE}/", "csrfToken": csrf, "json": "true"},
                              headers={
                                  "Content-Type": "application/x-www-form-urlencoded",
                                  "Accept": "application/json",
                                  "Referer": f"{self.BASE}/", "Origin": self.BASE,
                              })
        data = r.json()
        authorize_url = data.get("url", "")
        self._log("2. Signin", "POST", url, r.status_code, data)
        if not authorize_url:
            raise Exception("Failed to get authorize URL")
        return authorize_url

    def authorize(self, url: str) -> str:
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{self.BASE}/", "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        final_url = str(r.url)
        self._log("3. Authorize", "GET", url, r.status_code, {"final_url": final_url})
        return final_url

    def register(self, email: str, password: str):
        url = f"{self.AUTH}/api/accounts/user/register"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Referer": f"{self.AUTH}/create-account/password", "Origin": self.AUTH}
        headers.update(Utils.make_trace_headers())
        r = self.session.post(url, json={"username": email, "password": password}, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("4. Register", "POST", url, r.status_code, data)
        return r.status_code, data

    def send_otp(self):
        url = f"{self.AUTH}/api/accounts/email-otp/send"
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{self.AUTH}/create-account/password", "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        try:
            data = r.json()
        except Exception:
            data = {"final_url": str(r.url), "status": r.status_code}
        self._log("5. Send OTP", "GET", url, r.status_code, data)
        return r.status_code, data

    def validate_otp(self, code: str):
        url = f"{self.AUTH}/api/accounts/email-otp/validate"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Referer": f"{self.AUTH}/email-verification", "Origin": self.AUTH}
        headers.update(Utils.make_trace_headers())
        r = self.session.post(url, json={"code": code}, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("6. Validate OTP", "POST", url, r.status_code, data)
        return r.status_code, data

    def create_account(self, name: str, birthdate: str):
        url = f"{self.AUTH}/api/accounts/create_account"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Referer": f"{self.AUTH}/about-you", "Origin": self.AUTH}
        headers.update(Utils.make_trace_headers())
        r = self.session.post(url, json={"name": name, "birthdate": birthdate}, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("7. Create Account", "POST", url, r.status_code, data)
        if isinstance(data, dict):
            cb = data.get("continue_url") or data.get("url") or data.get("redirect_url")
            if cb:
                self._callback_url = cb
        return r.status_code, data

    def callback(self, url: str = None):
        if not url:
            url = self._callback_url
        if not url:
            self._print("[!] No callback URL, skipping.")
            return None, None
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        self._log("8. Callback", "GET", url, r.status_code, {"final_url": str(r.url)})
        return r.status_code, {"final_url": str(r.url)}

    # ── 注册主流程 ────────────────────────────────────────────────

    def run_register(self, email, password, name, birthdate, mail_token) -> bool:
        from urllib.parse import urlparse
        self.visit_homepage()
        Utils.random_delay(0.3, 0.8)
        csrf = self.get_csrf()
        Utils.random_delay(0.2, 0.5)
        auth_url = self.signin(email, csrf)
        Utils.random_delay(0.3, 0.8)
        final_url = self.authorize(auth_url)
        final_path = urlparse(final_url).path
        Utils.random_delay(0.3, 0.8)
        self._print(f"Authorize → {final_path}")

        need_otp = False
        if "create-account/password" in final_path:
            self._print("全新注册流程")
            Utils.random_delay(0.5, 1.0)
            status, data = self.register(email, password)
            if status != 200:
                raise Exception(f"Register 失败 ({status}): {data}")
            Utils.random_delay(0.3, 0.8)
            self.send_otp()
            need_otp = True
        elif "email-verification" in final_path or "email-otp" in final_path:
            self._print("跳到 OTP 验证阶段")
            need_otp = True
        elif "about-you" in final_path:
            self._print("跳到填写信息阶段")
            Utils.random_delay(0.5, 1.0)
            self.create_account(name, birthdate)
            Utils.random_delay(0.3, 0.5)
            self.callback()
            return True
        elif "callback" in final_path or "chatgpt.com" in final_url:
            self._print("账号已完成注册")
            return True
        else:
            self._print(f"未知跳转: {final_url}")
            self.register(email, password)
            self.send_otp()
            need_otp = True

        if need_otp:
            if self.otp_wait > 0:
                self._print(f"[OTP] 等待 {self.otp_wait}s 再取验证码...")
                time.sleep(self.otp_wait)
            otp_code = self.tmail.wait_for_verification_email(mail_token, email=email)
            if not otp_code:
                raise Exception("未能获取验证码")
            Utils.random_delay(0.3, 0.8)
            status, data = self.validate_otp(otp_code)
            if status != 200:
                self._print("验证码失败，重试...")
                self.send_otp()
                Utils.random_delay(1.0, 2.0)
                otp_code = self.tmail.wait_for_verification_email(mail_token, timeout=60, email=email)
                if not otp_code:
                    raise Exception("重试后仍未获取验证码")
                Utils.random_delay(0.3, 0.8)
                status, data = self.validate_otp(otp_code)
                if status != 200:
                    raise Exception(f"验证码失败 ({status}): {data}")

        Utils.random_delay(0.5, 1.5)
        status, data = self.create_account(name, birthdate)
        if status != 200:
            raise Exception(f"Create account 失败 ({status}): {data}")
        Utils.random_delay(0.2, 0.5)
        self.callback()
        return True

    # ── OAuth PKCE 登录 ────────────────────────────────────────────

    def _decode_oauth_session_cookie(self):
        import base64
        jar = getattr(self.session.cookies, "jar", None)
        cookie_items = list(jar) if jar is not None else []
        for c in cookie_items:
            name = getattr(c, "name", "") or ""
            if "oai-client-auth-session" not in name:
                continue
            raw_val = (getattr(c, "value", "") or "").strip()
            if not raw_val:
                continue
            candidates = [raw_val]
            try:
                from urllib.parse import unquote
                decoded = unquote(raw_val)
                if decoded != raw_val:
                    candidates.append(decoded)
            except Exception:
                pass
            for val in candidates:
                try:
                    if val[:1] in ('"', "'") and val[-1:] == val[:1]:
                        val = val[1:-1]
                    part = val.split(".")[0] if "." in val else val
                    pad = 4 - len(part) % 4
                    if pad != 4:
                        part += "=" * pad
                    data = json.loads(base64.urlsafe_b64decode(part).decode("utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue
        return None

    def _oauth_allow_redirect_extract_code(self, url: str, referer: str = None):
        headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                   "Upgrade-Insecure-Requests": "1", "User-Agent": self.ua}
        if referer:
            headers["Referer"] = referer
        try:
            resp = self.session.get(url, headers=headers, allow_redirects=True,
                                    timeout=30, impersonate=self.impersonate)
            code = Utils.extract_code_from_url(str(resp.url))
            if code:
                self._print("[OAuth] allow_redirect 命中最终 URL code")
                return code
            for r in getattr(resp, "history", []) or []:
                loc = r.headers.get("Location", "")
                code = Utils.extract_code_from_url(loc) or Utils.extract_code_from_url(str(r.url))
                if code:
                    return code
        except Exception as e:
            m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
            if m:
                code = Utils.extract_code_from_url(m.group(1))
                if code:
                    return code
            self._print(f"[OAuth] allow_redirect 异常: {e}")
        return None

    def _oauth_follow_for_code(self, start_url: str, referer: str = None, max_hops: int = 16):
        headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                   "Upgrade-Insecure-Requests": "1", "User-Agent": self.ua}
        if referer:
            headers["Referer"] = referer
        current_url = last_url = start_url
        for hop in range(max_hops):
            try:
                resp = self.session.get(current_url, headers=headers, allow_redirects=False,
                                        timeout=30, impersonate=self.impersonate)
            except Exception as e:
                m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
                if m:
                    code = Utils.extract_code_from_url(m.group(1))
                    if code:
                        return code, m.group(1)
                self._print(f"[OAuth] follow[{hop+1}] 请求异常: {e}")
                return None, last_url
            last_url = str(resp.url)
            self._print(f"[OAuth] follow[{hop+1}] {resp.status_code} {last_url[:140]}")
            code = Utils.extract_code_from_url(last_url)
            if code:
                return code, last_url
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location", "")
                if not loc:
                    return None, last_url
                if loc.startswith("/"):
                    loc = f"{self.oauth_issuer}{loc}"
                code = Utils.extract_code_from_url(loc)
                if code:
                    return code, loc
                current_url = loc
                headers["Referer"] = last_url
                continue
            return None, last_url
        return None, last_url

    def _oauth_submit_workspace_and_org(self, consent_url: str):
        session_data = self._decode_oauth_session_cookie()
        if not session_data:
            self._print("[OAuth] 无法解码 oai-client-auth-session")
            return None
        workspaces = session_data.get("workspaces", [])
        if not workspaces:
            self._print("[OAuth] session 中没有 workspace 信息")
            return None
        workspace_id = (workspaces[0] or {}).get("id")
        if not workspace_id:
            self._print("[OAuth] workspace_id 为空")
            return None

        h = {"Accept": "application/json", "Content-Type": "application/json",
             "Origin": self.oauth_issuer, "Referer": consent_url,
             "User-Agent": self.ua, "oai-device-id": self.device_id}
        h.update(Utils.make_trace_headers())

        resp = self.session.post(f"{self.oauth_issuer}/api/accounts/workspace/select",
                                 json={"workspace_id": workspace_id}, headers=h,
                                 allow_redirects=False, timeout=30, impersonate=self.impersonate)
        self._print(f"[OAuth] workspace/select -> {resp.status_code}")

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if loc.startswith("/"):
                loc = f"{self.oauth_issuer}{loc}"
            code = Utils.extract_code_from_url(loc)
            if code:
                return code
            code, _ = self._oauth_follow_for_code(loc, referer=consent_url)
            return code or self._oauth_allow_redirect_extract_code(loc, referer=consent_url)

        if resp.status_code != 200:
            self._print(f"[OAuth] workspace/select 失败: {resp.status_code}")
            return None
        try:
            ws_data = resp.json()
        except Exception:
            return None

        ws_next = ws_data.get("continue_url", "")
        orgs = ws_data.get("data", {}).get("orgs", [])
        ws_page = (ws_data.get("page") or {}).get("type", "")
        self._print(f"[OAuth] workspace/select page={ws_page or '-'} next={(ws_next or '-')[:140]}")

        org_id = project_id = None
        if orgs:
            org_id = (orgs[0] or {}).get("id")
            projects = (orgs[0] or {}).get("projects", [])
            if projects:
                project_id = (projects[0] or {}).get("id")

        if org_id:
            org_body = {"org_id": org_id}
            if project_id:
                org_body["project_id"] = project_id
            h_org = dict(h)
            if ws_next:
                h_org["Referer"] = ws_next if ws_next.startswith("http") else f"{self.oauth_issuer}{ws_next}"
            resp_org = self.session.post(f"{self.oauth_issuer}/api/accounts/organization/select",
                                         json=org_body, headers=h_org, allow_redirects=False,
                                         timeout=30, impersonate=self.impersonate)
            self._print(f"[OAuth] organization/select -> {resp_org.status_code}")
            if resp_org.status_code in (301, 302, 303, 307, 308):
                loc = resp_org.headers.get("Location", "")
                if loc.startswith("/"):
                    loc = f"{self.oauth_issuer}{loc}"
                code = Utils.extract_code_from_url(loc)
                if code:
                    return code
                code, _ = self._oauth_follow_for_code(loc, referer=h_org.get("Referer"))
                return code or self._oauth_allow_redirect_extract_code(loc, referer=h_org.get("Referer"))
            if resp_org.status_code == 200:
                try:
                    org_data = resp_org.json()
                except Exception:
                    return None
                org_next = org_data.get("continue_url", "")
                org_page = (org_data.get("page") or {}).get("type", "")
                self._print(f"[OAuth] organization/select page={org_page or '-'} next={(org_next or '-')[:140]}")
                if org_next:
                    if org_next.startswith("/"):
                        org_next = f"{self.oauth_issuer}{org_next}"
                    code, _ = self._oauth_follow_for_code(org_next, referer=h_org.get("Referer"))
                    return code or self._oauth_allow_redirect_extract_code(org_next, referer=h_org.get("Referer"))

        if ws_next:
            if ws_next.startswith("/"):
                ws_next = f"{self.oauth_issuer}{ws_next}"
            code, _ = self._oauth_follow_for_code(ws_next, referer=consent_url)
            return code or self._oauth_allow_redirect_extract_code(ws_next, referer=consent_url)
        return None

    def perform_codex_oauth_login_http(self, email: str, password: str, mail_token: str = None):
        """执行 PKCE OAuth 流程，返回包含 access_token 的 dict 或 None"""
        self.oauth_last_error = ""
        self._print("[OAuth] 开始执行 Codex OAuth 纯协议流程...")
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

        code_verifier, code_challenge = Utils.generate_pkce()
        state = _secrets.token_urlsafe(24)
        authorize_params = {
            "response_type": "code", "client_id": self.oauth_client_id,
            "redirect_uri": self.oauth_redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": code_challenge, "code_challenge_method": "S256",
            "state": state,
        }
        authorize_url = f"{self.oauth_issuer}/oauth/authorize?{urlencode(authorize_params)}"

        def _json_headers(referer: str) -> dict:
            h = {"Accept": "application/json", "Content-Type": "application/json",
                 "Origin": self.oauth_issuer, "Referer": referer,
                 "User-Agent": self.ua, "oai-device-id": self.device_id}
            h.update(Utils.make_trace_headers())
            return h

        def _bootstrap():
            self._print("[OAuth] 1/7 GET /oauth/authorize")
            try:
                r = self.session.get(
                    authorize_url,
                    headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                             "Referer": f"{self.BASE}/", "Upgrade-Insecure-Requests": "1",
                             "User-Agent": self.ua},
                    allow_redirects=True, timeout=30, impersonate=self.impersonate,
                )
            except Exception as e:
                self._print(f"[OAuth] /oauth/authorize 异常: {e}")
                return False, ""
            final_url = str(r.url)
            has_login = any(getattr(c, "name", "") == "login_session" for c in self.session.cookies)
            self._print(f"[OAuth] /oauth/authorize -> {r.status_code}, final={final_url[:140]}")
            self._print(f"[OAuth] login_session: {'已获取' if has_login else '未获取'}")
            if not has_login:
                try:
                    r2 = self.session.get(
                        f"{self.oauth_issuer}/api/oauth/oauth2/auth",
                        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                 "Referer": authorize_url, "Upgrade-Insecure-Requests": "1",
                                 "User-Agent": self.ua},
                        params=authorize_params, allow_redirects=True,
                        timeout=30, impersonate=self.impersonate,
                    )
                    final_url = str(r2.url)
                except Exception as e:
                    self._print(f"[OAuth] /api/oauth/oauth2/auth 异常: {e}")
                has_login = any(getattr(c, "name", "") == "login_session" for c in self.session.cookies)
                self._print(f"[OAuth] login_session(重试): {'已获取' if has_login else '未获取'}")
            return has_login, final_url

        def _post_continue(referer_url: str):
            sentinel = _build_sentinel_token(self.session, self.device_id, "authorize_continue",
                                             self.ua, self.sec_ch_ua, self.impersonate)
            if not sentinel:
                self._print("[OAuth] authorize_continue sentinel 获取失败")
                return None
            h = _json_headers(referer_url)
            h["openai-sentinel-token"] = sentinel
            try:
                return self.session.post(
                    f"{self.oauth_issuer}/api/accounts/authorize/continue",
                    json={"username": {"kind": "email", "value": email}},
                    headers=h, timeout=30, allow_redirects=False, impersonate=self.impersonate,
                )
            except Exception as e:
                self._print(f"[OAuth] authorize/continue 异常: {e}")
                return None

        _, authorize_final_url = _bootstrap()
        if not authorize_final_url:
            self._set_oauth_error("oauth bootstrap failed")
            return None

        continue_referer = (authorize_final_url if authorize_final_url.startswith(self.oauth_issuer)
                            else f"{self.oauth_issuer}/log-in")

        self._print("[OAuth] 2/7 POST /api/accounts/authorize/continue")
        resp_continue = _post_continue(continue_referer)
        if resp_continue is None:
            self._set_oauth_error("authorize/continue request failed")
            return None
        self._print(f"[OAuth] /authorize/continue -> {resp_continue.status_code}")
        if resp_continue.status_code == 400 and "invalid_auth_step" in (resp_continue.text or ""):
            self._print("[OAuth] invalid_auth_step，重新 bootstrap 重试")
            _, authorize_final_url = _bootstrap()
            if not authorize_final_url:
                self._set_oauth_error("oauth bootstrap retry failed")
                return None
            continue_referer = (authorize_final_url if authorize_final_url.startswith(self.oauth_issuer)
                                else f"{self.oauth_issuer}/log-in")
            resp_continue = _post_continue(continue_referer)
            if resp_continue is None:
                self._set_oauth_error("authorize/continue retry failed")
                return None
            self._print(f"[OAuth] /authorize/continue(重试) -> {resp_continue.status_code}")

        if resp_continue.status_code != 200:
            self._print(f"[OAuth] 邮箱提交失败: {resp_continue.text[:180]}")
            self._set_oauth_error(f"/authorize/continue -> {resp_continue.status_code}: {(resp_continue.text or '')[:200]}")
            return None
        try:
            continue_data = resp_continue.json()
        except Exception:
            self._print("[OAuth] authorize/continue 响应解析失败")
            self._set_oauth_error("authorize/continue response parse failed")
            return None

        continue_url = continue_data.get("continue_url", "")
        page_type = (continue_data.get("page") or {}).get("type", "")
        self._print(f"[OAuth] continue page={page_type or '-'} next={(continue_url or '-')[:140]}")

        self._print("[OAuth] 3/7 POST /api/accounts/password/verify")
        sentinel_pwd = _build_sentinel_token(self.session, self.device_id, "password_verify",
                                             self.ua, self.sec_ch_ua, self.impersonate)
        if not sentinel_pwd:
            self._print("[OAuth] password_verify sentinel 获取失败")
            self._set_oauth_error("password_verify sentinel failed")
            return None
        h_verify = _json_headers(f"{self.oauth_issuer}/log-in/password")
        h_verify["openai-sentinel-token"] = sentinel_pwd
        try:
            resp_verify = self.session.post(
                f"{self.oauth_issuer}/api/accounts/password/verify",
                json={"password": password}, headers=h_verify,
                timeout=30, allow_redirects=False, impersonate=self.impersonate,
            )
        except Exception as e:
            self._print(f"[OAuth] password/verify 异常: {e}")
            self._set_oauth_error(f"/password/verify exception: {e}")
            return None

        self._print(f"[OAuth] /password/verify -> {resp_verify.status_code}")
        if resp_verify.status_code != 200:
            self._print(f"[OAuth] 密码校验失败: {resp_verify.text[:180]}")
            self._set_oauth_error(f"/password/verify -> {resp_verify.status_code}: {(resp_verify.text or '')[:200]}")
            return None
        try:
            verify_data = resp_verify.json()
        except Exception:
            self._print("[OAuth] password/verify 响应解析失败")
            self._set_oauth_error("password/verify response parse failed")
            return None

        continue_url = verify_data.get("continue_url", "") or continue_url
        page_type = (verify_data.get("page") or {}).get("type", "") or page_type
        self._print(f"[OAuth] verify page={page_type or '-'} next={(continue_url or '-')[:140]}")

        need_oauth_otp = (
            page_type == "email_otp_verification"
            or "email-verification" in (continue_url or "")
            or "email-otp" in (continue_url or "")
        )
        if need_oauth_otp:
            self._print("[OAuth] 4/7 检测到邮箱 OTP 验证")
            if not mail_token:
                self._print("[OAuth] OAuth 阶段需要 OTP，但未提供 mail_token")
                self._set_oauth_error("mail_token missing for email_otp_verification")
                return None
            h_otp = _json_headers(f"{self.oauth_issuer}/email-verification")
            tried_codes: set = set()
            otp_success = False
            if self.otp_wait > 0:
                self._print(f"[OAuth] OTP 等待 {self.otp_wait}s 再取验证码...")
                time.sleep(self.otp_wait)
            otp_deadline = time.time() + 120
            while time.time() < otp_deadline and not otp_success:
                messages = self.tmail.fetch_emails(mail_token) or []
                candidate_codes = []
                for msg in messages[:12]:
                    content = msg.get("text") or msg.get("html") or msg.get("raw") or ""
                    if not content:
                        msg_id = msg.get("id") or msg.get("message_id")
                        if msg_id:
                            detail = self.tmail.fetch_email_detail(mail_token, msg_id)
                            if detail:
                                content = detail.get("text") or detail.get("html") or detail.get("raw") or ""
                    code = TmailClient.extract_verification_code(content)
                    if code and code not in tried_codes:
                        candidate_codes.append(code)
                if not candidate_codes:
                    elapsed = int(120 - max(0, otp_deadline - time.time()))
                    self._print(f"[OAuth] OTP 等待中... ({elapsed}s/120s)")
                    time.sleep(2)
                    continue
                for otp_code in candidate_codes:
                    tried_codes.add(otp_code)
                    self._print(f"[OAuth] 尝试 OTP: {otp_code}")
                    try:
                        resp_otp = self.session.post(
                            f"{self.oauth_issuer}/api/accounts/email-otp/validate",
                            json={"code": otp_code}, headers=h_otp,
                            timeout=30, allow_redirects=False, impersonate=self.impersonate,
                        )
                    except Exception as e:
                        self._print(f"[OAuth] email-otp/validate 异常: {e}")
                        self._set_oauth_error(f"/email-otp/validate exception: {e}")
                        continue
                    self._print(f"[OAuth] /email-otp/validate -> {resp_otp.status_code}")
                    if resp_otp.status_code != 200:
                        self._print(f"[OAuth] OTP 无效: {resp_otp.text[:160]}")
                        otp_text = (resp_otp.text or "")[:240]
                        self._set_oauth_error(f"/email-otp/validate -> {resp_otp.status_code}: {otp_text}")
                        lowered = otp_text.lower()
                        if (
                            resp_otp.status_code == 403
                            and ("deleted or deactivated" in lowered or "do not have an account" in lowered)
                        ):
                            return None
                        continue
                    try:
                        otp_data = resp_otp.json()
                    except Exception:
                        continue
                    continue_url = otp_data.get("continue_url", "") or continue_url
                    page_type = (otp_data.get("page") or {}).get("type", "") or page_type
                    self._print(f"[OAuth] OTP 通过 page={page_type or '-'} next={(continue_url or '-')[:140]}")
                    otp_success = True
                    break
                if not otp_success:
                    time.sleep(2)
            if not otp_success:
                self._print(f"[OAuth] OTP 验证失败，已尝试 {len(tried_codes)} 个验证码")
                if not self.oauth_last_error:
                    self._set_oauth_error("otp validation failed")
                return None

        code = None
        consent_url = continue_url
        if consent_url and consent_url.startswith("/"):
            consent_url = f"{self.oauth_issuer}{consent_url}"
        if not consent_url and "consent" in page_type:
            consent_url = f"{self.oauth_issuer}/sign-in-with-chatgpt/codex/consent"
        if consent_url:
            code = Utils.extract_code_from_url(consent_url)
        if not code and consent_url:
            self._print("[OAuth] 5/7 跟随 continue_url 提取 code")
            code, _ = self._oauth_follow_for_code(consent_url, referer=f"{self.oauth_issuer}/log-in/password")

        consent_hint = any([
            "consent" in (consent_url or ""), "sign-in-with-chatgpt" in (consent_url or ""),
            "workspace" in (consent_url or ""), "organization" in (consent_url or ""),
            "consent" in page_type, "organization" in page_type,
        ])
        if not code and consent_hint:
            if not consent_url:
                consent_url = f"{self.oauth_issuer}/sign-in-with-chatgpt/codex/consent"
            self._print("[OAuth] 6/7 执行 workspace/org 选择")
            code = self._oauth_submit_workspace_and_org(consent_url)

        if not code:
            fallback = f"{self.oauth_issuer}/sign-in-with-chatgpt/codex/consent"
            self._print("[OAuth] 6/7 回退 consent 路径重试")
            code = self._oauth_submit_workspace_and_org(fallback)
            if not code:
                code, _ = self._oauth_follow_for_code(fallback, referer=f"{self.oauth_issuer}/log-in/password")

        if not code:
            self._print("[OAuth] 未获取到 authorization code")
            self._set_oauth_error("authorization code missing")
            return None

        self._print("[OAuth] 7/7 POST /oauth/token")
        token_resp = self.session.post(
            f"{self.oauth_issuer}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": self.ua},
            data={"grant_type": "authorization_code", "code": code,
                  "redirect_uri": self.oauth_redirect_uri, "client_id": self.oauth_client_id,
                  "code_verifier": code_verifier},
            timeout=60, impersonate=self.impersonate,
        )
        self._print(f"[OAuth] /oauth/token -> {token_resp.status_code}")
        if token_resp.status_code != 200:
            self._print(f"[OAuth] token 交换失败: {token_resp.status_code} {token_resp.text[:200]}")
            self._set_oauth_error(f"/oauth/token -> {token_resp.status_code}: {(token_resp.text or '')[:240]}")
            return None
        try:
            data = token_resp.json()
        except Exception:
            self._print("[OAuth] token 响应解析失败")
            self._set_oauth_error("/oauth/token response parse failed")
            return None
        if not data.get("access_token"):
            self._print("[OAuth] token 响应缺少 access_token")
            self._set_oauth_error("/oauth/token missing access_token")
            return None
        self._print("[OAuth] Codex Token 获取成功")
        self.oauth_last_error = ""
        return data
