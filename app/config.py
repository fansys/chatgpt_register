"""配置读写模块"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG: dict = {
    "total_accounts": 3,
    "tmail_api_base": "https://xxxx.xxxx",
    "tmail_admin_auth": "",
    "tmail_domain": "awsl.uk",
    "tmail_custom_auth": "",
    "proxy": "",
    "output_file": "data/accounts.json",
    "otp_wait_seconds": 10,
    "enable_oauth": True,
    "oauth_required": True,
    "oauth_issuer": "https://auth.openai.com",
    "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    "oauth_redirect_uri": "http://localhost:1455/auth/callback",
    "token_json_dir": "data/tokens",
    "upload_api_url": "",
    "upload_api_token": "",
}


def read_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def write_config(data: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
