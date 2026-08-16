from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path: Path, value: Any, *, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=indent)
        stream.write("\n")
    os.replace(temporary, path)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def normalize_kv_arrays(value: Any) -> Any:
    """Convert FlatBuffers [{key, value}] maps to regular dictionaries."""
    if isinstance(value, dict):
        return {str(key): normalize_kv_arrays(item) for key, item in value.items()}
    if isinstance(value, list):
        normalized = [normalize_kv_arrays(item) for item in value]
        if normalized and all(
            isinstance(item, dict) and set(item) == {"key", "value"}
            for item in normalized
        ):
            keys = [str(item["key"]) for item in normalized]
            if len(keys) == len(set(keys)):
                return {key: item["value"] for key, item in zip(keys, normalized)}
        return normalized
    return value


HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
LATIN_RE = re.compile(r"[A-Za-z]")


def contains_han(text: str) -> bool:
    return bool(HAN_RE.search(text))


def looks_like_source_language(text: str, locale: str) -> bool:
    if locale == "en":
        return bool(LATIN_RE.search(text))
    if locale == "jp":
        return bool(KANA_RE.search(text) or HAN_RE.search(text) or LATIN_RE.search(text))
    raise ValueError(f"Unsupported source locale: {locale}")


def normalize_lookup_text(text: str) -> str:
    """Approximate XUnity's whitespace-normalized dictionary lookup key."""
    # Public Arknights JSON and string-map dumps are inconsistent here: some
    # fields contain a real newline while their matching locale contains the
    # two characters ``\\n``.  The client resolves both to a line break before
    # assigning the final text component, so normalize them before comparing
    # placeholders or writing the XUnity lookup key.
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    text = text.strip()
    return re.sub(r"[ \t]*\n[ \t]*", "\n", text)


PLACEHOLDER_RE = re.compile(
    r"</?(?:@[^>]*|[A-Za-z][^>]*)>"
    r"|</>"
    r"|\{\{[^{}]+\}\}"
    r"|\{[^{}\r\n]+\}"
    r"|%(?:\d+\$)?[-+#0-9.]*[A-Za-z]"
    r"|\\[nr]"
    r"|\$[A-Za-z_][A-Za-z0-9_]*"
)


def placeholder_counter(text: str) -> Counter[str]:
    return Counter(PLACEHOLDER_RE.findall(text))


NON_TEXT_FIELD_PARTS = (
    "asset",
    "audio",
    "avatar",
    "bgm",
    "bundle",
    "code",
    "color",
    "icon",
    "image",
    "model",
    "path",
    "prefab",
    "resource",
    "script",
    "template",
    "url",
)


def field_is_probably_identifier(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    field = path[-1].casefold()
    if field in {"id", "key", "type", "enum", "code"}:
        return True
    if field.endswith("id") or field.endswith("key"):
        return True
    # Match semantic field-name tokens instead of arbitrary substrings.
    # The previous substring check treated every ``description`` field as an
    # identifier because it contains the letters "script", discarding tens of
    # thousands of skill, talent, trait and module descriptions.
    tokenized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", path[-1])
    tokens = {
        token.casefold()
        for token in re.split(r"[^A-Za-z0-9]+", tokenized)
        if token
    }
    return bool(tokens.intersection(NON_TEXT_FIELD_PARTS))


IDENTITY_FIELDS = (
    "id",
    "key",
    "charId",
    "skillId",
    "stageId",
    "itemId",
    "enemyId",
    "storyId",
    "skinId",
    "uniEquipId",
)


def indexed_list(items: list[Any]) -> dict[str, Any] | None:
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for field in IDENTITY_FIELDS:
        if all(field in item and isinstance(item[field], (str, int)) for item in items):
            result = {str(item[field]): item for item in items}
            if len(result) == len(items):
                return result
    return None


def walk_paired(
    source: Any,
    target: Any,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[str, str, tuple[str, ...]]]:
    if isinstance(source, str) and isinstance(target, str):
        yield source, target, path
        return
    if isinstance(source, dict) and isinstance(target, dict):
        for key in source.keys() & target.keys():
            yield from walk_paired(source[key], target[key], path + (str(key),))
        return
    if isinstance(source, list) and isinstance(target, list):
        if len(source) == len(target):
            for index, (source_item, target_item) in enumerate(zip(source, target)):
                yield from walk_paired(source_item, target_item, path + (str(index),))
            return
        source_index = indexed_list(source)
        target_index = indexed_list(target)
        if source_index is not None and target_index is not None:
            for key in source_index.keys() & target_index.keys():
                yield from walk_paired(source_index[key], target_index[key], path + (key,))


def relative_files(root: Path, pattern: str) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix().casefold(): path
        for path in root.rglob(pattern)
        if path.is_file()
    }


def chunked(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    chunk: list[Any] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
