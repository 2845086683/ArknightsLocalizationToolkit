"""User-selected, region-specific runtime libraries with persistent identities."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .util import sha256_file, write_json

MANIFEST = "ARKLOCALIZER_MANIFEST.json"
METADATA = "WORDLIBRARY.json"
TRANSLATIONS = "BepInEx/Translation/"
REPOSITORY = "2845086683/ArknightsLocalizationToolkit"


def library_root(project: Path) -> Path:
    return project / "runtime"


def official_relative(locale: str) -> str:
    return f"runtime/{locale}-zh-offline-final"


def official_id(locale: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{REPOSITORY}/libraries/{locale}"))


def relative_path(relative: str) -> PurePosixPath:
    value = PurePosixPath(relative)
    if not relative or "\\" in relative or ":" in relative or value.is_absolute() or any(p in {".", ".."} or p.endswith((".", " ")) for p in value.parts):
        raise ValueError(f"不安全的文件路径：{relative}")
    if not value.parts: raise ValueError(f"无效的文件路径：{relative}")
    return value


def safe_child(root: Path, relative: str) -> Path:
    value = relative_path(relative)
    target = (root / Path(*value.parts)).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError(f"文件超出目标目录：{relative}")
    return target


def read_manifest(path: Path) -> dict:
    data = json.loads((path / MANIFEST).read_text(encoding="utf-8-sig"))
    if data.get("source_locale") not in {"en", "jp"} or not isinstance(data.get("files"), list):
        raise ValueError(f"词库清单无效：{path}")
    seen = set()
    for entry in data["files"]:
        key = str(entry["path"])
        safe_child(path, key)
        if key.casefold() in seen:
            raise ValueError(f"清单包含重复路径：{key}")
        seen.add(key.casefold())
    return data


@dataclass(frozen=True)
class Library:
    id: str
    name: str
    locale: str
    path: Path
    repository: str = ""
    remote_path: str = ""
    commit: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} · {self.locale.upper()} · {self.id[:8]}"


def identify(path: Path) -> Library:
    manifest = read_manifest(path)
    meta_path = path / METADATA
    if not meta_path.exists():
        write_json(meta_path, {"schema": 1, "id": str(uuid.uuid4()), "name": path.name, "locale": manifest["source_locale"]})
    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    identifier = str(uuid.UUID(meta["id"]))
    if meta.get("locale") != manifest["source_locale"]:
        raise ValueError(f"词库区服与清单不一致：{path}")
    return Library(identifier, str(meta["name"]), meta["locale"], path.resolve(),
                   str(meta.get("repository", "")), str(meta.get("remote_path", "")), str(meta.get("commit", "")))


def discover(project: Path, locale: str | None = None) -> list[Library]:
    result = []
    seen = set()
    for path in sorted(library_root(project).glob(f"*/{MANIFEST}")):
        if path.parent.name.startswith("."):
            continue
        library = identify(path.parent)
        if library.id in seen:
            raise ValueError(f"发现重复词库 ID {library.id}，请移走重复副本：{library.path}")
        seen.add(library.id)
        if locale is None or library.locale == locale:
            result.append(library)
    return result


def verify(path: Path) -> dict:
    manifest = read_manifest(path)
    for entry in manifest["files"]:
        item = safe_child(path, entry["path"])
        if not item.is_file() or item.stat().st_size != entry["bytes"] or sha256_file(item) != entry["sha256"]:
            raise ValueError(f"词库文件校验失败：{entry['path']}")
    return manifest


def import_library(project: Path, source: Path, *, official: bool = False) -> Library:
    manifest = verify(source)
    locale = manifest["source_locale"]
    if source.resolve().parent == library_root(project).resolve():
        return identify(source)
    identifier = official_id(locale) if official else str(uuid.uuid4())
    name = f"官方中文词库（{'美服' if locale == 'en' else '日服'}）" if official else source.name
    metadata = {"schema": 1, "id": identifier, "name": name, "locale": locale}
    if official:
        metadata.update(repository=REPOSITORY, remote_path=official_relative(locale))
    elif (source / METADATA).exists():
        metadata = json.loads((source / METADATA).read_text(encoding="utf-8-sig"))
        identifier = str(uuid.UUID(metadata["id"]))
    if any(item.id == identifier for item in discover(project)):
        raise ValueError("此词库 ID 已存在，无需重复导入")
    destination = library_root(project) / (f"{locale}-zh-offline-final" if official else identifier)
    if destination.exists(): raise FileExistsError(destination)
    temporary = destination.with_name(".import-" + uuid.uuid4().hex)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, temporary)
        write_json(temporary / METADATA, metadata)
        identify(temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    return identify(destination)


def migrate_legacy(project: Path, config=None) -> list[Library]:
    from .library_migration import migrate
    return migrate(project, config)


def translation_entries(manifest: dict) -> list[dict]:
    entries = [entry for entry in manifest["files"] if entry["path"].startswith(TRANSLATIONS)]
    if not entries or any(not entry["path"].endswith(".txt") for entry in entries):
        raise ValueError("远程词库没有有效的文本词典")
    return entries
