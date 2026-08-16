from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import UnityPy
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from UnityPy.enums.BundleFile import CompressionFlags
from UnityPy.helpers import CompressionHelper

from .lz4ak import decompress_lz4ak
from .util import normalize_kv_arrays, sha256_bytes, write_json


CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak

FBS_NAME_RE = re.compile(r"^(?P<name>.+?)(?P<schema>[0-9a-fA-F]{6})$")
AES_MASK_V2 = b"UITpAi82pHAWwnzqHRMCwPonJLIB3WCl"


@dataclass(frozen=True)
class BundleSource:
    path: Path
    layer: str
    relative: str


def validate_game_directory(game_dir: Path) -> Path:
    game_dir = game_dir.resolve()
    if not (game_dir / "Arknights.exe").is_file():
        raise FileNotFoundError(f"Arknights.exe not found below {game_dir}")
    if not (game_dir / "Arknights_Data").is_dir():
        raise FileNotFoundError(f"Arknights_Data not found below {game_dir}")
    return game_dir


def effective_anon_bundles(game_dir: Path) -> list[BundleSource]:
    game_dir = validate_game_directory(game_dir)
    data = game_dir / "Arknights_Data"
    layers = (
        ("base", data / "StreamingAssets" / "AB" / "Windows" / "anon"),
        ("hot", data / "PersistentData" / "Bundles" / "anon"),
    )
    effective: dict[str, BundleSource] = {}
    for layer, root in layers:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.is_file() and path.suffix.casefold() in {".bin", ".ab"}:
                effective[path.name.casefold()] = BundleSource(path.resolve(), layer, path.name)
    return sorted(effective.values(), key=lambda item: (item.layer, item.relative.casefold()))


def _safe_virtual_path(value: str, fallback: str) -> PurePosixPath:
    value = value.replace("\\", "/").lstrip("/")
    path = PurePosixPath(value or fallback)
    if path.is_absolute() or ".." in path.parts:
        return PurePosixPath("unmapped") / fallback
    return path


def _matches_scope(virtual_path: PurePosixPath, scope: str) -> bool:
    value = "/" + virtual_path.as_posix().casefold()
    if scope == "all":
        return True
    if scope == "story":
        return "/gamedata/story/" in value
    if scope == "tables":
        return (
            "/gamedata/excel/" in value
            or "/i18n/" in value
            or virtual_path.stem.casefold() in {"string_map", "main_text", "init_text"}
        )
    raise ValueError(f"Unknown extraction scope: {scope}")


def _text_asset_bytes(value: Any) -> bytes:
    script = getattr(value, "m_Script", b"")
    if isinstance(script, str):
        return script.encode("utf-8", errors="surrogateescape")
    return bytes(script)


def _short_error(error: BaseException) -> str:
    message = str(error)
    if len(message) > 500:
        message = message[:500] + "..."
    return f"{type(error).__name__}: {message}"


def extract_text_assets(
    game_dir: Path,
    output_root: Path,
    *,
    scope: str = "tables",
    progress: bool = True,
) -> dict[str, Any]:
    bundles = effective_anon_bundles(game_dir)
    output_root = output_root.resolve()
    raw_root = output_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    manifest_items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for bundle_index, bundle in enumerate(bundles, start=1):
        if progress and (bundle_index == 1 or bundle_index % 10 == 0 or bundle_index == len(bundles)):
            print(f"[extract] bundle {bundle_index}/{len(bundles)}: {bundle.path.name}", flush=True)
        try:
            with bundle.path.open("rb") as stream:
                environment = UnityPy.load(stream)
            candidates: list[tuple[str, Any]] = list(environment.container.items())
            if scope == "all":
                container_readers = {reader.path_id for reader in environment.container.values()}
                for reader in environment.objects:
                    if reader.path_id not in container_readers and reader.type.name == "TextAsset":
                        candidates.append((f"unmapped/{bundle.path.stem}_{reader.path_id}.bytes", reader))

            for container_path, reader in candidates:
                if reader.type.name != "TextAsset":
                    continue
                fallback = f"{bundle.path.stem}_{reader.path_id}.bytes"
                virtual_path = _safe_virtual_path(container_path, fallback)
                if not _matches_scope(virtual_path, scope):
                    continue
                try:
                    value = reader.read()
                    data = _text_asset_bytes(value)
                    destination = raw_root.joinpath(*virtual_path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists() and destination.read_bytes() != data:
                        destination = destination.with_name(
                            f"{destination.stem}__{reader.path_id}{destination.suffix}"
                        )
                    destination.write_bytes(data)
                    manifest_items.append(
                        {
                            "virtual_path": virtual_path.as_posix(),
                            "raw_path": destination.relative_to(output_root).as_posix(),
                            "bundle": str(bundle.path),
                            "bundle_layer": bundle.layer,
                            "path_id": reader.path_id,
                            "name": getattr(value, "m_Name", ""),
                            "bytes": len(data),
                            "sha256": sha256_bytes(data),
                        }
                    )
                except Exception as error:
                    errors.append(
                        {
                            "bundle": str(bundle.path),
                            "virtual_path": virtual_path.as_posix(),
                            "error": _short_error(error),
                        }
                    )
        except Exception as error:  # keep scanning independent bundles
            errors.append({"bundle": str(bundle.path), "error": _short_error(error)})

    manifest = {
        "game_directory": str(game_dir.resolve()),
        "scope": scope,
        "bundle_count": len(bundles),
        "text_asset_count": len(manifest_items),
        "error_count": len(errors),
        "items": manifest_items,
        "errors": errors,
    }
    write_json(output_root / "extract-manifest.json", manifest)
    return manifest


def _decrypt_aes_document(data: bytes) -> Any:
    if len(data) < 160:
        raise ValueError("Encrypted asset too short")
    payload = data[128:]
    key = AES_MASK_V2[:16]
    iv = bytes(value ^ mask for value, mask in zip(payload[:16], AES_MASK_V2[16:]))
    decrypted = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(payload[16:]), AES.block_size)
    try:
        return json.loads(decrypted.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        import bson

        return bson.loads(decrypted)


def _decode_flatbuffer(
    data: bytes,
    stem: str,
    schema: Path,
    flatc: Path,
) -> Any:
    with tempfile.TemporaryDirectory(prefix="arklocalizer-fbs-") as temporary_name:
        temporary = Path(temporary_name)
        binary = temporary / f"{stem}.bin"
        binary.write_bytes(data[128:])
        command = [
            str(flatc),
            "-o",
            str(temporary),
            str(schema),
            "--",
            str(binary),
            "--no-warnings",
            "--json",
            "--strict-json",
            "--natural-utf8",
            "--defaults-json",
            "--unknown-json",
            "--raw-binary",
            "--force-empty",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"flatc exited {completed.returncode}")
        decoded = temporary / f"{stem}.json"
        return normalize_kv_arrays(json.loads(decoded.read_text(encoding="utf-8")))


def decode_extracted_assets(
    extraction_root: Path,
    output_root: Path,
    schema_root: Path,
    flatc: Path,
) -> dict[str, Any]:
    manifest = json.loads((extraction_root / "extract-manifest.json").read_text(encoding="utf-8"))
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []
    stats = {"flatbuffer": 0, "plaintext": 0, "encrypted": 0, "failed": 0}

    for item in manifest["items"]:
        raw_path = extraction_root / item["raw_path"]
        virtual = PurePosixPath(item["virtual_path"])
        data = raw_path.read_bytes()
        stem = virtual.stem
        match = FBS_NAME_RE.match(stem)
        try:
            if match and (schema_root / f"{match.group('name')}.fbs").is_file():
                logical_name = match.group("name")
                decoded = _decode_flatbuffer(
                    data,
                    stem,
                    schema_root / f"{logical_name}.fbs",
                    flatc,
                )
                destination = output_root.joinpath(*virtual.parent.parts) / f"{logical_name}.json"
                write_json(destination, decoded)
                kind = "flatbuffer"
            else:
                try:
                    text = data.decode("utf-8-sig")
                    printable = sum(char.isprintable() or char in "\r\n\t" for char in text)
                    if text and printable / len(text) < 0.80:
                        raise UnicodeDecodeError("utf-8", data, 0, 1, "low printable ratio")
                    destination = output_root.joinpath(*virtual.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(text, encoding="utf-8", newline="\n")
                    kind = "plaintext"
                except UnicodeDecodeError:
                    decoded = _decrypt_aes_document(data)
                    destination = output_root.joinpath(*virtual.parent.parts) / f"{stem}.json"
                    write_json(destination, decoded)
                    kind = "encrypted"
            stats[kind] += 1
            results.append(
                {
                    "virtual_path": virtual.as_posix(),
                    "kind": kind,
                    "output": destination.relative_to(output_root).as_posix(),
                }
            )
        except Exception as error:
            stats["failed"] += 1
            results.append(
                {
                    "virtual_path": virtual.as_posix(),
                    "kind": "failed",
                    "error": repr(error),
                }
            )

    report = {"stats": stats, "items": results}
    write_json(output_root / "decode-report.json", report)
    return report


def extract_and_decode(
    game_dir: Path,
    output_root: Path,
    schema_root: Path,
    flatc: Path,
    *,
    scope: str = "tables",
) -> dict[str, Any]:
    extraction_root = output_root / "extracted"
    decoded_root = output_root / "decoded"
    extraction = extract_text_assets(game_dir, extraction_root, scope=scope)
    decoding = decode_extracted_assets(extraction_root, decoded_root, schema_root, flatc)
    return {"extraction": extraction, "decoding": decoding}
