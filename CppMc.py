#!/usr/bin/env python3
"""C++mc / CppMc launcher entry point.

Usage:
    python CppMc.py                 # 打开图形界面
    python CppMc.py --list          # 列出所有版本
    python CppMc.py --launch 1.21.5 --name Steve
"""

from cppmc.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
