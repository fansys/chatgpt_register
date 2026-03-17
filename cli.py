#!/usr/bin/env python3
"""
ChatGPT Register Manager — 命令行工具

用法:
  python cli.py register   --count 5 --concurrency 2
  python cli.py refresh    --email user@example.com
  python cli.py refresh-all
  python cli.py accounts   [--filter keyword]
  python cli.py tokens
  python cli.py config     [--set KEY=VALUE ...]
"""

import sys
import json
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))


# ── 子命令实现 ────────────────────────────────────────────────

def cmd_register(args: argparse.Namespace) -> None:
    from app.tasks import create_task
    from app.workers import install_tee_stream, run_register
    install_tee_stream()
    task = create_task("register")
    print(f"[任务 {task['id']}] 开始注册 {args.count} 个账号（并发 {args.concurrency}）...\n")
    run_register(task, args.count, args.concurrency)
    result = task.get("result") or {}
    print(f"\n结果: 成功 {result.get('success', '?')} | 失败 {result.get('fail', '?')}")
    sys.exit(0 if task["status"] == "done" else 1)


def cmd_refresh(args: argparse.Namespace) -> None:
    from app.tasks import create_task
    from app.workers import install_tee_stream, run_refresh
    install_tee_stream()
    task = create_task("refresh")
    print(f"[任务 {task['id']}] 刷新 Token: {args.email}\n")
    run_refresh(task, args.email)
    print(f"\n状态: {task['status']}")
    sys.exit(0 if task["status"] == "done" else 1)


def cmd_refresh_all(args: argparse.Namespace) -> None:
    from app.storage import parse_accounts
    from app.tasks import create_task
    from app.workers import install_tee_stream, run_batch_refresh
    install_tee_stream()
    accounts = parse_accounts()
    emails = [a["email"] for a in accounts if a["email"]]
    if not emails:
        print("没有找到任何账号")
        sys.exit(1)
    task = create_task("refresh-batch")
    print(f"[任务 {task['id']}] 批量刷新 {len(emails)} 个账号...\n")
    run_batch_refresh(task, emails)
    result = task.get("result") or {}
    print(f"\n结果: 成功 {result.get('ok', '?')} | 失败 {result.get('fail', '?')}")
    sys.exit(0 if task["status"] == "done" else 1)


def cmd_accounts(args: argparse.Namespace) -> None:
    from app.storage import parse_accounts
    accounts = parse_accounts()
    keyword = (args.filter or "").lower()
    if keyword:
        accounts = [a for a in accounts if keyword in a["email"].lower()]
    if not accounts:
        print("没有匹配的账号记录")
        return
    print(f"\n共 {len(accounts)} 个账号:\n")
    header = f"{'#':<4} {'邮箱':<42} {'OAuth状态':<18} {'Token刷新时间'}"
    print(header)
    print("-" * len(header))
    for i, acc in enumerate(accounts, 1):
        status = acc.get("status") or "—"
        refresh = acc.get("token_last_refresh") or "未获取"
        print(f"{i:<4} {acc['email']:<42} {status:<18} {refresh}")


def cmd_tokens(args: argparse.Namespace) -> None:
    from app.storage import parse_tokens
    tokens = parse_tokens()
    if not tokens:
        print("没有 Token 记录")
        return
    print(f"\n共 {len(tokens)} 个 Token:\n")
    header = f"{'#':<4} {'邮箱':<42} {'最后刷新':<26} {'过期时间'}"
    print(header)
    print("-" * len(header))
    for i, tok in enumerate(tokens, 1):
        refresh = tok.get("last_refresh") or tok.get("updated") or "—"
        expired = tok.get("expired") or "—"
        print(f"{i:<4} {tok['email']:<42} {refresh:<26} {expired}")


def cmd_config(args: argparse.Namespace) -> None:
    from app.config import read_config, write_config
    cfg = read_config()
    if args.set:
        for kv in args.set:
            k, _, v = kv.partition("=")
            k = k.strip()
            v = v.strip()
            try:
                cfg[k] = json.loads(v)
            except json.JSONDecodeError:
                cfg[k] = v
        write_config(cfg)
        print("配置已保存")
        for kv in args.set:
            k, _, _ = kv.partition("=")
            k = k.strip()
            print(f"  {k} = {json.dumps(cfg.get(k), ensure_ascii=False)}")
    else:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))


# ── 入口 ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="ChatGPT Register Manager — 命令行工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # register
    p_reg = sub.add_parser("register", help="批量注册 ChatGPT 账号")
    p_reg.add_argument("--count", type=int, default=1, metavar="N", help="注册数量 (默认 1)")
    p_reg.add_argument("--concurrency", type=int, default=1, metavar="N", help="并发数 (默认 1)")

    # refresh
    p_ref = sub.add_parser("refresh", help="刷新单个账号的 OAuth Token")
    p_ref.add_argument("--email", required=True, help="账号邮箱地址")

    # refresh-all
    sub.add_parser("refresh-all", help="批量刷新所有账号的 OAuth Token")

    # accounts
    p_acc = sub.add_parser("accounts", help="查看账号列表")
    p_acc.add_argument("--filter", metavar="KEYWORD", help="按邮箱关键字过滤")

    # tokens
    sub.add_parser("tokens", help="查看 Token 列表")

    # config
    p_cfg = sub.add_parser("config", help="查看或修改配置 (不带参数则打印完整配置)")
    p_cfg.add_argument(
        "--set",
        nargs="+",
        metavar="KEY=VALUE",
        help="设置配置项，值自动解析 JSON 类型，例: --set proxy=http://127.0.0.1:7890 enable_oauth=true",
    )

    args = parser.parse_args()
    handlers = {
        "register": cmd_register,
        "refresh": cmd_refresh,
        "refresh-all": cmd_refresh_all,
        "accounts": cmd_accounts,
        "tokens": cmd_tokens,
        "config": cmd_config,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
