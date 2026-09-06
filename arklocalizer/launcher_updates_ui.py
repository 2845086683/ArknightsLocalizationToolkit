from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .libraries import discover, import_library, library_root
from .updates import GitHub, check_updates, normalize_mirror, prepare_software_update, schedule_software_install, update_library

PROXY_MODES = {"auto": "自动：代理不可用时重试直连", "direct": "不使用代理", "custom": "仅使用填写的代理"}

class LibraryUpdatesUI:
    def _refresh_libraries(self) -> None:
        locale = "jp" if self.locale_var.get().startswith("日服") else "en"
        try: self.libraries = discover(self.project, locale)
        except Exception as error:
            self.libraries = []
            self.library_combo.configure(values=[])
            self.library_var.set("词库读取失败，请检查执行记录")
            self.library_detail.set(str(error))
            self._append_log(str(error), "danger")
            return
        selected = self.config_data.selected_libraries.get(locale)
        library = next((item for item in self.libraries if item.id == selected), None)
        if library is None and not selected and self.libraries: library = self.libraries[0]
        self.library_combo.configure(values=[item.label for item in self.libraries])
        self.library_var.set(library.label if library else "请选择词库" if self.libraries else "此区服暂无词库，请导入")
        self.library_detail.set(f"ID：{library.id}  ·  Git：{library.commit[:10] or '本地版本'}" if library else "选择仅显示当前区服，词库 ID 不随文件夹改名而改变")

    def _selected_library(self):
        return next((item for item in self.libraries if item.label == self.library_var.get()), None)

    def _library_changed(self, _event=None):
        self._save_from_form(show_message=False)
        self._refresh_libraries()

    def _region_changed(self, _event=None):
        self._refresh_libraries()
        self._save_from_form(show_message=False)

    def _import_library(self):
        selected = filedialog.askdirectory(parent=self, title="选择包含 ARKLOCALIZER_MANIFEST.json 的词库目录")
        if not selected: return
        def worker():
            item = import_library(self.project, Path(selected))
            self.events.put(("library_imported", item))
        self._start_task("导入并校验词库", worker)

    def _check_online(self, *, force=False):
        if self.checking_updates: return
        try: config = self._form_config()
        except Exception as error:
            self.online_status.set("更新配置无效，请打开更新与设置")
            self._append_log(str(error), "danger")
            return
        if not force and not (config.check_library_updates or config.check_software_updates):
            self.online_status.set("启动检查已关闭，可手动检查")
            return
        self.checking_updates = True
        self.online_status.set("正在检查在线更新…")
        def worker():
            try: result = check_updates(self.project, config, force=force,
                                        client=GitHub(config, log=self._queue_log))
            except Exception as error: result = {"libraries": [], "software": None, "errors": [str(error)]}
            self.events.put(("online_checked", result))
        threading.Thread(target=worker, daemon=True, name="ark-update-check").start()

    def _handle_online_event(self, event, payload):
        if event == "online_checked":
            self.checking_updates = False
            self.online_result = payload
            count = sum(item["available"] for item in payload["libraries"])
            software = payload.get("software")
            labels = []
            if count: labels.append(f"{count} 个词库与 Git 不同")
            if software and software["available"]: labels.append(f"软件 {software['version']} 可更新")
            self.online_status.set(" · ".join(labels) if labels else "检查未完成，可在更新设置中重试" if payload["errors"] else "已检查，当前无更新")
            for error in payload["errors"]: self._append_log(error)
            self._refresh_update_details()
        elif event == "library_imported":
            self.config_data.selected_libraries[payload.locale] = payload.id
            self.locale_var.set("日服 / JP" if payload.locale == "jp" else "美服 / EN")
            self._refresh_libraries()
            self._save_from_form(show_message=False)
        elif event == "libraries_changed":
            if payload:
                for item in self.online_result.get("libraries", []):
                    if item['id'] == payload: item['available'] = False
            self._refresh_libraries()
        elif event == "software_ready":
            candidate = payload
            try:
                plan = schedule_software_install(self.project, candidate)
            except RuntimeError as error:
                self._append_log(str(error), "good")
                messagebox.showinfo("更新包已就绪", str(error), parent=self)
            except Exception as error:
                self._append_log(f"软件更新未安装：{error}", "danger")
                messagebox.showerror("软件更新未安装", str(error), parent=self)
            else:
                self._append_log(f"将在关闭后更新完整软件与官方词库，自建词库保留。回滚及日志：{plan.parent}", "good")
                self.after(200, self.destroy)
        else:
            return False
        return True

    def _update_dialog(self):
        if self.update_window is not None and self.update_window.winfo_exists():
            self.update_window.lift()
            return
        from .launcher import COLORS
        window = self.update_window = tk.Toplevel(self)
        window.title("词库与软件更新")
        window.geometry("720x700")
        window.minsize(650, 650)
        window.configure(bg=COLORS["panel"])
        window.transient(self)
        panel = tk.Frame(window, bg=COLORS["panel"], padx=24, pady=18)
        panel.pack(fill="both", expand=True)
        def label(text, **kwargs):
            item = tk.Label(panel, text=text, bg=COLORS["panel"], fg=COLORS["text"], anchor="w", justify="left", **kwargs)
            item.pack(fill="x", pady=(0, 8))
            return item
        label(f"词库与软件更新 当前版本 {__version__}", font=("Microsoft YaHei UI", 12, "bold"))
        self.check_words_var = tk.BooleanVar(value=self.config_data.check_library_updates)
        self.check_app_var = tk.BooleanVar(value=self.config_data.check_software_updates)
        for text, variable in (("每次启动检查词库", self.check_words_var), ("每次启动检查软件", self.check_app_var)):
            ttk.Checkbutton(panel, text=text, variable=variable, style="Ark.TCheckbutton").pack(anchor="w", pady=3)
        label("更新来源", font=("Microsoft YaHei UI", 10, "bold"))
        self.source_var = tk.StringVar(value="GitHub 镜像" if self.config_data.update_source == "mirror" else "GitHub 直连")
        ttk.Combobox(panel, textvariable=self.source_var, values=("GitHub 直连", "GitHub 镜像"), state="readonly", style="Ark.TCombobox").pack(fill="x")
        self.mirror_var = tk.StringVar(value=self.config_data.mirror_url)
        label("镜像前缀（选择镜像时生效）")
        self._entry(panel, self.mirror_var).pack(fill="x", pady=(0, 8))
        connection = tk.Frame(panel, bg=COLORS["panel"])
        connection.pack(fill="x", pady=(2, 8))
        tk.Label(connection, text="更新连接", bg=COLORS["panel"], fg=COLORS["text"]).pack(side="left", padx=(0, 12))
        self.proxy_mode_var = tk.StringVar(value=PROXY_MODES[self.config_data.update_proxy_mode])
        ttk.Combobox(connection, textvariable=self.proxy_mode_var, values=list(PROXY_MODES.values()),
                     state="readonly", style="Ark.TCombobox").pack(side="left", fill="x", expand=True)
        label("HTTP / HTTPS 代理地址（自动模式留空使用系统代理）")
        self._entry(panel, self.proxy_var).pack(fill="x", pady=(0, 8))
        row = tk.Frame(panel, bg=COLORS["panel"])
        row.pack(fill="x", pady=6)
        self._button(row, "保存设置", self._save_update_settings, kind="secondary").pack(side="left")
        self._button(row, "立即检查", lambda: self._save_update_settings(check=True), kind="primary").pack(side="left", padx=8)
        tk.Label(panel, textvariable=self.online_status, bg=COLORS["panel"], fg=COLORS["accent"], anchor="w", wraplength=650).pack(fill="x", pady=10)
        self.update_details = tk.Text(panel, height=7, wrap="word", bg=COLORS["log"], fg=COLORS["text"], relief="flat", font=("Microsoft YaHei UI", 9))
        self.update_details.pack(fill="both", expand=True)
        actions = tk.Frame(panel, bg=COLORS["panel"])
        actions.pack(fill="x", pady=(12, 0))
        self._button(actions, "更新所选词库", self._download_words, kind="primary").pack(side="left")
        self._button(actions, "下载软件并重启更新", self._download_software, kind="secondary").pack(side="left", padx=8)
        self._button(actions, "发布页面", lambda: webbrowser.open("https://github.com/2845086683/ArknightsLocalizationToolkit/releases"), kind="secondary").pack(side="left")
        self.update_details.pack_forget()
        actions.pack_configure(side="bottom")
        self.update_details.pack(fill="both", expand=True)
        self._refresh_update_details()

    def _save_update_settings(self, *, check=False):
        try:
            self.config_data = replace(self.config_data,
                check_library_updates=self.check_words_var.get(), check_software_updates=self.check_app_var.get(),
                update_proxy_mode=next(key for key, label in PROXY_MODES.items() if label == self.proxy_mode_var.get()),
                update_source="mirror" if self.source_var.get() == "GitHub 镜像" else "github", mirror_url=normalize_mirror(self.mirror_var.get()))
            if self._save_from_form() is not None and check: self._check_online(force=True)
        except Exception as error: messagebox.showerror("更新设置无效", str(error), parent=self.update_window)

    def _refresh_update_details(self):
        if self.update_window is None or not self.update_window.winfo_exists(): return
        result = self.online_result
        lines = [f"词库目录：{library_root(self.project)}"]
        selected = self._selected_library()
        if selected: lines += [f"所选：{selected.name} / {selected.locale.upper()}", f"ID：{selected.id}"]
        for item in result.get("libraries", []):
            lines.append(f"{item['locale'].upper()} · Git {item['commit'][:10]} · {'可更新' if item['available'] else '词典内容一致'}")
        software = result.get("software")
        if software: lines += [f"Release：{software['version']} · {'可更新' if software['available'] else '无需更新'}", str(software.get("notes", ""))[:3000]]
        lines += result.get("errors", [])
        self.update_details.configure(state="normal")
        self.update_details.delete("1.0", "end")
        self.update_details.insert("end", "\n".join(lines))
        self.update_details.configure(state="disabled")

    def _download_words(self):
        library = self._selected_library()
        update = next((x for x in self.online_result.get("libraries", []) if library and x["id"] == library.id), None)
        if not update:
            messagebox.showinfo("请先检查", "请先选择项目官方词库并完成更新检查。", parent=self.update_window)
            return
        if not update["available"]:
            messagebox.showinfo("词库已一致", "所选词库的文本内容已是最新", parent=self.update_window)
            return
        config = self._form_config()
        def worker():
            client = GitHub(config, cancelled=lambda: self.cancel_requested, log=self._queue_log)
            update_library(library, update, client)
            self.events.put(("libraries_changed", library.id))
            self._queue_log("词库已更新；点击主界面的“安装 / 修复并启动”应用到游戏。旧版保存在 runtime/.backups 目录中。", "good")
        self._start_task("在线词库更新", worker)

    def _download_software(self):
        release = self.online_result.get("software")
        if not release or not release["available"]:
            messagebox.showinfo("暂无软件更新", "请先检查更新", parent=self.update_window)
            return
        config = self._form_config()
        def worker():
            candidate = prepare_software_update(self.project, release, GitHub(config, cancelled=lambda: self.cancel_requested, log=self._queue_log))
            self.events.put(("software_ready", candidate))
        self._start_task("下载并校验软件", worker)
