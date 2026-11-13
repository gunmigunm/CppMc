"""Tkinter window for CppMc (standard library only).

The window covers: version picker, offline name / Microsoft account, memory,
Java path (auto detect + auto download), game directory and the log view.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import auth
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
from .java import resolve_java

BG = "#12141b"
PANEL = "#1b1e28"
FG = "#e8eaf0"
MUTED = "#9aa3b2"
ACCENT = "#3ecf8e"


class LauncherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self.process = None
        self.account = auth.load_account()
        self.config = load_config()

        root.title(f"{APP_BRAND} — Minecraft Launcher (Python)")
        root.geometry("860x680")
        root.minsize(740, 580)
        root.configure(bg=BG)

        self._build_styles()
        self._build_widgets()
        self._load_initial_values()

        threading.Thread(target=self._bootstrap, daemon=True).start()
        root.after(80, self._drain_queue)

    # ------------------------------------------------------------------ style
    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        for candidate in ("clam", "vista", "xpnative"):
            if candidate in style.theme_names():
                style.theme_use(candidate)
                break
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI Semibold", 18))
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 12), padding=10)
        style.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG,
                        bordercolor="#2b3040")
        style.configure("TCombobox", fieldbackground=PANEL, foreground=FG)
        style.configure("TCheckbutton", background=PANEL, foreground=FG, font=("Segoe UI", 9))
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL,
                        bordercolor=PANEL, lightcolor=ACCENT)
        style.configure("TSpinbox", fieldbackground=PANEL, foreground=FG)

    # ---------------------------------------------------------------- widgets
    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=APP_BRAND, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{APP_VERSION}   ·   纯 Python + Tkinter   ·   离线 / 正版双模式",
                  style="Muted.TLabel").pack(side="left", padx=12, pady=(8, 0))

        form = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        form.pack(fill="x", pady=(14, 8))
        for column in (1, 3):
            form.columnconfigure(column, weight=1)

        ttk.Label(form, text="游戏版本", background=PANEL).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.version_var = tk.StringVar()
        self.version_box = ttk.Combobox(form, textvariable=self.version_var, values=[],
                                        state="readonly", width=26)
        self.version_box.grid(row=0, column=1, sticky="ew", pady=4)
        self.refresh_button = ttk.Button(form, text="刷新版本列表", command=self.refresh_versions)
        self.refresh_button.grid(row=0, column=2, sticky="e", padx=(8, 0), pady=4)

        ttk.Label(form, text="玩家名字", background=PANEL).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(form, textvariable=self.name_var)
        self.name_entry.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(form, text="内存 (MB)", background=PANEL).grid(row=1, column=2, sticky="e", padx=(8, 8), pady=4)
        self.memory_var = tk.StringVar()
        ttk.Spinbox(form, from_=512, to=16384, increment=256, width=8,
                    textvariable=self.memory_var).grid(row=1, column=3, sticky="w", pady=4)

        ttk.Label(form, text="Java 路径", background=PANEL).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        self.java_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.java_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Button(form, text="自动检测", command=self.detect_java).grid(row=2, column=3, sticky="w", pady=4)
        self.auto_java_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="找不到合适 Java 时自动下载（Adoptium Temurin，装到 %APPDATA%\\CppMc\\java）",
                        variable=self.auto_java_var).grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 2))

        ttk.Label(form, text="游戏目录", background=PANEL).grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        self.dir_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.dir_var).grid(row=4, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Button(form, text="选择…", command=self.choose_dir).grid(row=4, column=3, sticky="w", pady=4)

        account_box = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        account_box.pack(fill="x", pady=(0, 8))
        account_box.columnconfigure(1, weight=1)
        ttk.Label(account_box, text="账号", background=PANEL).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.account_var = tk.StringVar()
        ttk.Label(account_box, textvariable=self.account_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Button(account_box, text="微软登录", command=self.on_login).grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Button(account_box, text="退出登录", command=self.on_logout).grid(row=0, column=3, sticky="e", padx=(6, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 8))
        self.play_button = ttk.Button(actions, text="启动游戏", style="Accent.TButton",
                                      command=lambda: self.start(launch=True))
        self.play_button.pack(side="left")
        self.install_button = ttk.Button(actions, text="仅下载", command=lambda: self.start(launch=False))
        self.install_button.pack(side="left", padx=8)
        ttk.Button(actions, text="打开游戏目录", command=self.open_game_dir).pack(side="right")

        status = ttk.Frame(outer)
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 10))

        log_frame = ttk.Frame(outer, style="Panel.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                                wrap="word", font=("Consolas", 9), padx=10, pady=8)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(state="disabled")

    def _load_initial_values(self) -> None:
        self.name_var.set(str(self.config.get("username") or "Player"))
        self.memory_var.set(str(self.config.get("memory_mb") or 2048))
        self.dir_var.set(str(self.config.get("game_dir") or DEFAULT_GAME_DIR))
        java_path = str(self.config.get("java_path") or "")
        detected = find_java(java_path)
        self.java_var.set(detected or java_path)
        if not detected:
            self.append_log("没有自动找到 Java，启动时会按需自动下载。")
        self._refresh_account_label()

    # ---------------------------------------------------------------- account
    def _refresh_account_label(self) -> None:
        if self.account:
            self.account_var.set(f"正版账号：{self.account.get('name')}（{self.account.get('uuid')}）")
            self.name_entry.configure(state="disabled")
        else:
            self.account_var.set("离线模式（没有登录微软账号）")
            self.name_entry.configure(state="normal")

    def on_login(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_BRAND, "先等当前任务结束再登录。")
            return
        self.append_log("正在向微软申请设备码…")

        def worker() -> None:
            try:
                account = auth.login(
                    on_code=lambda code, url: self.queue.put(("code", code, url)),
                    log=self._thread_log,
                )
                self.queue.put(("account", account))
            except LauncherError as exc:
                self.queue.put(("error", str(exc)))
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("error", f"登录出错：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _show_code_dialog(self, code: str, url: str) -> None:
        top = tk.Toplevel(self.root)
        top.title("微软账号登录")
        top.configure(bg=BG)
        top.geometry("520x260")
        top.transient(self.root)
        ttk.Label(top, text="用浏览器打开下面的地址，然后输入这个代码：", style="Muted.TLabel").pack(
            anchor="w", padx=16, pady=(16, 8)
        )
        ttk.Label(top, text=url, foreground=ACCENT, background=BG, font=("Consolas", 11)).pack(
            anchor="w", padx=16
        )
        code_label = tk.Label(top, text=code, bg=PANEL, fg=FG, font=("Consolas", 22, "bold"),
                              padx=16, pady=10)
        code_label.pack(pady=14)
        row = ttk.Frame(top)
        row.pack(pady=(0, 12))

        def copy() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
            self.append_log("设备码已复制到剪贴板。")

        ttk.Button(row, text="复制代码", command=copy).pack(side="left", padx=6)
        ttk.Button(row, text="知道了", command=top.destroy).pack(side="left", padx=6)
        ttk.Label(top, text="登录成功后这个窗口会自动更新，不用手动关。", style="Muted.TLabel").pack()

    def on_logout(self) -> None:
        auth.clear_account()
        self.account = None
        self._refresh_account_label()
        self.append_log("已退出登录，之后按离线模式启动。")

    # ----------------------------------------------------------------- helpers
    def _game_dir(self) -> str:
        return self.dir_var.get().strip() or str(DEFAULT_GAME_DIR)

    # ----------------------------------------------------------------- actions
    def _bootstrap(self) -> None:
        self.queue.put(("status", "正在获取版本列表…"))
        try:
            launcher = Launcher(self._game_dir(), log=self._thread_log)
            entries = launcher.version_entries(refresh=True)
            ids = [e["id"] for e in entries if e.get("type") == "release"]
            ids += [e["id"] for e in entries if e.get("type") != "release"]
            self.queue.put(("versions", ids))
            latest = launcher.manifest().get("latest", {}).get("release")
            saved = str(self.config.get("version") or "")
            self.queue.put(("select", saved if saved in ids else (latest or (ids[0] if ids else ""))))
            self.queue.put(("status", "就绪"))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"获取版本列表失败：{exc}"))
            self.queue.put(("status", "版本列表获取失败"))

    def refresh_versions(self) -> None:
        if self.busy:
            return
        self.refresh_button.config(state="disabled")
        self.queue.put(("status", "正在刷新版本列表…"))

        def worker() -> None:
            try:
                launcher = Launcher(self._game_dir(), log=self._thread_log)
                entries = launcher.version_entries(refresh=True)
                ids = [e["id"] for e in entries if e.get("type") == "release"]
                ids += [e["id"] for e in entries if e.get("type") != "release"]
                self.queue.put(("versions", ids))
                self.queue.put(("status", f"版本列表已刷新，共 {len(ids)} 个"))
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("error", f"刷新失败：{exc}"))
            finally:
                self.queue.put(("refresh_done", None))

        threading.Thread(target=worker, daemon=True).start()

    def detect_java(self) -> None:
        path = find_java(self.java_var.get().strip())
        if not path:
            messagebox.showwarning(APP_BRAND, "没有找到 Java。可以留空，启动时会自动下载。")
            return
        self.java_var.set(path)
        major = java_major_version(path)
        self.append_log(f"已检测到 Java：{path}{f'（Java {major}）' if major else ''}")

    def choose_dir(self) -> None:
        folder = filedialog.askdirectory(initialdir=self._game_dir())
        if folder:
            self.dir_var.set(folder)

    def open_game_dir(self) -> None:
        import os
        import subprocess
        import sys

        path = Path(self._game_dir())
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(APP_BRAND, f"无法打开目录：{exc}")

    def start(self, launch: bool) -> None:
        if self.busy:
            messagebox.showinfo(APP_BRAND, "已经在运行了，先等这次结束吧。")
            return
        version_id = self.version_var.get().strip()
        if not version_id:
            messagebox.showwarning(APP_BRAND, "先选一个游戏版本。")
            return
        username = self.name_var.get().strip() or "Player"
        try:
            memory_mb = int(self.memory_var.get())
        except ValueError:
            memory_mb = 2048
        java_path = self.java_var.get().strip()
        auto_java = bool(self.auto_java_var.get())

        self.busy = True
        self.play_button.config(state="disabled")
        self.install_button.config(state="disabled")
        self.save_settings(version_id, username, memory_mb)

        threading.Thread(target=self._worker,
                         args=(version_id, username, memory_mb, java_path, launch, auto_java),
                         daemon=True).start()

    def save_settings(self, version_id: str, username: str, memory_mb: int) -> None:
        self.config.update({
            "version": version_id,
            "username": username,
            "memory_mb": memory_mb,
            "java_path": self.java_var.get().strip(),
            "game_dir": self._game_dir(),
        })
        save_config(self.config)

    # ------------------------------------------------------------------ worker
    def _worker(self, version_id: str, username: str, memory_mb: int, java_path: str,
                launch: bool, auto_java: bool) -> None:
        try:
            launcher = Launcher(self._game_dir(), log=self._thread_log, progress=self._thread_progress)
            self.queue.put(("status", f"准备 {version_id} …"))
            prepared = launcher.install(version_id, download=True)

            account = self.account if self.account else auth.ensure_account(log=self._thread_log)
            if account:
                username = account["name"]
                self.queue.put(("account", account))
            else:
                self._thread_log(f"离线模式启动，玩家名：{username}")

            resolved, major = resolve_java(java_path, prepared.java_major_required,
                                           self._thread_log, self._thread_progress,
                                           allow_download=auto_java)
            if not resolved:
                raise LauncherError("没有找到 Java，也无法自动下载。填一个 java.exe 路径，或勾上自动下载。")
            self.queue.put(("java", resolved))
            self._thread_log(f"使用 Java {major or '?'}：{resolved}")

            command = launcher.build_command(prepared, username=username, java_path=resolved,
                                             memory_mb=memory_mb)
            if account:
                command = auth.apply_account(command, account)

            if not launch:
                self.queue.put(("status", f"{version_id} 下载完成（未启动）"))
                self.queue.put(("progress", "完成", 100, 100))
                return

            self.queue.put(("status", "正在启动游戏…"))
            process = launcher.launch(command, on_line=self._thread_log)
            self.process = process
            code = process.wait()
            self.queue.put(("status", f"游戏已退出（退出码 {code}）"))
            if code != 0:
                self.queue.put(("error", f"游戏进程异常退出，退出码 {code}。请查看上面的日志。"))
        except LauncherError as exc:
            self.queue.put(("error", str(exc)))
            self.queue.put(("status", "出错了"))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"意外错误：{exc}"))
            self.queue.put(("status", "出错了"))
        finally:
            self.queue.put(("busy", False))

    # ------------------------------------------------------------ queue plumbing
    def _thread_log(self, message: str) -> None:
        self.queue.put(("log", message))

    def _thread_progress(self, label: str, current: int, total: int) -> None:
        self.queue.put(("progress", label, current, total))

    def _drain_queue(self) -> None:
        try:
            while True:
                message = self.queue.get_nowait()
                kind = message[0]
                if kind == "log":
                    self.append_log(message[1])
                elif kind == "progress":
                    _, label, current, total = message
                    percent = (current / total * 100) if total else 0
                    self.progress.config(value=percent)
                    self.status_var.set(f"{label} {current}/{total}（{percent:.0f}%）")
                elif kind == "status":
                    self.status_var.set(message[1])
                elif kind == "versions":
                    self.version_box.config(values=message[1])
                elif kind == "select":
                    if message[1]:
                        self.version_var.set(message[1])
                elif kind == "code":
                    self._show_code_dialog(message[1], message[2])
                elif kind == "account":
                    self.account = message[1]
                    self._refresh_account_label()
                elif kind == "java":
                    self.java_var.set(message[1])
                elif kind == "refresh_done":
                    self.refresh_button.config(state="normal")
                elif kind == "busy":
                    self.busy = bool(message[1])
                    state = "disabled" if self.busy else "normal"
                    self.play_button.config(state=state)
                    self.install_button.config(state=state)
                    if not self.busy:
                        self.progress.config(value=0)
                elif kind == "error":
                    self.append_log("错误：" + str(message[1]))
                    messagebox.showerror(APP_BRAND, str(message[1]))
        except queue.Empty:
            pass
        self.root.after(80, self._drain_queue)

    def append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(message) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def run_gui() -> int:
    root = tk.Tk()
    LauncherApp(root)
    root.mainloop()
    return 0
