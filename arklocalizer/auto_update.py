"""Dependency-free, public-data-only wordlibrary builds for GitHub Actions.

Run with ``python -m arklocalizer.auto_update check`` or ``build``. No game
client, Unity tools, executable downloads, or translation service is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .libraries import MANIFEST, official_relative, read_manifest, translation_entries
from .mapping import build_translation_pack
from .util import read_json, sha256_file, write_json
from .xunity import validate_translation_pack

UPSTREAM = "ArknightsAssets/ArknightsGamedata"
UPSTREAM_REF = "master"
LOCALES = ("en", "jp")
STATE = Path(".github/wordlibrary-state")
TEXT = Path("Translation/zh/Text")
PACK_FILES = ("10_i18n.txt", "15_format_regex.txt", "20_tables.txt", "30_story.txt")
MAX_DROP = 0.10


def github_json(endpoint: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "arklocalizer-wordlibraries"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/repos/{UPSTREAM}/{endpoint}", headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("GitHub API request failed")


def builder_digest(project: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted((project / "arklocalizer").glob("*.py"))
    paths.append(project / ".github/workflows/update-wordlibraries.yml")
    for path in paths:
        digest.update(path.relative_to(project).as_posix().encode())
        digest.update(b"\0")
        # Match Windows and Linux checkouts regardless of core.autocrlf.
        digest.update(path.read_text(encoding="utf-8").encode())
        digest.update(b"\0")
    return digest.hexdigest()


def upstream_snapshot(locale: str) -> dict:
    commit = github_json(f"git/ref/heads/{UPSTREAM_REF}")["object"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid upstream commit SHA")
    tree = github_json(f"git/trees/{commit}")
    if tree.get("truncated"):
        raise ValueError("Truncated upstream directory listing")
    trees = {item["path"]: item["sha"] for item in tree["tree"] if item["type"] == "tree"}
    return {"repository": UPSTREAM, "ref": UPSTREAM_REF, "commit": commit,
            "trees": {region: trees[region] for region in (locale, "cn")}}


def needs_build(previous: dict, plan: dict) -> bool:
    # A KR/TW-only commit should not download/build the three unchanged locales.
    return (previous.get("schema") != plan["schema"]
            or previous.get("builder_sha256") != plan["builder_sha256"]
            or any(previous.get("upstream", {}).get(key) != plan["upstream"][key]
                   for key in ("repository", "ref", "trees")))


def summary(message: str) -> None:
    print(message, flush=True)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(message + "\n\n")


def check(project: Path, work: Path, locale: str, *, force: bool = False) -> dict:
    plan = {"schema": 1, "locale": locale, "upstream": upstream_snapshot(locale),
            "builder_sha256": builder_digest(project)}
    state = project / STATE / f"{locale}.json"
    previous = read_json(state) if state.exists() else {}
    changed = force or needs_build(previous, plan)
    write_json(work / "build-plan.json", plan)
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"changed={str(changed).lower()}\nupstream_sha={plan['upstream']['commit']}\n")
    summary(f"Upstream: `{UPSTREAM}@{plan['upstream']['commit']}`. "
            + (f"{locale.upper()} build required." if changed else f"{locale.upper()}/CN data and builder unchanged; skipped."))
    return plan


def validate_inputs(data: Path, plan: dict) -> None:
    def revision(ref: str) -> str:
        return subprocess.check_output(["git", "-C", str(data), "rev-parse", ref], text=True).strip()

    if revision("HEAD") != plan["upstream"]["commit"]:
        raise ValueError("Upstream checkout does not match the pinned build plan")
    if subprocess.check_output(["git", "-C", str(data), "status", "--porcelain"], text=True).strip():
        raise ValueError("Upstream checkout has local changes")
    for locale in (plan["locale"], "cn"):
        if revision(f"HEAD:{locale}") != plan["upstream"]["trees"][locale]:
            raise ValueError(f"Upstream tree mismatch: {locale}")
        root = data / locale
        version = read_json(root / "hot_update_list.json").get("versionId")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", version):
            raise ValueError(f"Missing or invalid server resource version: {locale}")
        if not (root / "i18n/string_map.txt").is_file():
            raise ValueError(f"Missing i18n data: {locale}")
        for folder, pattern in (("gamedata/excel", "*.json"), ("gamedata/story", "*.txt")):
            if not any((root / folder).rglob(pattern)):
                raise ValueError(f"Missing {folder}: {locale}")


def validate_counts(current: dict, previous: dict, locale: str) -> None:
    if not current["ok"] or not previous["ok"]:
        raise ValueError(f"{locale}: invalid translation syntax/conflicting mappings; see validation reports")
    counts = {item["name"]: item["mappings"] for item in current["files"]}
    baseline = {item["name"]: item["mappings"] for item in previous["files"]}
    if set(counts) != set(PACK_FILES) or set(baseline) != set(PACK_FILES):
        raise ValueError(f"{locale}: unexpected translation file set")
    for name in PACK_FILES:
        count, old = counts[name], baseline[name]
        # Regex output may legitimately be empty (currently EN); other categories may not.
        if (name != "15_format_regex.txt" and count == 0) or count < old * (1 - MAX_DROP):
            raise ValueError(f"{locale}/{name}: mappings dropped from {old} to {count}; "
                             "refusing automatic publication (maximum drop: 10%)")


def prepare_locale(project: Path, data: Path, work: Path, locale: str) -> tuple[Path, dict]:
    runtime = project / official_relative(locale)
    manifest = read_manifest(runtime)
    if manifest["source_locale"] != locale:
        raise ValueError(f"Wrong locale in manifest: {runtime}")
    expected = {f"BepInEx/{TEXT.as_posix()}/{name}" for name in PACK_FILES}
    if {item["path"] for item in translation_entries(manifest)} != expected:
        raise ValueError(f"{locale}: unexpected manifest translation file set")
    for entry in translation_entries(manifest):
        path = runtime / entry["path"]
        if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            raise ValueError(f"Baseline manifest hash mismatch: {path}")
    previous = validate_translation_pack(runtime / "BepInEx")
    write_json(work / locale / "baseline-validation.json", previous)
    # A fresh directory prevents stale outputs from passing validation on retries.
    with tempfile.TemporaryDirectory(prefix=f"{locale}-pack-", dir=work) as temporary:
        pack = Path(temporary)
        report = build_translation_pack(data, pack, locale)
        validation = validate_translation_pack(pack)
        write_json(work / locale / "pack-validation.json", validation)
        write_json(work / locale / "pack-report.json", report)
        shutil.copytree(pack / "reports", work / locale / "reports", dirs_exist_ok=True)
        validate_counts(validation, previous, locale)
        prepared = work / locale / "prepared"
        for name in PACK_FILES:
            destination = prepared / "BepInEx" / TEXT / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pack / TEXT / name, destination)
        by_name = {item["name"]: item for item in validation["files"]}
        for entry in translation_entries(manifest):
            updated = by_name[Path(entry["path"]).name]
            entry.update(bytes=updated["bytes"], sha256=updated["sha256"])
        write_json(prepared / MANIFEST, manifest)
    summary(f"{locale.upper()}: {previous['mappings']:,} → {validation['mappings']:,} mappings; "
            f"{report['collision_sources']:,} ambiguous sources excluded; validation passed.")
    return prepared, validation


def build(project: Path, data: Path, work: Path, locale: str) -> None:
    plan = read_json(work / "build-plan.json")
    if plan["locale"] != locale:
        raise ValueError("Build plan locale mismatch")
    if plan["builder_sha256"] != builder_digest(project):
        raise ValueError("Builder changed after the build plan was created")
    validate_inputs(data, plan)
    versions = {region: read_json(data / region / "hot_update_list.json")["versionId"]
                for region in (locale, "cn")}
    source, _ = prepare_locale(project, data, work, locale)
    manifest = read_json(source / MANIFEST)
    manifest["wordlibrary_build"] = {
        "source_locale": locale, "resource_versions": versions,
        "upstream_repository": UPSTREAM, "upstream_commit": plan["upstream"]["commit"],
        "builder_sha256": plan["builder_sha256"],
    }
    write_json(source / MANIFEST, manifest)
    runtime = project / official_relative(locale)
    for name in PACK_FILES:
        relative = Path("BepInEx") / TEXT / name
        shutil.copyfile(source / relative, runtime / relative)
    shutil.copyfile(source / MANIFEST, runtime / MANIFEST)
    # No timestamps/absolute paths: repeated builds of identical inputs are stable.
    write_json(project / STATE / f"{locale}.json", dict(plan, resource_versions=versions))
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"server_version={versions[locale]}\n")
    summary(f"{locale.upper()} resource version `{versions[locale]}`; CN `{versions['cn']}`. "
            "Validated library ready for publication to main.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build"))
    parser.add_argument("--locale", choices=LOCALES, required=True)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--work", type=Path)
    parser.add_argument("--data-root", type=Path, default=Path("cache/ArknightsGamedataMulti"))
    parser.add_argument("--force", action="store_true", help="Rebuild unchanged data; validation still applies")
    args = parser.parse_args()
    project = args.project.resolve()
    work = (args.work or Path(f"work/wordlibrary-update/{args.locale}")).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if args.command == "check":
        check(project, work, args.locale, force=args.force)
    else:
        build(project, args.data_root.resolve(), work, args.locale)


if __name__ == "__main__":
    main()
