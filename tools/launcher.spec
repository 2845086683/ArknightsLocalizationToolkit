# Build only via tools/build_launcher.py: it isolates the environment, verifies
# DLL provenance and runs the resulting EXE before publishing it.
from pathlib import Path
import runpy
import sys
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

project = Path(SPECPATH).resolve().parent
prefix = Path(sys.prefix).resolve()
tk_bin = prefix / "Library" / "bin"
version = runpy.run_path(str(project / "arklocalizer/__init__.py"))["__version__"]
version_tuple = tuple(map(int, version.split("."))) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple,
                     mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("080404B0", [
        StringStruct("FileDescription", "明日方舟离线汉化启动器"),
        StringStruct("FileVersion", version),
        StringStruct("ProductName", "Arknights Localization Toolkit"),
        StringStruct("ProductVersion", version),
        StringStruct("OriginalFilename", "明日方舟汉化启动器.exe"),
    ])]), VarFileInfo([VarStruct("Translation", [2052, 1200])])],
)

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
    version=version_info,
)
