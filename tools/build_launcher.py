"""Build and smoke-test a Windows launcher before replacing the release EXE.

Run with the project's Conda Python: .conda-env/python.exe tools/build_launcher.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
EXE_NAME = "明日方舟汉化启动器.exe"


def isolated_environment(prefix: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.upper().startswith(("PYTHON", "CONDA", "_CE_", "ARKLOCALIZER_")) or key.upper() in {
            "TCL_LIBRARY", "TK_LIBRARY", "TCLLIBPATH",
        }:
            del env[key]
    system = Path(env.get("SystemRoot", r"C:\Windows"))
    paths = [system / "System32", system]
    if prefix is not None:
        paths = [prefix, prefix / "Library" / "bin", prefix / "DLLs", prefix / "Scripts", *paths]
        env["TCL_LIBRARY"] = str(prefix / "Library" / "lib" / "tcl8.6")
        env["TK_LIBRARY"] = str(prefix / "Library" / "lib" / "tk8.6")
        env["CONDA_PREFIX"] = str(prefix)
    env["PATH"] = os.pathsep.join(map(str, paths))
    env["PYTHONUTF8"] = "1"
    return env


def check_startup(command: list[str], report: Path, env: dict[str, str], *, frozen: bool) -> dict:
    subprocess.run(
        [*command, "--startup-check", str(report)], cwd=report.parent, env=env,
        check=True, timeout=60,
    )
    result = json.loads(report.read_text(encoding="utf-8"))
    if result.get("ok") is not True or result.get("frozen") is not frozen or not result.get("widgets"):
        raise RuntimeError(f"Invalid startup-check result: {result}")
    return result


def verify_archive(executable: Path, prefix: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    expected = {
        "tcl86t.dll": prefix / "Library/bin/tcl86t.dll",
        "tk86t.dll": prefix / "Library/bin/tk86t.dll",
        "_tkinter.pyd": prefix / "DLLs/_tkinter.pyd",
        "_tcl_data/init.tcl": prefix / "Library/lib/tcl8.6/init.tcl",
        "_tk_data/tk.tcl": prefix / "Library/lib/tk8.6/tk.tcl",
        "tools/runtime/BepInEx.cfg": PROJECT / "tools/runtime/BepInEx.cfg",
    }
    names = {name.replace("\\", "/"): name for name in archive.toc}
    for name, source in expected.items():
        if name not in names or archive.extract(names[name]) != source.read_bytes():
            raise RuntimeError(f"Packaged dependency mismatch: {name}")


def build() -> Path:
    if os.name != "nt":
        raise RuntimeError("The launcher must be built and tested on Windows")
    prefix = Path(sys.prefix).resolve()
    for relative in ("Library/bin/tcl86t.dll", "Library/bin/tk86t.dll", "DLLs/_tkinter.pyd",
                     "Library/lib/tcl8.6/init.tcl", "Library/lib/tk8.6/tk.tcl"):
        if not (prefix / relative).is_file():
            raise FileNotFoundError(f"Use the project Conda environment; missing {prefix / relative}")
    work = PROJECT / "work"
    work.mkdir(exist_ok=True)
    build_root = Path(tempfile.mkdtemp(prefix="launcher-verified-", dir=work))
    print(f"Build reports: {build_root}", flush=True)
    env = isolated_environment(prefix)
    source = check_startup(
        [sys.executable, str(PROJECT / "launcher_entry.pyw")], build_root / "source-check.json", env,
        frozen=False,
    )
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(build_root / "dist"), "--workpath", str(build_root / "build"),
         str(PROJECT / "tools/launcher.spec")],
        cwd=PROJECT, env=env, check=True,
    )
    candidate = build_root / "dist" / EXE_NAME
    verify_archive(candidate, prefix)
    clean = check_startup([str(candidate)], build_root / "clean-check.json", isolated_environment(None), frozen=True)
    # Reproduce hostile user environment variables without relying on another
    # locally installed Python. The frozen program must use its own scripts.
    contaminated = isolated_environment(None)
    contaminated.update({
        "TCL_LIBRARY": str(build_root / "nonexistent-tcl"),
        "TK_LIBRARY": str(build_root / "nonexistent-tk"),
        "TCLLIBPATH": str(build_root / "nonexistent-modules"),
        "PYTHONHOME": str(build_root / "nonexistent-python"),
        "PYTHONPATH": str(build_root / "nonexistent-python"),
    })
    hostile = check_startup([str(candidate)], build_root / "contaminated-check.json", contaminated, frozen=True)
    for result in (clean, hostile):
        if any(result[key] != source[key] for key in ("tcl", "tk", "build")):
            raise RuntimeError(f"Frozen/source runtime mismatch: {result}")
    destination = PROJECT / EXE_NAME
    if destination.exists():
        shutil.copy2(destination, build_root / "previous-launcher.exe")
    os.replace(candidate, destination)
    (build_root / "release.json").write_text(json.dumps({
        "executable": str(destination),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "source": source, "clean": clean, "contaminated": hostile,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified and published: {destination}", flush=True)
    return destination


if __name__ == "__main__":
    build()
