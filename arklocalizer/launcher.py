from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .launcher_backend import (
    LauncherConfig,
    cli_command,
    command_environment,
    config_path,
    configured_game,
    infer_locale,
    launch_game,
    load_config,
    normalize_proxy,
    project_root,
    rebuild_commands,
    record_runtime,
    runtime_for,
    save_config,
    scan_configured_client,
    setup_commands,
)
from .runtime import install_runtime, uninstall_runtime
from .util import write_json


COLORS = {
    "bg": "#0A0D10",
    "panel": "#11171C",
    "panel_alt": "#171E24",
    "line": "#29333B",
    "text": "#F2EFE7",
    "muted": "#8B969E",
    "accent": "#F7A900",
    "accent_dark": "#B97800",
    "good": "#6FD59B",
    "danger": "#FF665C",
    "log": "#080A0C",
}


class LauncherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.project = project_root()
        self.config_data = load_config()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_process: subprocess.Popen[str] | None = None
        self.busy = False
        self.cancel_requested = False
        self.action_buttons: list[tk.Button] = []

        self.title("明日方舟离线汉化启动器")
        self.geometry("1080x820")
        self.minsize(920, 720)
        self.configure(bg=COLORS["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.game_var = tk.StringVar(value=self.config_data.game_executable)
        self.locale_var = tk.StringVar(value="美服 / EN" if self.config_data.locale == "en" else "日服 / JP")
        self.proxy_var = tk.StringVar(value=self.config_data.proxy)
        self.update_var = tk.BooleanVar(value=self.config_data.update_repositories)
        self.status_var = tk.StringVar(value="等待配置")
        self.detail_var = tk.StringVar(value="选择 Arknights.exe 后即可扫描")
        self.task_var = tk.StringVar(value="IDLE / 空闲")

        self._configure_styles()
        self._build_interface()
        self.after(100, self._poll_events)
        self.after(300, self._initial_scan)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Ark.Horizontal.TProgressbar",
            troughcolor=COLORS["panel_alt"],
            background=COLORS["accent"],
            bordercolor=COLORS["panel_alt"],
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
        )
        style.configure(
            "Ark.TCombobox",
            fieldbackground=COLORS["panel_alt"],
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["accent"],
            bordercolor=COLORS["line"],
            padding=8,
        )
        style.map(
            "Ark.TCombobox",
            fieldbackground=[("readonly", COLORS["panel_alt"])],
            foreground=[("readonly", COLORS["text"])],
            selectbackground=[("readonly", COLORS["panel_alt"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "Ark.TCheckbutton",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        style.map("Ark.TCheckbutton", foreground=[("active", COLORS["text"])])

    def _build_interface(self) -> None:
        tk.Frame(self, bg=COLORS["accent"], height=5).pack(fill="x")
        shell = tk.Frame(self, bg=COLORS["bg"], padx=28, pady=20)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=COLORS["bg"])
        header.pack(fill="x")
        brand = tk.Frame(header, bg=COLORS["bg"])
        brand.pack(side="left", fill="x", expand=True)
        tk.Label(
            brand,
            text="ARK // LOCALIZATION BAY",
            bg=COLORS["bg"],
            fg=COLORS["accent"],
            font=("Bahnschrift SemiBold", 10),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="明日方舟离线汉化启动器",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 23, "bold"),
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            brand,
            text="作者：bilibili 繁花掠影",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        status = tk.Frame(header, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"], padx=18, pady=12)
        status.pack(side="right", padx=(18, 0))
        self.status_label = tk.Label(
            status,
            textvariable=self.status_var,
            bg=COLORS["panel"],
            fg=COLORS["accent"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.status_label.pack(anchor="e")
        tk.Label(
            status,
            textvariable=self.detail_var,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="e", pady=(4, 0))

        config_panel = tk.Frame(
            shell,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            padx=20,
            pady=16,
        )
        config_panel.pack(fill="x")
        tk.Label(
            config_panel,
            text="运行环境",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))
        tk.Label(
            config_panel,
            text=f"配置文件：{config_path()}",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Cascadia Mono", 8),
        ).grid(row=0, column=1, columnspan=3, sticky="e", pady=(0, 12))

        self._field_label(config_panel, "游戏程序", 1)
        game_entry = self._entry(config_panel, self.game_var)
        game_entry.grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=5)
        browse = self._button(config_panel, "选择 Arknights.exe", self._browse_game, kind="secondary")
        browse.grid(row=1, column=2, sticky="ew", pady=5)

        self._field_label(config_panel, "服务器区域", 2)
        locale = ttk.Combobox(
            config_panel,
            textvariable=self.locale_var,
            values=("美服 / EN", "日服 / JP"),
            state="readonly",
            style="Ark.TCombobox",
            width=16,
        )
        locale.grid(row=2, column=1, sticky="w", padx=(12, 8), pady=5)

        self._field_label(config_panel, "下载代理", 3)
        proxy_entry = self._entry(config_panel, self.proxy_var)
        proxy_entry.grid(row=3, column=1, sticky="ew", padx=(12, 8), pady=5)
        tk.Label(
            config_panel,
            text="可选；仅用于组件、依赖和公开仓库更新",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).grid(row=3, column=2, columnspan=2, sticky="w", pady=5)

        update_check = ttk.Checkbutton(
            config_panel,
            text="重建前更新多服词表、FlatBuffers Schema 与解析参考仓库",
            variable=self.update_var,
            style="Ark.TCheckbutton",
        )
        update_check.grid(row=4, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(9, 2))
        save = self._button(config_panel, "保存配置", self._save_from_form, kind="secondary")
        save.grid(row=4, column=3, sticky="e", padx=(8, 0), pady=(9, 2))
        config_panel.columnconfigure(1, weight=1)

        actions = tk.Frame(shell, bg=COLORS["bg"], pady=14)
        actions.pack(fill="x")
        for text, command, kind in (
            ("初始化构建环境", self._check_environment, "secondary"),
            ("扫描客户端", self._scan_client, "secondary"),
            ("更新词表并重建", self._rebuild, "secondary"),
            ("安装 / 修复并启动", self._install_and_launch, "primary"),
            ("仅启动游戏", self._launch_only, "secondary"),
            ("卸载汉化", self._uninstall, "danger"),
        ):
            button = self._button(actions, text, command, kind=kind)
            button.pack(side="left", padx=(0, 8))
            self.action_buttons.append(button)

        monitor = tk.Frame(shell, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        monitor.pack(fill="both", expand=True)
        monitor_header = tk.Frame(monitor, bg=COLORS["panel_alt"], padx=14, pady=9)
        monitor_header.pack(fill="x")
        tk.Label(
            monitor_header,
            text="执行记录",
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")
        tk.Label(
            monitor_header,
            textvariable=self.task_var,
            bg=COLORS["panel_alt"],
            fg=COLORS["accent"],
            font=("Cascadia Mono", 8, "bold"),
        ).pack(side="right")

        self.log_text = tk.Text(
            monitor,
            bg=COLORS["log"],
            fg="#CBD3D8",
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["accent_dark"],
            relief="flat",
            borderwidth=0,
            font=("Cascadia Mono", 9),
            padx=14,
            pady=12,
            wrap="word",
            height=10,
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("accent", foreground=COLORS["accent"])
        self.log_text.tag_configure("good", foreground=COLORS["good"])
        self.log_text.tag_configure("danger", foreground=COLORS["danger"])

        footer = tk.Frame(shell, bg=COLORS["bg"])
        footer.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", style="Ark.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True)
        self.cancel_button = self._button(footer, "取消当前任务", self._cancel_task, kind="danger")
        self.cancel_button.configure(state="disabled")
        self.cancel_button.pack(side="left", padx=(12, 0))
        open_output = self._button(footer, "打开产物目录", self._open_outputs, kind="secondary")
        open_output.pack(side="left", padx=(8, 0))

        self._append_log("启动器就绪。首次使用请先选择游戏安装目录下的 Arknights.exe。", "accent")
        self._append_log("本补丁安装一次后会再次启动游戏会自动持续加载，若无需补丁启动请及时卸载。")
        self._append_log("在安装完补丁后启动游戏后会有一个黑色窗口，请耐心等待不要主动关闭它，在准备就绪后游戏进程会自动启动。")

    def _field_label(self, parent: tk.Widget, text: str, row: int) -> None:
        tk.Label(
            parent,
            text=text,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).grid(row=row, column=0, sticky="w", pady=5)

    def _entry(self, parent: tk.Widget, variable: tk.StringVar) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            bg=COLORS["panel_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["accent"],
            disabledbackground=COLORS["panel_alt"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["accent"],
            font=("Microsoft YaHei UI", 9),
        )

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        *,
        kind: str,
    ) -> tk.Button:
        palette = {
            "primary": (COLORS["accent"], COLORS["bg"], "#FFC247"),
            "secondary": (COLORS["panel_alt"], COLORS["text"], "#24303A"),
            "danger": ("#2B1819", COLORS["danger"], "#3A2021"),
        }
        background, foreground, active = palette[kind]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=active,
            activeforeground=foreground,
            disabledforeground="#59636A",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold" if kind == "primary" else "normal"),
            padx=14,
            pady=8,
        )

    def _form_config(self) -> LauncherConfig:
        locale = "jp" if self.locale_var.get().startswith("日服") else "en"
        return LauncherConfig(
            game_executable=self.game_var.get().strip(),
            locale=locale,
            proxy=normalize_proxy(self.proxy_var.get()),
            update_repositories=self.update_var.get(),
            last_runtime=self.config_data.last_runtime if self.config_data.locale == locale else "",
        )

    def _save_from_form(self, *, show_message: bool = True) -> LauncherConfig | None:
        try:
            config = self._form_config()
            save_config(config)
            self.config_data = config
            if show_message:
                self._append_log(f"配置已保存：{config_path()}", "good")
            return config
        except Exception as error:
            messagebox.showerror("配置无效", str(error), parent=self)
            return None

    def _browse_game(self) -> None:
        current = Path(self.game_var.get()).expanduser() if self.game_var.get() else None
        initial = str(current.parent) if current and current.parent.is_dir() else str(Path.cwd())
        selected = filedialog.askopenfilename(
            parent=self,
            title="选择明日方舟客户端 Arknights.exe",
            initialdir=initial,
            filetypes=(("Arknights.exe", "Arknights.exe"), ("可执行文件", "*.exe")),
        )
        if not selected:
            return
        self.game_var.set(str(Path(selected).resolve()))
        if locale := infer_locale(Path(selected)):
            self.locale_var.set("日服 / JP" if locale == "jp" else "美服 / EN")
        self._save_from_form(show_message=False)
        self._scan_client()

    def _append_log(self, text: str, tag: str | None = None) -> None:
        self.log_text.configure(state="normal")
        stamp = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] ", "accent")
        self.log_text.insert("end", text.rstrip() + "\n", tag or "")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _queue_log(self, text: str, tag: str | None = None) -> None:
        self.events.put(("log", (text, tag)))

    def _initial_scan(self) -> None:
        if self.game_var.get() and Path(self.game_var.get()).is_file():
            self._scan_client()

    def _start_task(self, label: str, worker: Callable[[], Any]) -> None:
        if self.busy:
            messagebox.showinfo("任务正在运行", "请等待当前任务完成，或先取消。", parent=self)
            return
        self.busy = True
        self.cancel_requested = False
        self.task_var.set(f"RUNNING / {label}")
        self.progress.start(12)
        self.cancel_button.configure(state="normal")
        for button in self.action_buttons:
            button.configure(state="disabled")

        def target() -> None:
            try:
                result = worker()
            except Exception as error:
                self.events.put(("error", (label, str(error))))
            else:
                self.events.put(("done", (label, result)))

        threading.Thread(target=target, daemon=True, name=f"arklocalizer-{label}").start()

    def _finish_task(self) -> None:
        self.busy = False
        self.current_process = None
        self.progress.stop()
        self.cancel_button.configure(state="disabled")
        for button in self.action_buttons:
            button.configure(state="normal")

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self._append_log(*payload)
                elif event == "scan":
                    self._apply_scan(payload)
                elif event == "done":
                    label, result = payload
                    self._finish_task()
                    self.task_var.set("DONE / 已完成")
                    self._append_log(f"{label}完成。", "good")
                    if isinstance(result, dict) and "runtime" in result:
                        self._apply_scan(result)
                elif event == "error":
                    label, detail = payload
                    self._finish_task()
                    self.task_var.set("ERROR / 需要处理")
                    self._append_log(f"{label}失败：{detail}", "danger")
                    messagebox.showerror(f"{label}失败", detail, parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _run_command(self, label: str, command: list[str], config: LauncherConfig) -> None:
        if self.cancel_requested:
            raise RuntimeError("任务已取消")
        self._queue_log(f"▶ {label}", "accent")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            cwd=self.project,
            env=command_environment(config, self.project),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        self.current_process = process
        assert process.stdout is not None
        for line in process.stdout:
            if line.strip():
                self._queue_log(line.rstrip())
        code = process.wait()
        self.current_process = None
        if self.cancel_requested:
            raise RuntimeError("任务已取消")
        if code != 0:
            raise RuntimeError(f"{label}返回错误代码 {code}")

    def _check_environment(self) -> None:
        config = self._save_from_form(show_message=False)
        if config is None:
            return
        if not messagebox.askyesno(
            "初始化构建环境",
            "将创建/更新项目内 Python 环境，下载固定版本运行组件，并克隆或更新公开数据仓库。\n\n该操作需要网络、Git、Conda 和数 GB 可用空间。继续吗？",
            parent=self,
        ):
            return

        def worker() -> dict[str, Any] | None:
            for label, command in setup_commands(self.project, config):
                self._run_command(label, command, config)
            if config.game_executable:
                command = cli_command(self.project, "doctor", "--game-dir", str(configured_game(config)[1]), "--locale", config.locale)
                self._run_command("检查所选客户端", command, config)
                result = scan_configured_client(config)
                self.events.put(("scan", result))
                return result
            return None

        self._start_task("构建环境初始化", worker)

    def _scan_client(self) -> None:
        config = self._save_from_form(show_message=False)
        if config is None:
            return

        def worker() -> dict[str, Any]:
            result = scan_configured_client(config)
            runtime = result["runtime"]
            self._queue_log(
                f"客户端扫描：有效资源包 {result['effective_anon_bundles']}，"
                f"基础层 {result['base_layer']['files']}，热更新层 {result['hot_layer']['files']}。"
            )
            self._queue_log(
                f"运行时状态：{runtime['state']}；已验证 {runtime['verified']}，"
                f"修改 {runtime['modified']}，缺失 {runtime['missing']}，"
                f"运行期改写 {runtime.get('runtime_modified', 0)}。"
            )
            translation_pack = result.get("translation_pack", {})
            if translation_pack.get("update_available"):
                self._queue_log(
                    "检测到客户端已安装词表与当前默认运行时不一致；"
                    "建议点击“安装 / 修复并启动”来启动游戏，以获取更佳体验。",
                    "accent",
                )
            if result["running_processes"]:
                self._queue_log(
                    "游戏正在运行，安装和卸载已锁定："
                    + ", ".join(f"PID {item['pid']}" for item in result["running_processes"]),
                    "danger",
                )
            self.events.put(("scan", result))
            return result

        self._start_task("客户端扫描", worker)

    def _rebuild(self) -> None:
        config = self._save_from_form(show_message=False)
        if config is None:
            return
        try:
            configured_game(config)
            scan = scan_configured_client(config)
        except Exception as error:
            messagebox.showerror("客户端未就绪", str(error), parent=self)
            return
        if scan["running_processes"]:
            messagebox.showerror("请先关闭游戏", "提取和重建前必须完全关闭 Arknights.exe。", parent=self)
            return
        if not messagebox.askyesno(
            "更新词表并重建",
            "将从当前客户端提取数据表和剧情，重新对齐中文公开数据，生成并严格校验完整词表。\n\n通常需要数分钟并占用额外磁盘空间。继续吗？",
            parent=self,
        ):
            return

        def worker() -> dict[str, Any]:
            initialized_now = False
            if not (self.project / ".conda-env" / "python.exe").is_file():
                for label, command in setup_commands(self.project, config):
                    self._run_command(label, command, config)
                initialized_now = True
            runtime, commands = rebuild_commands(
                self.project,
                config,
                # setup_commands already cloned/updated every repository.
                include_repository_update=config.update_repositories and not initialized_now,
            )
            for label, command in commands:
                self._run_command(label, command, config)
            record_runtime(self.project, config, runtime)
            self.config_data = config
            self._queue_log(f"新词表和运行时已生成：{runtime}", "good")
            result = scan_configured_client(config)
            self.events.put(("scan", result))
            return result

        self._start_task("词表更新与重建", worker)

    def _install_and_launch(self) -> None:
        config = self._save_from_form(show_message=False)
        if config is None:
            return
        try:
            scan = scan_configured_client(config)
            stage = runtime_for(self.project, config)
        except Exception as error:
            messagebox.showerror("无法安装", str(error), parent=self)
            return
        if scan["running_processes"]:
            messagebox.showerror("请先关闭游戏", "安装/修复前必须完全关闭 Arknights.exe。", parent=self)
            return
        if scan["runtime"]["state"] == "orphaned":
            messagebox.showinfo(
                "请先清理旧版残留",
                "检测到旧版卸载留下的 BepInEx 运行期文件。请先点击“卸载汉化”清理，再重新安装。",
                parent=self,
            )
            return
        if scan["runtime"]["state"] == "foreign" and not messagebox.askyesno(
            "检测到外部注入",
            "客户端中存在非本工具管理的注入文件。继续可能覆盖其中一部分。\n\n建议先彻底还原原版客户端。仍要继续吗？",
            parent=self,
        ):
            return
        if not messagebox.askyesno(
            "安装并启动",
            f"将安装/修复 {config.locale.upper()} 离线汉化并启动游戏。\n\n运行时：{stage}\n\n继续吗？",
            parent=self,
        ):
            return

        def worker() -> dict[str, Any]:
            report = config_path().parent / "reports" / f"{config.locale}-gui-install-{time.strftime('%Y%m%d-%H%M%S')}.json"
            _, game_dir = configured_game(config)
            result = install_runtime(stage, game_dir, apply=True)
            write_json(report, result)
            self._queue_log(
                f"持久化安装完成：新建 {result['create']}，替换 {result['replace']}，"
                f"复用 {result['unchanged']}；报告 {report}",
                "good",
            )
            process = launch_game(config)
            self._queue_log(f"游戏已启动，PID {process.pid}。以后直接运行 Arknights.exe 也会加载汉化。", "good")
            result = scan_configured_client(config)
            self.events.put(("scan", result))
            return result

        self._start_task("安装并启动", worker)

    def _launch_only(self) -> None:
        config = self._save_from_form(show_message=False)
        if config is None:
            return
        try:
            scan = scan_configured_client(config)
        except Exception as error:
            messagebox.showerror("无法启动", str(error), parent=self)
            return
        if scan["running_processes"]:
            messagebox.showinfo("游戏已在运行", "检测到 Arknights.exe 已启动，无需重复启动。", parent=self)
            return
        if scan["runtime"]["state"] not in {"installed", "needs_repair"} and not messagebox.askyesno(
            "汉化尚未安装",
            "当前客户端没有本工具的安装清单，直接启动很可能不会加载中文。仍要启动吗？",
            parent=self,
        ):
            return
        try:
            process = launch_game(config)
            self._append_log(f"游戏已启动，PID {process.pid}。", "good")
        except Exception as error:
            messagebox.showerror("启动失败", str(error), parent=self)

    def _uninstall(self) -> None:
        config = self._save_from_form(show_message=False)
        if config is None:
            return
        try:
            scan = scan_configured_client(config)
        except Exception as error:
            messagebox.showerror("无法卸载", str(error), parent=self)
            return
        if scan["running_processes"]:
            messagebox.showerror("请先关闭游戏", "卸载前必须完全关闭 Arknights.exe。", parent=self)
            return
        if scan["runtime"]["state"] not in {"installed", "needs_repair", "orphaned"}:
            messagebox.showinfo("无需卸载", "没有找到本工具的安装清单。", parent=self)
            return
        legacy_residue = scan["runtime"]["state"] == "orphaned"
        if not messagebox.askyesno(
            "卸载并还原",
            (
                "检测到旧版卸载遗留的 BepInEx 运行期缓存、日志和互操作文件，"
                "将只清理可确认为本工具生成的残留。\n\n继续吗？"
                if legacy_residue
                else "将按安装清单恢复被替换文件，并移除本工具文件及其运行期产物。\n\n继续吗？"
            ),
            parent=self,
        ):
            return

        def worker() -> dict[str, Any]:
            report = config_path().parent / "reports" / f"{config.locale}-gui-uninstall-{time.strftime('%Y%m%d-%H%M%S')}.json"
            _, game_dir = configured_game(config)
            uninstall_result = uninstall_runtime(game_dir, apply=True)
            write_json(report, uninstall_result)
            counts: dict[str, int] = {}
            for action in uninstall_result["actions"]:
                key = action["action"]
                counts[key] = counts.get(key, 0) + 1
            self._queue_log(
                "卸载完成：" + "，".join(f"{key} {value}" for key, value in sorted(counts.items()))
                + f"；报告 {report}",
                "good",
            )
            result = scan_configured_client(config)
            self.events.put(("scan", result))
            return result

        self._start_task("卸载汉化", worker)

    def _apply_scan(self, result: dict[str, Any]) -> None:
        runtime = result["runtime"]
        mapping = {
            "clean": ("原版客户端 / 待安装", COLORS["accent"], "未检测到注入文件"),
            "foreign": ("检测到外部注入", COLORS["danger"], "建议先恢复原版客户端"),
            "orphaned": ("检测到旧版卸载残留", COLORS["danger"], "可点击“卸载汉化”彻底清理"),
            "installed": (
                "离线汉化已安装",
                COLORS["good"],
                f"已验证 {runtime['verified']} 个文件"
                + (
                    f" · 运行期改写 {runtime.get('runtime_modified', 0)}"
                    if runtime.get("runtime_modified", 0)
                    else ""
                ),
            ),
            "needs_repair": (
                "汉化需要修复",
                COLORS["danger"],
                f"修改 {runtime['modified']} / 缺失 {runtime['missing']}",
            ),
        }
        title, color, detail = mapping.get(runtime["state"], (runtime["state"], COLORS["muted"], ""))
        translation_pack = result.get("translation_pack", {})
        if translation_pack.get("update_available"):
            update_detail = (
                "客户端词表与当前默认运行时不一致\n"
                "建议点击“安装 / 修复并启动”来启动游戏并获取更佳体验"
            )
            if runtime["state"] == "installed":
                title = "词表版本待更新"
                color = COLORS["accent"]
                detail = update_detail
            else:
                detail += "\n" + update_detail
        if result.get("running_processes"):
            detail += " · 游戏运行中"
        self.status_var.set(title)
        self.detail_var.set(detail)
        self.status_label.configure(fg=color)

    def _cancel_task(self) -> None:
        if not self.busy:
            return
        self.cancel_requested = True
        process = self.current_process
        if process is not None and process.poll() is None:
            process.terminate()
        self._append_log("正在取消当前步骤……", "danger")

    def _open_outputs(self) -> None:
        destination = self.project / "outputs"
        destination.mkdir(parents=True, exist_ok=True)
        os.startfile(destination)

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
            "任务仍在运行",
            "关闭启动器会终止当前子进程。确定关闭吗？",
            parent=self,
        ):
            return
        if self.current_process is not None and self.current_process.poll() is None:
            self.current_process.terminate()
        self.destroy()


def main() -> int:
    app = LauncherApp()
    app.mainloop()
    return 0
