from __future__ import annotations

import configparser
import csv
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .runtime import AUTO_TRANSLATOR_CONFIG_PATH, inspect_orphaned_runtime
from .util import sha256_file
from .xunity import offline_config


INJECTION_MARKERS = (
    "winhttp.dll",
    ".doorstop_version",
    "doorstop_config.ini",
    "BepInEx",
)

TRANSLATION_TEXT_ROOT = PurePosixPath("BepInEx/Translation/zh/Text")


def _managed_translation_files(manifest: dict[str, Any]) -> dict[str, str]:
    """Return only staged dictionaries that are maintained by the toolkit.

    XUnity's underscore-prefixed files are generated or rewritten while the
    game is running, so they deliberately do not participate in pack version
    comparisons.
    """
    root_parts = tuple(part.casefold() for part in TRANSLATION_TEXT_ROOT.parts)
    files: dict[str, str] = {}
    for item in manifest.get("files", []):
        relative = PurePosixPath(str(item.get("path", "")))
        parts = tuple(part.casefold() for part in relative.parts)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(parts) != len(root_parts) + 1
            or parts[: len(root_parts)] != root_parts
            or relative.suffix.casefold() != ".txt"
            or relative.name.startswith("_")
        ):
            continue
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"词表清单中的 SHA-256 无效：{relative.as_posix()}")
        key = relative.as_posix().casefold()
        if key in files:
            raise ValueError(f"词表清单中存在重复路径：{relative.as_posix()}")
        files[key] = digest.casefold()
    return files


def _translation_version(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, file_hash in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def compare_translation_pack(game_dir: Path, default_runtime: Path) -> dict[str, Any]:
    """Compare the installed and default *dictionary manifests* only.

    Comparing manifests instead of every runtime file keeps logs, caches and
    configuration migrations from being mistaken for a dictionary update.
    """
    install_path = game_dir / "ArknightsLocalizationToolkit.install.json"
    if not install_path.is_file():
        return {
            "checked": False,
            "reason": "not_installed",
            "update_available": False,
        }

    default_runtime = default_runtime.expanduser().resolve()
    default_manifest_path = default_runtime / "ARKLOCALIZER_MANIFEST.json"
    installed_manifest = json.loads(install_path.read_text(encoding="utf-8-sig"))
    default_manifest = json.loads(default_manifest_path.read_text(encoding="utf-8-sig"))
    installed_files = _managed_translation_files(installed_manifest.get("staging_manifest", {}))
    default_files = _managed_translation_files(default_manifest)
    if not default_files:
        raise ValueError(f"默认运行时清单中没有可比较的主词表：{default_manifest_path}")
    if not installed_files:
        return {
            "checked": False,
            "reason": "installed_manifest_has_no_translation_pack",
            "default_runtime": str(default_runtime),
            "update_available": False,
        }

    default_paths = set(default_files)
    installed_paths = set(installed_files)
    changed = sorted(
        path
        for path in default_paths & installed_paths
        if default_files[path] != installed_files[path]
    )
    missing = sorted(default_paths - installed_paths)
    retired = sorted(installed_paths - default_paths)
    default_version = _translation_version(default_files)
    installed_version = _translation_version(installed_files)
    return {
        "checked": True,
        "default_runtime": str(default_runtime),
        "default_version": default_version,
        "installed_version": installed_version,
        "files": len(default_files),
        "changed": len(changed),
        "missing": len(missing),
        "retired": len(retired),
        "differences": changed + missing + retired,
        "update_available": default_version != installed_version,
    }


def running_game_processes() -> list[dict[str, str]]:
    if os.name != "nt":
        return []
    completed = subprocess.run(
        ["tasklist.exe", "/FI", "IMAGENAME eq Arknights.exe", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return [
        {"image": row[0], "pid": row[1]}
        for row in csv.reader(io.StringIO(completed.stdout))
        if len(row) >= 2 and row[0].casefold() == "arknights.exe"
    ]


def validate_game_executable(executable: Path) -> tuple[Path, Path]:
    executable = executable.expanduser().resolve()
    if not executable.is_file() or executable.name.casefold() != "arknights.exe":
        raise FileNotFoundError(f"请选择 Arknights.exe：{executable}")
    game_dir = executable.parent
    if not (game_dir / "Arknights_Data").is_dir():
        raise FileNotFoundError(f"Arknights_Data 不存在：{game_dir}")
    return executable, game_dir


def _effective_anon_count(*layers: Path) -> int:
    names: set[str] = set()
    for root in layers:
        if not root.is_dir():
            continue
        names.update(
            path.name.casefold()
            for path in root.iterdir()
            if path.is_file() and path.suffix.casefold() in {".bin", ".ab"}
        )
    return len(names)


def _layer_summary(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        return {"path": str(root), "exists": False, "files": 0, "bytes": 0}
    files = [path for path in root.iterdir() if path.is_file()]
    return {
        "path": str(root),
        "exists": True,
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }


def _parse_ini(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        interpolation=None,
        empty_lines_in_values=False,
    )
    parser.read_string(text)
    return parser


def _compatible_auto_translator_config(path: Path, manifest: dict[str, Any]) -> bool:
    staging = manifest.get("staging_manifest", {})
    source_locale = staging.get("source_locale")
    if source_locale not in {"en", "jp"}:
        return False
    font = staging.get("font")
    if font is not None and not isinstance(font, str):
        return False
    try:
        expected = _parse_ini(offline_config(source_locale, font or ""))
        actual = _parse_ini(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, configparser.Error, ValueError):
        return False
    return all(
        actual.has_section(section)
        and all(
            actual.get(section, option, raw=True, fallback=None) == value
            for option, value in expected.items(section, raw=True)
        )
        for section in expected.sections()
    )


def _installed_runtime(game_dir: Path) -> dict[str, Any]:
    install_path = game_dir / "ArknightsLocalizationToolkit.install.json"
    if not install_path.is_file():
        markers = [name for name in INJECTION_MARKERS if (game_dir / name).exists()]
        orphaned = inspect_orphaned_runtime(game_dir) if markers else None
        return {
            "state": "orphaned" if orphaned and orphaned["orphaned"] else ("foreign" if markers else "clean"),
            "manifest": None,
            "markers": markers,
            "orphaned_generated_files": orphaned["generated_files"] if orphaned else [],
            "unknown_runtime_files": orphaned["unknown_files"] if orphaned else [],
            "verified": 0,
            "modified": 0,
            "missing": 0,
            "runtime_modified": 0,
        }

    manifest = json.loads(install_path.read_text(encoding="utf-8"))
    verified = modified = missing = runtime_modified = 0
    for item in manifest.get("files", []):
        relative = PurePosixPath(str(item["path"]))
        destination = game_dir.joinpath(*relative.parts)
        if not destination.is_file():
            missing += 1
        elif sha256_file(destination) == item["sha256"]:
            verified += 1
        elif (
            relative.as_posix() == AUTO_TRANSLATOR_CONFIG_PATH
            and _compatible_auto_translator_config(destination, manifest)
        ):
            runtime_modified += 1
        else:
            modified += 1
    source_locale = manifest.get("staging_manifest", {}).get("source_locale")
    state = "installed" if not missing and not modified else "needs_repair"
    return {
        "state": state,
        "manifest": str(install_path),
        "source_locale": source_locale,
        "installed_at": manifest.get("installed_at"),
        "markers": [name for name in INJECTION_MARKERS if (game_dir / name).exists()],
        "verified": verified,
        "modified": modified,
        "missing": missing,
        "runtime_modified": runtime_modified,
    }


def scan_client(executable: Path, *, default_runtime: Path | None = None) -> dict[str, Any]:
    executable, game_dir = validate_game_executable(executable)
    data = game_dir / "Arknights_Data"
    base = data / "StreamingAssets" / "AB" / "Windows" / "anon"
    hot = data / "PersistentData" / "Bundles" / "anon"
    result = {
        "ok": True,
        "executable": str(executable),
        "game_directory": str(game_dir),
        "executable_bytes": executable.stat().st_size,
        "executable_modified": executable.stat().st_mtime,
        "base_layer": _layer_summary(base),
        "hot_layer": _layer_summary(hot),
        "effective_anon_bundles": _effective_anon_count(base, hot),
        "running_processes": running_game_processes(),
        "runtime": _installed_runtime(game_dir),
    }
    if default_runtime is not None:
        result["translation_pack"] = compare_translation_pack(game_dir, default_runtime)
    return result
