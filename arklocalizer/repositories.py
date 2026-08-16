from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .util import is_within


REPOSITORIES = (
    (
        "ArknightsGamedataMulti",
        "https://github.com/ArknightsAssets/ArknightsGamedata.git",
        Path("cache/ArknightsGamedataMulti"),
        ("--depth", "1", "--filter=blob:none", "--sparse"),
    ),
    (
        "ArknightsFlatbuffers",
        "https://github.com/ArknightsAssets/ArknightsFlatbuffers.git",
        Path("vendor/ArknightsFlatbuffers"),
        ("--depth", "1", "--filter=blob:none"),
    ),
    (
        "Ark-Unpacker",
        "https://github.com/isHarryh/Ark-Unpacker.git",
        Path("vendor/Ark-Unpacker"),
        ("--depth", "1", "--branch", "v5.x", "--recurse-submodules", "--shallow-submodules"),
    ),
    (
        "XUnity.AutoTranslator",
        "https://github.com/bbepis/XUnity.AutoTranslator.git",
        Path("vendor/XUnity.AutoTranslator"),
        ("--depth", "1", "--branch", "v5.6.1", "--filter=blob:none"),
    ),
)


def _run_git(project: Path, proxy: str, *arguments: str) -> None:
    command = ["git"]
    if proxy:
        command += ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    command.extend(arguments)
    subprocess.run(command, cwd=project, check=True)


def update_public_repositories(project: Path, *, proxy: str = "") -> dict[str, Any]:
    """Clone or fast-forward the public data/schema/reference repositories."""
    project = project.resolve()
    results: list[dict[str, str]] = []
    for name, url, relative, clone_options in REPOSITORIES:
        destination = (project / relative).resolve()
        if not is_within(destination, project):
            raise ValueError(f"Repository path escaped project directory: {destination}")
        git_dir = destination / ".git"
        if git_dir.is_dir():
            if name == "XUnity.AutoTranslator":
                # This dependency is intentionally pinned to a release tag.
                # A checkout at a tag is detached, so ``git pull`` is invalid.
                _run_git(project, proxy, "-C", str(destination), "fetch", "--tags", "origin")
                _run_git(project, proxy, "-C", str(destination), "checkout", "--detach", "v5.6.1")
            else:
                _run_git(project, proxy, "-C", str(destination), "pull", "--ff-only")
            action = "updated"
        elif destination.exists():
            raise FileExistsError(f"Repository destination exists but is not a Git checkout: {destination}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _run_git(
                project,
                proxy,
                "clone",
                *clone_options,
                url,
                str(destination),
            )
            action = "cloned"
        if name == "ArknightsGamedataMulti":
            _run_git(project, proxy, "-C", str(destination), "sparse-checkout", "set", "en", "jp", "cn")
        if name == "Ark-Unpacker":
            _run_git(
                project,
                proxy,
                "-C",
                str(destination),
                "submodule",
                "update",
                "--init",
                "--recursive",
            )
        results.append({"name": name, "action": action, "path": str(destination)})
    return {"repositories": results}
