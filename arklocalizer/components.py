from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .runtime import BEPINEX_ARCHIVE, COMPONENT_HASHES, XUNITY_ARCHIVE
from .util import PROJECT_ROOT, sha256_file
from .xunity import DEFAULT_TMP_FONT


FONT_BUNDLE = DEFAULT_TMP_FONT
FONT_BUNDLE_SHA256 = "b1962c66f900e0a908ae85b98b9b138b783d7da222b90bbf05cff2d14cc98f5b"

COMPONENT_URLS = {
    BEPINEX_ARCHIVE: (
        "https://github.com/BepInEx/BepInEx/releases/download/"
        "v6.0.0-pre.2/BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip"
    ),
    XUNITY_ARCHIVE: (
        "https://github.com/bbepis/XUnity.AutoTranslator/releases/download/"
        "v5.6.1/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip"
    ),
}

_DOWNLOAD_RETRY_DELAYS = (1.0, 2.0, 4.0)
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class _DownloadedHashMismatch(ValueError):
    """A completed response whose bytes do not match the pinned artifact."""


def _is_retryable_download_error(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in _RETRYABLE_HTTP_STATUS
    if isinstance(error, urllib.error.URLError):
        return isinstance(
            error.reason,
            (ConnectionError, OSError, TimeoutError, socket.timeout, ssl.SSLError),
        )
    return isinstance(
        error,
        (
            _DownloadedHashMismatch,
            ConnectionError,
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ),
    )


def _download_verified(url: str, destination: Path, expected_hash: str, proxy: str | None) -> str:
    if destination.is_file():
        actual = sha256_file(destination)
        if actual != expected_hash:
            raise ValueError(f"Existing download has unexpected SHA-256: {destination} ({actual})")
        return "cached"

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    handlers: list[urllib.request.BaseHandler] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    attempts = len(_DOWNLOAD_RETRY_DELAYS) + 1
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "ArknightsLocalizationToolkit/0.1"},
        )
        try:
            with opener.open(request, timeout=90) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            actual = sha256_file(partial)
            if actual != expected_hash:
                raise _DownloadedHashMismatch(
                    f"Downloaded SHA-256 mismatch for {destination.name}: {actual}"
                )
            os.replace(partial, destination)
            return "downloaded"
        except Exception as error:
            if partial.is_file():
                partial.unlink()
            retryable = _is_retryable_download_error(error)
            if not retryable:
                raise
            if attempt == attempts - 1:
                proxy_hint = (
                    "Check the configured HTTP(S) proxy."
                    if proxy
                    else "Retry later or configure an HTTP(S) proxy in the launcher."
                )
                raise RuntimeError(
                    f"Failed to download {destination.name} after {attempts} attempts: "
                    f"{error}. {proxy_hint}"
                ) from error
            time.sleep(_DOWNLOAD_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


def prepare_official_components(
    components_root: Path,
    font_root: Path,
    *,
    proxy: str | None = None,
) -> dict[str, Any]:
    expected = dict(COMPONENT_HASHES)
    downloads = []
    for name, expected_hash in expected.items():
        destination = components_root / name
        status = _download_verified(COMPONENT_URLS[name], destination, expected_hash, proxy)
        downloads.append(
            {"name": name, "status": status, "sha256": expected_hash, "url": COMPONENT_URLS[name]}
        )

    font = font_root / FONT_BUNDLE
    if not font.is_file():
        bundled = PROJECT_ROOT / "runtime" / "jp-zh-offline-final" / FONT_BUNDLE
        if not bundled.is_file() or sha256_file(bundled) != FONT_BUNDLE_SHA256:
            raise FileNotFoundError(
                "CN font not available. Use a complete toolkit or run import-cn-font --game-dir <CN client>."
            )
        font_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, font)
    actual_font_hash = sha256_file(font)
    source_report = font.with_name(font.name + ".json")
    expected_font_hash = FONT_BUNDLE_SHA256
    if source_report.is_file():
        expected_font_hash = json.loads(source_report.read_text(encoding="utf-8"))["sha256"]
    if actual_font_hash != expected_font_hash:
        raise ValueError(f"CN font SHA-256 mismatch: {font}")
    from .cn_font import validate_cn_font
    validate_cn_font(font)
    return {
        "downloads": downloads,
        "font": {"path": str(font.resolve()), "sha256": actual_font_hash},
    }
