"""Tkinter GUI for CppMc — only the standard library, no third party packages."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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
        self.config = load_config()

        root.title(f"{APP_BRAND} — Minecraft Launcher (Python)")
        root.geometry("820x620")
        root.minsize(720, 540)
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
        style.configure(
            "TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, bordercolor="#2b3040"
        )
        style.configure("TCombobox", fieldbackground=PANEL, foreground=FG)
        style.configure(
            "Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=PANEL,
            bordercolor=PANEL,
            lightcolor=ACCENT,
        )
        style.configure("TSpinbox", fieldbackground=PANEL, foreground=FG)

    # ---------------------------------------------------------------- widgets
    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=APP_BRAND, style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text=f"v{APP_VERSION}   ·   纯 Python + Tkinter   ·   离线模式启动器",
            style="Muted.TLabel",
        ).pack(side="left", padx=12, pady=(8, 0))

        form = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        form.pack(fill="x", pady=(14, 10))
        for column in (1, 3):
            form.columnconfigure(column, weight=1)

        # row 0 : version + refresh
        ttk.Label(form, text="游戏版本", background=PANEL).grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.version_var = tk.StringVar()
        self.version_box = ttk.Combobox(
            form, textvariable=self.version_var, values=[], state="readonly", width=26
        )
        self.version_box.grid(row=0, column=1, sticky="ew", pady=4)
        self.refresh_button = ttk.Button(form, text="刷新版本列表", command=self.refresh_versions)
        self.refresh_button.grid(row=0, column=2, sticky="e", padx=(8, 0), pady=4)

        # row 1 : username + memory
        ttk.Label(form, text="玩家名字", background=PANEL).grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(form, text="内存 (MB)", background=PANEL).grid(
            row=1, column=2, sticky="e", padx=(8, 8), pady=4
        )
        self.memory_var = tk.StringVar()
        ttk.Spinbox(
            form, from_=512, to=16384, increment=256, width=8, textvariable=self.memory_var
        ).grid(row=1, column=3, sticky="w", pady=4)

        # row 2 : java path
        ttk.Label(form, text="Java 路径", background=PANEL).grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.java_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.java_var).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Button(form, text="自动检测", command=self.detect_java).grid(
            row=2, column=3, sticky="w", pady=4
        )

        # row 3 : game directory
        ttk.Label(form, text="游戏目录", background=PANEL).grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.dir_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.dir_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Button(form, text="选择…", command=self.choose_dir).grid(
            row=3, column=3, sticky="w", pady=4
        )

        # buttons
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 8))
        self.play_button = ttk.Button(
            actions, text="启动游戏", style="Accent.TButton", command=lambda: self.start(launch=True)
        )
        self.play_button.pack(side="left")
        self.install_button = ttk.Button(
            actions, text="仅下载", command=lambda: self.start(launch=False)
        )
        self.install_button.pack(side="left", padx=8)
        self.open_dir_button = ttk.Button(actions, text="打开游戏目录", command=self.open_game_dir)
        self.open_dir_button.pack(side="right")

        status = ttk.Frame(outer)
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(4, 10))

        log_frame = ttk.Frame(outer, style="Panel.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame,
            bg=PANEL,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            wrap="word",
            font=("Consolas", 9),
            padx=10,
            pady=8,
        )
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
            self.append_log("没有自动找到 Java，请设置 JAVA_HOME 或手动填写 java.exe 路径。")

    # ----------------------------------------------------------------- helpers
    def _game_dir(self) -> str:
        return self.dir_var.get().strip() or str(DEFAULT_GAME_DIR)

    # ----------------------------------------------------------------- actions
    def _bootstrap(self) -> None:
        self.queue.put(("status", "正在获取版本列表…"))
        try:
            launcher = Launcher(self._game_dir(), log=self._thread_log, progress=self._thread_progress)
            entries = launcher.version_entries(refresh=True)
            ids = [entry["id"] for entry in entries if entry.get("type") == "release"]
            ids += [entry["id"] for entry in entries if entry.get("type") != "release"]
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
                launcher = Launcher(self._game_dir(), log=self._thread_log, progress=self._thread_progress)
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
            messagebox.showwarning(APP_BRAND, "没有找到 Java。请安装 JDK/JRE 或手动填写 java.exe 路径。")
            return
        self.java_var.set(path)
        major = java_major_version(path)
        suffix = f"（Java {major}）" if major else ""
        self.append_log(f"已检测到 Java：{path}{suffix}")

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

        self.busy = True
        self.play_button.config(state="disabled")
        self.install_button.config(state="disabled")
        self.save_settings(version_id, username, memory_mb)

        threading.Thread(
            target=self._worker,
            args=(version_id, username, memory_mb, java_path, launch),
            daemon=True,
        ).start()

    def save_settings(self, version_id: str, username: str, memory_mb: int) -> None:
        self.config.update(
            {
                "version": version_id,
                "username": username,
                "memory_mb": memory_mb,
                "java_path": self.java_var.get().strip(),
                "game_dir": self._game_dir(),
            }
        )
        save_config(self.config)

    # ------------------------------------------------------------------ worker
    def _worker(self, version_id: str, username: str, memory_mb: int, java_path: str, launch: bool) -> None:
        try:
            launcher = Launcher(self._game_dir(), log=self._thread_log, progress=self._thread_progress)
            self.queue.put(("status", f"准备 {version_id} …"))
            prepared = launcher.install(version_id, download=True)

            if not java_path:
                java_path = find_java() or ""
            if not java_path:
                raise LauncherError("没有找到 Java，请安装 JDK/JRE 或在界面上填写 java.exe 路径。")

            required = prepared.java_major_required
            installed = java_major_version(java_path)
            if required and installed and installed < required:
                self._thread_log(f"警告：该版本需要 Java {required}+，当前是 Java {installed}，可能会崩溃。")

            command = launcher.build_command(
                prepared,
                username=username,
                java_path=java_path,
                memory_mb=memory_mb,
            )

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
