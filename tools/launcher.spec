# Build only via tools/build_launcher.py: it isolates the environment, verifies
# DLL provenance and runs the resulting EXE before publishing it.
from pathlib import Path
import sys

project = Path(SPECPATH).resolve().parent
prefix = Path(sys.prefix).resolve()
tk_bin = prefix / "Library" / "bin"

a = Analysis(
    [str(project / "launcher_entry.pyw")],
    pathex=[str(project)],
    binaries=[(str(tk_bin / name), ".") for name in ("tcl86t.dll", "tk86t.dll")],
    datas=[(str(project / "tools" / "runtime" / "BepInEx.cfg"), "tools/runtime")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Font extraction/staging runs in the separate maintainer Python CLI, not
    # the frozen installer. Do not bundle UnityPy and the asset extractor.
    excludes=["arklocalizer.cn_font"],
    noarchive=False,
    optimize=0,
)

# Refuse to package a DLL from another Python/Conda installation, even if
# future PyInstaller dependency-resolution behavior changes.
for name in ("tcl86t.dll", "tk86t.dll", "_tkinter.pyd"):
    matches = [Path(source).resolve() for dest, source, kind in a.binaries if Path(dest).name.lower() == name]
    expected = (tk_bin / name) if name.endswith(".dll") else (prefix / "DLLs" / name)
    if matches != [expected.resolve()]:
        raise RuntimeError(f"Foreign or missing Tk dependency: {name}: {matches}")

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="明日方舟汉化启动器", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
)
