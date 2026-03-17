"""工具类：随机生成、PKCE、JWT 解码等无状态工具"""

import re
import json
import random
import string
import time
import uuid
import secrets
import hashlib
import base64
import threading
from urllib.parse import urlparse, parse_qs

# 全局 print 锁（所有模块共享）
print_lock = threading.Lock()

_CHROME_PROFILES = [
    {
        "major": 131, "impersonate": "chrome131",
        "build": 6778, "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133, "impersonate": "chrome133a",
        "build": 6943, "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136, "impersonate": "chrome136",
        "build": 7103, "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
    {
        "major": 142, "impersonate": "chrome142",
        "build": 7540, "patch_range": (30, 150),
        "sec_ch_ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    },
]


class Utils:
    """通用静态工具方法集合"""

    @staticmethod
    def random_chrome_version() -> tuple:
        """返回 (impersonate, major, full_ver, ua, sec_ch_ua)"""
        profile = random.choice(_CHROME_PROFILES)
        major = profile["major"]
        build = profile["build"]
        patch = random.randint(*profile["patch_range"])
        full_ver = f"{major}.0.{build}.{patch}"
        ua = (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
        )
        return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]

    @staticmethod
    def random_delay(low: float = 0.3, high: float = 1.0) -> None:
        time.sleep(random.uniform(low, high))

    @staticmethod
    def make_trace_headers() -> dict:
        trace_id = random.randint(10**17, 10**18 - 1)
        parent_id = random.randint(10**17, 10**18 - 1)
        tp = f"00-{uuid.uuid4().hex}-{format(parent_id, '016x')}-01"
        return {
            "traceparent": tp, "tracestate": "dd=s:1;o:rum",
            "x-datadog-origin": "rum", "x-datadog-sampling-priority": "1",
            "x-datadog-trace-id": str(trace_id), "x-datadog-parent-id": str(parent_id),
        }

    @staticmethod
    def generate_pkce() -> tuple:
        """返回 (code_verifier, code_challenge)"""
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge

    @staticmethod
    def generate_password(length: int = 14) -> str:
        lower, upper, digits, special = (
            string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%&*"
        )
        pwd = [random.choice(lower), random.choice(upper),
               random.choice(digits), random.choice(special)]
        all_chars = lower + upper + digits + special
        pwd += [random.choice(all_chars) for _ in range(length - 4)]
        random.shuffle(pwd)
        return "".join(pwd)

    @staticmethod
    def random_name() -> str:
        first = random.choice([
            "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia",
            "Lucas", "Mia", "Mason", "Isabella", "Logan", "Charlotte", "Alexander",
            "Amelia", "Benjamin", "Harper", "William", "Evelyn", "Henry", "Abigail",
            "Sebastian", "Emily", "Jack", "Elizabeth",
        ])
        last = random.choice([
            "Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore", "Taylor",
            "Clark", "Hall", "Young", "Anderson", "Thomas", "Jackson", "White",
            "Harris", "Martin", "Thompson", "Garcia", "Robinson", "Lewis",
            "Walker", "Allen", "King", "Wright", "Scott", "Green",
        ])
        return f"{first} {last}"

    @staticmethod
    def random_birthdate() -> str:
        y = random.randint(1985, 2002)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{y}-{m:02d}-{d:02d}"

    @staticmethod
    def extract_code_from_url(url: str):
        if not url or "code=" not in url:
            return None
        try:
            return parse_qs(urlparse(url).query).get("code", [None])[0]
        except Exception:
            return None

    @staticmethod
    def decode_jwt_payload(token: str) -> dict:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return {}
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        except Exception:
            return {}

    @staticmethod
    def as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
