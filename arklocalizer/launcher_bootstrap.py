"""Startup diagnostics that still work when Tk itself cannot initialize."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import traceback
from pathlib import Path


def write_crash_report(details: str) -> Path | None:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    candidates = [root / "launcher-crash.log"]
    if local := os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(local) / "ArknightsLocalizationToolkit" / "launcher-crash.log")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(details, encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def show_startup_error(report: Path | None) -> None:
    message = "启动器未能初始化。请使用完整工具包，或将错误日志反馈给维护者。"
    if report is not None:
        message += f"\n\n错误详情：\n{report}"
    # Do not use tkinter.messagebox here: a Tcl failure would trigger another
    # Tk initialization failure and hide the original diagnostic.
    try:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "启动器发生异常", 0x10)
        elif sys.stderr is not None:
            print(message, file=sys.stderr)
    except Exception:
        pass


def startup_check() -> dict[str, object]:
    from .launcher import LAUNCHER_BUILD, LauncherApp
    from .runtime import BEPINEX_CONFIG_TEMPLATE

    app = LauncherApp(startup_check=True)
    try:
        app._update_dialog()
        app.update_window.withdraw()
        # Construct the real widgets and exercise Tcl/Tk/ttk, not just import
        # tkinter or check whether init.tcl exists in the archive.
        app.update_idletasks()
        app.update()
        if not BEPINEX_CONFIG_TEMPLATE.is_file():
            raise FileNotFoundError(BEPINEX_CONFIG_TEMPLATE)
        return {
            "ok": True,
            "build": LAUNCHER_BUILD,
            "frozen": bool(getattr(sys, "frozen", False)),
            "tcl": str(app.tk.call("info", "patchlevel")),
            "tk": str(app.tk.call("package", "require", "Tk")),
            "tcl_library": str(app.tk.call("info", "library")),
            "tk_library": str(app.tk.getvar("tk_library")),
            "widgets": len(app.winfo_children()),
        }
    finally:
        app.destroy()


def run(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    check_requested = bool(args and args[0] in {"--startup-check", "--check-updates"})
    if check_requested and len(args) != 2:
        return 2
    report_path = Path(args[1]) if check_requested else None
    try:
        if report_path is not None:
            if args[0] == "--check-updates":
                from . import __version__
                from .launcher_backend import project_root, load_config
                from .libraries import REPOSITORY, migrate_legacy
                from .updates import check_updates, GitHub
                root, config = project_root(), load_config()
                migrate_legacy(root)
                messages = []
                result = check_updates(root, config, force=True, client=GitHub(config, log=messages.append))
                result.update(ok=not result['errors'], version=__version__, repository=REPOSITORY, messages=messages)
            else:
                result = startup_check()
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0 if result.get('ok') else 1
        # Keep application imports inside the error boundary as well.
        from .launcher import main
        return main()
    except Exception:
        details = traceback.format_exc()
        if report_path is not None:
            try:
                report_path.write_text(json.dumps({"ok": False, "error": details}), encoding="utf-8")
            except OSError:
                pass
        else:
            show_startup_error(write_crash_report(details))
        return 1
