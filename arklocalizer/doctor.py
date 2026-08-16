from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from .components import (
    FONT_ARCHIVE,
    FONT_ARCHIVE_SHA256,
    FONT_BUNDLE,
    FONT_BUNDLE_SHA256,
)
from .extractor import effective_anon_bundles
from .runtime import COMPONENT_HASHES, RICH_TEXT_FIX_PLUGIN
from .util import PROJECT_ROOT, sha256_file


ENV_CLIENTS = {
    "en": "ARKLOCALIZER_EN_GAME",
    "jp": "ARKLOCALIZER_JP_GAME",
    "cn": "ARKLOCALIZER_CN_GAME",
}


def doctor(
    project_root: Path = PROJECT_ROOT,
    *,
    game_dir: Path | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "python",
            "ok": sys.version_info[:2] == (3, 12),
            "detail": sys.version.split()[0],
        }
    )
    try:
        import UnityPy

        checks.append({"name": "UnityPy", "ok": True, "detail": UnityPy.__version__})
    except Exception as error:
        checks.append({"name": "UnityPy", "ok": False, "detail": repr(error)})

    flatc = project_root / "tools" / "flatc" / "flatc.exe"
    if flatc.is_file():
        completed = subprocess.run([str(flatc), "--version"], capture_output=True, text=True)
        checks.append({"name": "flatc", "ok": completed.returncode == 0, "detail": completed.stdout.strip()})
    else:
        checks.append({"name": "flatc", "ok": False, "detail": str(flatc)})

    clients: dict[str, Path] = {}
    if game_dir is not None:
        clients[locale or "selected"] = game_dir
    else:
        for client_locale, variable in ENV_CLIENTS.items():
            if value := os.environ.get(variable):
                clients[client_locale] = Path(value)
    for client_locale, client_dir in clients.items():
        exists = (client_dir / "Arknights.exe").is_file()
        detail: dict[str, Any] = {"path": str(client_dir)}
        if exists and client_locale != "cn":
            detail["effective_anon_bundles"] = len(effective_anon_bundles(client_dir))
        checks.append({"name": f"client_{client_locale}", "ok": exists, "detail": detail})

    data_root = project_root / "cache" / "ArknightsGamedataMulti"
    data_ok = all((data_root / locale / "gamedata" / "excel").is_dir() for locale in ("en", "jp", "cn"))
    checks.append({"name": "multilang_data", "ok": data_ok, "detail": str(data_root)})

    schema_root = project_root / "vendor" / "ArknightsFlatbuffers" / "yostar"
    schemas = len(list(schema_root.glob("*.fbs"))) if schema_root.is_dir() else 0
    checks.append({"name": "yostar_schemas", "ok": schemas >= 40, "detail": schemas})

    components = project_root / "cache" / "official-components"
    for name, expected in COMPONENT_HASHES.items():
        path = components / name
        actual = sha256_file(path) if path.is_file() else None
        checks.append(
            {
                "name": f"component_{name}",
                "ok": actual == expected,
                "detail": {"path": str(path), "sha256": actual},
            }
        )
    checks.append(
        {
            "name": "rich_text_companion",
            "ok": RICH_TEXT_FIX_PLUGIN.is_file(),
            "detail": str(RICH_TEXT_FIX_PLUGIN),
        }
    )
    for name, path, expected in (
        ("font_archive", components / FONT_ARCHIVE, FONT_ARCHIVE_SHA256),
        ("unity2021_font", project_root / "cache" / "fonts" / FONT_BUNDLE, FONT_BUNDLE_SHA256),
    ):
        actual = sha256_file(path) if path.is_file() else None
        checks.append(
            {
                "name": name,
                "ok": actual == expected,
                "detail": {"path": str(path), "sha256": actual},
            }
        )
    return {
        "ok": all(check["ok"] for check in checks if not check["name"].startswith("client_cn")),
        "platform": platform.platform(),
        "project_root": str(project_root),
        "checks": checks,
    }


def print_doctor_report(report: dict[str, Any]) -> None:
    for check in report["checks"]:
        mark = "OK" if check["ok"] else "FAIL"
        detail = check["detail"]
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        print(f"[{mark:4}] {check['name']}: {detail}")
    print(f"Overall: {'OK' if report['ok'] else 'ATTENTION REQUIRED'}")
