"""后台任务执行器（注册 & Token 刷新）"""

import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import read_config
from app.tasks import task_log, finish_task
from app.storage import (
    get_accounts_file,
    get_token_dir,
    parse_invalid_accounts,
    update_account_status,
    upsert_invalid_account,
)
from app.lib.chatgpt import ChatGPTRegister
from app.lib.storage import DataStorage
from app.lib.upload import CpaUploader, DEFAULT_CPA_PROBE_UA
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
        if email not in accounts:
            task_log(task, f"未找到账号: {email}")
            finish_task(task, "error", "account not found")
            return

        task_log(task, "开始执行 OAuth 刷新并回传 CPA")
        _refresh_one_email(email, accounts, storage, proxy)
        task_log(task, f"✓ Token 刷新完成: {email}")
        finish_task(task, "done", {"email": email})
    except Exception as e:
        if _is_deactivated_account_error(e):
            _mark_invalid_account(email, str(e), task)
            finish_task(task, "done", {"email": email, "invalid_marked": True, "reason": "account_deactivated"})
            return
        update_account_status(email, "oauth=failed")
        task_log(task, f"✗ 刷新失败: {e}\n{traceback.format_exc()}")
        finish_task(task, "error", str(e))
    finally:
        _unregister_thread()


def _refresh_one_email(email: str, accounts: dict, storage: DataStorage, proxy: str):
    acc = accounts.get(email)
    if not acc:
        raise Exception("account not found")

    chatgpt_password, email_pwd, _raw = acc
    reg = ChatGPTRegister(proxy=proxy, tag=email.split("@")[0])

    mail_token = None
    if email_pwd:
        mail_token = reg.tmail_login_for_token(email, email_pwd)

    tokens = reg.perform_codex_oauth_login_http(email, chatgpt_password, mail_token=mail_token)
    if not (tokens and tokens.get("access_token")):
        detail = getattr(reg, "oauth_last_error", "") or "OAuth 获取 Token 失败"
        raise Exception(detail)

    storage.save_codex_tokens(email, tokens)
    update_account_status(email, "oauth=ok")


def _is_deactivated_account_error(err: Exception | str) -> bool:
    text = str(err or "").lower()
    return (
        "/email-otp/validate -> 403" in text
        and ("deleted or deactivated" in text or "do not have an account" in text)
    )


def _mark_invalid_account(email: str, reason: str, task: dict | None = None) -> None:
    update_account_status(email, "oauth=invalid")
    cpa_deleted = False
    cpa_delete_error = ""
    try:
        delete_res = CpaUploader.from_config().delete_auth_file_by_email(email, timeout=30)
        cpa_deleted = bool(delete_res.get("deleted"))
        cpa_delete_error = str(delete_res.get("error") or "")
    except Exception as e:
        cpa_delete_error = str(e)

    upsert_invalid_account(
        email=email,
        reason=reason,
        source="oauth_refresh",
        cpa_deleted=cpa_deleted,
        cpa_delete_error=cpa_delete_error,
    )
    if task is not None:
        if cpa_deleted:
            task_log(task, f"[{email}] 已判定失效，CPA 记录已删除，并加入失效列表")
        else:
            task_log(task, f"[{email}] 已判定失效，加入失效列表（CPA 删除失败: {cpa_delete_error or 'unknown'}）")


def _list_target_emails(emails: list[str], token_dir: Path) -> list[str]:
    if emails:
        return [e for e in emails if e]
    result: list[str] = []
    if not token_dir.exists():
        return result
    for jf in sorted(token_dir.glob("*.json")):
        result.append(jf.stem)
    return result


def run_sync_cpa(task: dict, emails: list[str]) -> None:
    _register_thread(task["logs"])
    try:
        task["status"] = "running"
        cfg = read_config()
        proxy = cfg.get("proxy", "") or None
        probe_user_agent = cfg.get("probe_user_agent", DEFAULT_CPA_PROBE_UA)
        storage = DataStorage()
        uploader = CpaUploader.from_config()
        token_dir = get_token_dir()
        accounts = storage.load_registered_accounts(str(get_accounts_file()))
        invalid_email_set = {
            str(item.get("email") or "").strip().lower()
            for item in parse_invalid_accounts()
            if str(item.get("email") or "").strip()
        }

        if not cfg.get("upload_api_url"):
            raise Exception("upload_api_url not configured")
        if not cfg.get("upload_api_token"):
            raise Exception("upload_api_token not configured")

        target_emails = _list_target_emails(emails, token_dir)
        if not target_emails:
            task_log(task, "没有可同步的账号")
            finish_task(task, "done", {"total": 0})
            return

        task_log(task, f"开始同步 CPA: {len(target_emails)} 个账号")
        task_log(task, "步骤 1/3: 拉取 CPA auth-files...")
        auth_files = uploader.fetch_auth_files(timeout=30)
        task_log(task, f"步骤 1/3 完成: 拉取到 {len(auth_files)} 条 CPA 记录")

        # auth-files 的 email 字段在不同后端版本可能是 email/account/name，统一索引。
        cpa_email_map: dict[str, dict] = {}
        for item in auth_files:
            email = CpaUploader.get_file_email(item)
            if email:
                cpa_email_map[email.lower()] = item
        task_log(task, f"步骤 2/3: 已建立 CPA 邮箱索引 {len(cpa_email_map)} 条，开始逐个校验...")

        ok_count = 0
        uploaded_count = 0
        refreshed_count = 0
        invalid_marked_count = 0
        invalid_skip_upload_count = 0
        missing_count = 0
        failed_count = 0
        results: dict[str, str] = {}

        total = len(target_emails)
        for idx, email in enumerate(target_emails, start=1):
            task_log(task, f"[{idx}/{total}] 处理 {email}")
            token_path = token_dir / f"{email}.json"
            if not token_path.exists():
                results[email] = "local_token_not_found"
                missing_count += 1
                task_log(task, f"[{idx}/{total}] {email} -> 本地 token 不存在，跳过")
                continue

            cpa_item = cpa_email_map.get(email.lower())
            if not cpa_item:
                if email.lower() in invalid_email_set:
                    results[email] = "skip_upload_invalid_listed"
                    invalid_skip_upload_count += 1
                    ok_count += 1
                    task_log(task, f"[{idx}/{total}] {email} -> 在失效列表中，跳过补传 token")
                    continue
                try:
                    uploader.upload_token_json(str(token_path))
                    results[email] = "uploaded_missing_in_cpa"
                    uploaded_count += 1
                    ok_count += 1
                    task_log(task, f"[{idx}/{total}] {email} -> CPA 不存在，已补传 token")
                except Exception as e:
                    results[email] = f"upload_failed: {e}"
                    failed_count += 1
                    task_log(task, f"[{idx}/{total}] {email} -> CPA 不存在，补传失败: {e}")
                continue

            auth_index = cpa_item.get("auth_index")
            if not auth_index:
                results[email] = "exists_without_auth_index"
                ok_count += 1
                task_log(task, f"[{idx}/{total}] {email} -> CPA 已存在但无 auth_index，跳过 probe")
                continue

            account_id = (
                CpaUploader.read_account_id_from_token_file(str(token_path))
                or CpaUploader.get_file_account_id(cpa_item)
            )
            task_log(task, f"[{idx}/{total}] {email} -> 步骤 3/3 probe auth_index={auth_index}")
            probe = uploader.probe_auth_index(
                str(auth_index),
                chatgpt_account_id=account_id,
                user_agent=probe_user_agent,
                timeout=30,
            )
            if probe.get("invalid_401"):
                try:
                    _refresh_one_email(email, accounts, storage, proxy)
                    results[email] = "refreshed_after_401"
                    refreshed_count += 1
                    ok_count += 1
                    task_log(task, f"[{idx}/{total}] {email} -> 401，token 刷新成功")
                except Exception as e:
                    if _is_deactivated_account_error(e):
                        _mark_invalid_account(email, str(e), task)
                        results[email] = "marked_invalid_account_deactivated"
                        invalid_marked_count += 1
                        ok_count += 1
                        task_log(task, f"[{idx}/{total}] {email} -> 401，检测为失效账号，已加入失效列表")
                        continue
                    update_account_status(email, "oauth=failed")
                    results[email] = f"refresh_failed: {e}"
                    failed_count += 1
                    task_log(task, f"[{idx}/{total}] {email} -> 401，token 刷新失败: {e}")
                continue

            if probe.get("error"):
                results[email] = f"probe_failed: {probe.get('error')}"
                failed_count += 1
                task_log(task, f"[{idx}/{total}] {email} -> probe 失败: {probe.get('error')}")
                continue

            results[email] = "ok"
            ok_count += 1
            task_log(task, f"[{idx}/{total}] {email} -> probe 正常")

        summary = {
            "total": len(target_emails),
            "ok": ok_count,
            "uploaded_missing": uploaded_count,
            "refreshed_401": refreshed_count,
            "invalid_marked": invalid_marked_count,
            "invalid_skip_upload": invalid_skip_upload_count,
            "local_token_not_found": missing_count,
            "failed": failed_count,
            "results": results,
        }
        task_log(task, f"同步完成: ok={ok_count}, uploaded={uploaded_count}, refreshed={refreshed_count}, invalid={invalid_marked_count}, skip_upload={invalid_skip_upload_count}, failed={failed_count}")
        finish_task(task, "done", summary)
    except Exception as e:
        task_log(task, f"任务异常: {e}\n{traceback.format_exc()}")
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
        invalid_marked_count = 0
        for email in emails:
            if email not in accounts:
                task_log(task, f"[{email}] ✗ 未找到账号记录")
                fail_count += 1
                continue
            try:
                _refresh_one_email(email, accounts, storage, proxy)
                task_log(task, f"[{email}] ✓ Token 刷新成功")
                ok_count += 1
            except Exception as e:
                if _is_deactivated_account_error(e):
                    _mark_invalid_account(email, str(e), task)
                    invalid_marked_count += 1
                    continue
                update_account_status(email, "oauth=failed")
                task_log(task, f"[{email}] ✗ 失败: {e}")
                fail_count += 1

        task_log(task, f"完成: 成功 {ok_count} | 标记失效 {invalid_marked_count} | 失败 {fail_count}")
        finish_task(task, "done", {"ok": ok_count, "invalid_marked": invalid_marked_count, "fail": fail_count})
    except Exception as e:
        task_log(task, f"任务异常: {e}\n{traceback.format_exc()}")
        finish_task(task, "error", str(e))
    finally:
        _unregister_thread()
