"""后台任务执行器（注册 & Token 刷新）"""

import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.config import read_config
from app.tasks import task_log, finish_task
from app.storage import get_accounts_file, update_account_status
from app.lib.chatgpt import ChatGPTRegister
from app.lib.storage import DataStorage
from app.lib.utils import Utils, print_lock

# ── stdout 路由（按线程捕获 print 到任务日志）────────────────────

_thread_log_map: dict[int, list] = {}
_tls_lock = threading.Lock()


class _TeeStream:
    """透传 stdout，同时将当前线程的输出追加到对应任务日志。"""

    def __init__(self, original: object) -> None:
        self._orig = original
        self._local = threading.local()

    def write(self, text: str) -> int:
        self._orig.write(text)
        tid = threading.get_ident()
        with _tls_lock:
            task_logs = _thread_log_map.get(tid)
        if task_logs is None:
            return len(text)
        buf = getattr(self._local, "buf", "") + text
        lines = buf.split("\n")
        self._local.buf = lines[-1]
        ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
        for line in lines[:-1]:
            stripped = line.strip()
            if stripped:
                task_logs.append(f"[{ts}] {stripped}")
        return len(text)

    def flush(self) -> None:
        self._orig.flush()

    def __getattr__(self, name: str):
        return getattr(self._orig, name)


def install_tee_stream() -> None:
    """安装 stdout 劫持流（幂等）。"""
    if not isinstance(sys.stdout, _TeeStream):
        sys.stdout = _TeeStream(sys.stdout)


def _register_thread(task_logs: list) -> None:
    with _tls_lock:
        _thread_log_map[threading.get_ident()] = task_logs


def _unregister_thread() -> None:
    with _tls_lock:
        _thread_log_map.pop(threading.get_ident(), None)


# ── 单账号注册（线程内运行）──────────────────────────────────────

def _register_one(idx: int, total: int, proxy: str, output_file: str, task_logs: list):
    """在线程中执行单次注册，返回 (ok, email, error_msg)"""
    _register_thread(task_logs)
    try:
        reg = ChatGPTRegister(proxy=proxy, tag=f"{idx}")

        reg._print("[tmail] 创建临时邮箱...")
        email, email_pwd, mail_token = reg.create_temp_email()
        reg.tag = email.split("@")[0]
        reg.tmail.tag = reg.tag

        chatgpt_password = Utils.generate_password()
        name = Utils.random_name()
        birthdate = Utils.random_birthdate()

        with print_lock:
            print(f"\n{'=' * 60}")
            print(f"  [{idx}/{total}] 注册: {email}")
            print(f"  ChatGPT密码: {chatgpt_password}")
            print(f"  邮箱密码: {email_pwd}")
            print(f"  姓名: {name} | 生日: {birthdate}")
            print(f"{'=' * 60}")

        reg.run_register(email, chatgpt_password, name, birthdate, mail_token)

        # OAuth
        cfg = read_config()
        oauth_ok = False
        if Utils.as_bool(cfg.get("enable_oauth", True)):
            tokens = reg.perform_codex_oauth_login_http(email, chatgpt_password, mail_token=mail_token)
            oauth_ok = bool(tokens and tokens.get("access_token"))
            if oauth_ok:
                DataStorage().save_codex_tokens(email, tokens)
                reg._print("[OAuth] Token 已保存")
            elif Utils.as_bool(cfg.get("oauth_required", True)):
                raise Exception("OAuth 获取失败（oauth_required=true）")
            else:
                reg._print("[OAuth] OAuth 获取失败（按配置继续）")

        DataStorage().write_account_line(output_file, email, chatgpt_password, email_pwd, oauth_ok)

        with print_lock:
            print(f"\n[OK] [{reg.tag}] {email} 注册成功!")
        return True, email, None

    except Exception as e:
        with print_lock:
            print(f"\n[FAIL] [{idx}] 注册失败: {e}")
            traceback.print_exc()
        return False, None, str(e)
    finally:
        _unregister_thread()


# ── 批量注册 ──────────────────────────────────────────────────

def run_register(task: dict, count: int, concurrency: int) -> None:
    _register_thread(task["logs"])
    try:
        task["status"] = "running"
        cfg = read_config()
        proxy = cfg.get("proxy", "") or None
        output_file = str(get_accounts_file())
        task_log(task, f"开始批量注册: {count} 个账号，并发 {concurrency}")

        success = fail = 0

        def _one(idx: int):
            return _register_one(idx, count, proxy, output_file, task["logs"])

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_one, i): i for i in range(1, count + 1)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    ok, email, err = fut.result()
                    if ok:
                        success += 1
                        task_log(task, f"[{idx}] ✓ {email} 注册成功")
                    else:
                        fail += 1
                        task_log(task, f"[{idx}] ✗ 失败: {err}")
                except Exception as e:
                    fail += 1
                    task_log(task, f"[{idx}] ✗ 线程异常: {e}")

        task_log(task, f"完成: 成功 {success} | 失败 {fail}")
        finish_task(task, "done", {"success": success, "fail": fail})
    except Exception as e:
        task_log(task, f"任务异常: {e}\n{traceback.format_exc()}")
        finish_task(task, "error", str(e))
    finally:
        _unregister_thread()


# ── 单账号 Token 刷新 ─────────────────────────────────────────

def run_refresh(task: dict, email: str) -> None:
    _register_thread(task["logs"])
    try:
        task["status"] = "running"
        cfg = read_config()
        proxy = cfg.get("proxy", "") or None
        storage = DataStorage()
        task_log(task, f"刷新 Token: {email}")

        accounts = storage.load_registered_accounts(str(get_accounts_file()))
        acc = accounts.get(email)
        if not acc:
            task_log(task, f"未找到账号: {email}")
            finish_task(task, "error", "account not found")
            return

        chatgpt_password, email_pwd, _raw = acc
        reg = ChatGPTRegister(proxy=proxy, tag=email.split("@")[0])

        mail_token = None
        if email_pwd:
            task_log(task, "使用邮箱密码获取 tmail JWT...")
            mail_token = reg.tmail_login_for_token(email, email_pwd)

        tokens = reg.perform_codex_oauth_login_http(email, chatgpt_password, mail_token=mail_token)
        if not (tokens and tokens.get("access_token")):
            raise Exception("OAuth 获取 Token 失败")

        storage.save_codex_tokens(email, tokens)
        update_account_status(email, "oauth=ok")
        task_log(task, f"✓ Token 刷新完成: {email}")
        finish_task(task, "done", {"email": email})
    except Exception as e:
        update_account_status(email, "oauth=failed")
        task_log(task, f"✗ 刷新失败: {e}\n{traceback.format_exc()}")
        finish_task(task, "error", str(e))
    finally:
        _unregister_thread()


# ── 批量 Token 刷新 ───────────────────────────────────────────

def run_batch_refresh(task: dict, emails: list) -> None:
    _register_thread(task["logs"])
    try:
        task["status"] = "running"
        cfg = read_config()
        proxy = cfg.get("proxy", "") or None
        storage = DataStorage()
        accounts = storage.load_registered_accounts(str(get_accounts_file()))
        task_log(task, f"批量刷新 Token: {len(emails)} 个账号")

        ok_count = fail_count = 0
        for email in emails:
            acc = accounts.get(email)
            if not acc:
                task_log(task, f"[{email}] ✗ 未找到账号记录")
                fail_count += 1
                continue
            chatgpt_password, email_pwd, _raw = acc
            reg = ChatGPTRegister(proxy=proxy, tag=email.split("@")[0])
            try:
                mail_token = None
                if email_pwd:
                    mail_token = reg.tmail_login_for_token(email, email_pwd)
                tokens = reg.perform_codex_oauth_login_http(email, chatgpt_password, mail_token=mail_token)
                if not (tokens and tokens.get("access_token")):
                    raise Exception("OAuth 获取 Token 失败")
                storage.save_codex_tokens(email, tokens)
                update_account_status(email, "oauth=ok")
                task_log(task, f"[{email}] ✓ Token 刷新成功")
                ok_count += 1
            except Exception as e:
                update_account_status(email, "oauth=failed")
                task_log(task, f"[{email}] ✗ 失败: {e}")
                fail_count += 1

        task_log(task, f"完成: 成功 {ok_count} | 失败 {fail_count}")
        finish_task(task, "done", {"ok": ok_count, "fail": fail_count})
    except Exception as e:
        task_log(task, f"任务异常: {e}\n{traceback.format_exc()}")
        finish_task(task, "error", str(e))
    finally:
        _unregister_thread()
