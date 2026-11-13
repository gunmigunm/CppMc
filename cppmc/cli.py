"""Command line interface for CppMc."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .core import (
    APP_BRAND,
    APP_VERSION,
    DEFAULT_GAME_DIR,
    Launcher,
    LauncherError,
    find_java,
    java_major_version,
    load_config,
    save_config,
)


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
    parser.add_argument("--java", metavar="PATH", help="java 可执行文件路径，默认自动检测")
    parser.add_argument("--game-dir", metavar="DIR", help="游戏目录，默认 %%APPDATA%%\\CppMc\\game")
    parser.add_argument("--dry-run", action="store_true", help="只打印启动命令，不实际启动")
    parser.add_argument("--refresh", action="store_true", help="强制刷新版本列表")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if not argv:
        from .gui import run_gui

        return run_gui()

    config = load_config()
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

        java_path = args.java or find_java(str(config.get("java_path") or ""))
        prepared = launcher.install(version_id, download=download)

        if not java_path:
            if not args.dry_run:
                raise LauncherError("没有找到 Java，请用 --java 指定 java.exe 的路径。")
            java_path = "java"

        required = prepared.java_major_required
        if required and not args.dry_run:
            installed = java_major_version(java_path)
            if installed and installed < required:
                launcher.log(f"警告：该版本建议 Java {required}+，当前 {installed}，可能无法启动")

        command = launcher.build_command(
            prepared,
            username=username,
            java_path=java_path,
            memory_mb=memory_mb,
        )

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
