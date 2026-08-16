import sys
import traceback
from pathlib import Path
from tkinter import messagebox

from arklocalizer.launcher import main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        report = root / "launcher-crash.log"
        report.write_text(traceback.format_exc(), encoding="utf-8")
        messagebox.showerror("启动器发生异常", f"错误详情已写入：\n{report}")
        raise
