"""Command line interface for CppMc."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from . import auth
from .core import (
    APP_BRAND,
    APP_VERSION,
    DEFAULT_GAME_DIR,
    Launcher,
    LauncherError,
    load_config,
    save_config,
)
from .java import resolve_java


def _ensure_utf8_streams() -> None:
    """Force UTF-8 output so Chinese logs never trip a cp936/cp1252 console.

    Also covers the windowed (``--noconsole``) exe, where sys.stdout is None:
    prints are then no-ops instead of an AttributeError.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _print_log(message: str) -> None:
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CppMc",
        description=f"{APP_BRAND} {APP_VERSION} — 一个纯 Python 写成的 Minecraft 启动器。",
    )
    parser.add_argument("--list", action="store_true", help="列出所有可用版本后退出")
    parser.add_argument("--install", metavar="VERSION", help="只下载（安装）指定版本，不启动")
    parser.add_argument("--launch", metavar="VERSION", help="下载并启动指定版本")
    parser.add_argument("--name", metavar="PLAYER", help="离线模式用户名")
    parser.add_argument("--memory", type=int, metavar="MB", help="最大内存（MB），默认 2048")
    parser.add_argument("--java", metavar="PATH", help="java 可执行文件路径（默认自动检测/自动下载）")
    parser.add_argument("--no-auto-java", action="store_true",
                        help="找不到合适 Java 时不要自动下载")
    parser.add_argument("--game-dir", metavar="DIR", help="游戏目录，默认 %%APPDATA%%\\CppMc\\game")
    parser.add_argument("--dry-run", action="store_true", help="只打印启动命令，不实际启动")
    parser.add_argument("--refresh", action="store_true", help="强制刷新版本列表")
    account_group = parser.add_argument_group("账号")
    account_group.add_argument("--login", action="store_true", help="用微软账号登录（设备码）")
    account_group.add_argument("--logout", action="store_true", help="删除本机保存的账号")
    account_group.add_argument("--account", action="store_true", help="显示当前账号")
    account_group.add_argument("--offline", action="store_true", help="强制离线模式启动")
    return parser


def _print_account(account: dict) -> None:
    remaining = int(float(account.get("expires_at") or 0) - time.time())
    state = (f"有效，约 {max(remaining, 0) // 60} 分钟后到期" if remaining > 0
             else "已过期，启动时会自动刷新")
    print(f"正版账号：{account.get('name')}")
    print(f"  UUID  ：{account.get('uuid')}")
    print(f"  令牌  ：{state}")
    if account.get("xuid"):
        print(f"  XUID  ：{account.get('xuid')}")


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if not argv:
        from .gui import run_gui

        return run_gui()

    config = load_config()

    # ---- account only commands
    try:
        if args.logout:
            auth.clear_account()
            print("已删除本机保存的微软账号，之后按离线模式启动。")
            return 0

        if args.account:
            account = auth.ensure_account(log=_print_log)
            if account:
                _print_account(account)
            else:
                print("当前没有登录的账号，启动时使用离线模式。用 --login 登录微软账号。")
            return 0

        if args.login:
            def show_code(code: str, url: str) -> None:
                print(f"\n请用浏览器打开：{url}")
                print(f"并输入代码：{code}\n")
                print("（这个窗口会一直等，登录完成会自动继续）\n")

            account = auth.login(on_code=show_code, log=_print_log)
            print(f"登录成功：{account['name']}，之后启动会自动使用正版账号。")
            print("提示：账号令牌保存在 %APPDATA%\\CppMc\\account.json，可随时用 --logout 删除。")
            return 0
    except LauncherError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    game_dir = args.game_dir or config.get("game_dir") or str(DEFAULT_GAME_DIR)
    launcher = Launcher(Path(game_dir), log=_print_log)

    try:
        if args.list:
            entries = launcher.version_entries(refresh=args.refresh)
            latest = launcher.manifest().get("latest", {})
            print(f"最新正式版：{latest.get('release')}    最新快照：{latest.get('snapshot')}")
            for entry in entries:
                print(f"  {entry.get('id'):<28} {entry.get('type', ''):<10} {entry.get('releaseTime', '')}")
            return 0

        version_id = args.launch or args.install
        if not version_id:
            parser.print_help()
            return 2

        username = args.name or config.get("username") or "Player"
        memory_mb = int(args.memory or config.get("memory_mb") or 2048)
        download = not args.dry_run

        account = None if args.offline else auth.ensure_account(log=_print_log)
        if account:
            username = account["name"]
            _print_log(f"使用正版账号：{account['name']}")
        else:
            _print_log(f"使用离线模式：{username}")

        prepared = launcher.install(version_id, download=download)

        required = prepared.java_major_required
        java_path = args.java or resolve_java(
            str(config.get("java_path") or ""),
            required,
            _print_log,
            lambda label, current, total: None,
            allow_download=not args.no_auto_java and not args.dry_run,
        )[0]
        if not java_path:
            if not args.dry_run:
                raise LauncherError("没有找到 Java，也无法自动下载。请用 --java 指定 java.exe 的路径。")
            java_path = "java"

        command = launcher.build_command(
            prepared,
            username=username,
            java_path=java_path,
            memory_mb=memory_mb,
        )
        if account:
            command = auth.apply_account(command, account)

        if args.dry_run:
            print(subprocess.list2cmdline(command))
            return 0

        if args.install and not args.launch:
            config.update({"version": version_id, "username": username})
            save_config(config)
            print(f"{version_id} 安装完成，文件位于 {launcher.game_dir}")
            return 0

        process = launcher.launch(command, on_line=lambda line: print(line, flush=True))
        code = process.wait()
        if code != 0:
            print(f"游戏进程退出，退出码 {code}")
            return code
        return 0

    except LauncherError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
