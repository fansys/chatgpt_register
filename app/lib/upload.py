"""CPA 平台上传类"""

import os
import threading

from app.config import read_config

_print_lock = threading.Lock()


class CpaUploader:
    """上传 Token JSON 文件到 CPA 管理平台"""

    def __init__(self, upload_url: str, upload_token: str, proxy: str = ""):
        self.upload_url = upload_url
        self.upload_token = upload_token
        self.proxy = proxy

    @classmethod
    def from_config(cls) -> "CpaUploader":
        cfg = read_config()
        return cls(
            upload_url=cfg.get("upload_api_url", ""),
            upload_token=cfg.get("upload_api_token", ""),
            proxy=cfg.get("proxy", ""),
        )

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
                headers={"Authorization": f"Bearer {self.upload_token}"},
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
