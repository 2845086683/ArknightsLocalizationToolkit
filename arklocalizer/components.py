from __future__ import annotations

import http.client
import os
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import py7zr

from .runtime import BEPINEX_ARCHIVE, COMPONENT_HASHES, XUNITY_ARCHIVE
from .util import sha256_file


FONT_ARCHIVE = "TMP_Font_AssetBundles_2025-12-08.7z"
FONT_ARCHIVE_SHA256 = "889e963fb9dbd4b64927e0adf5d9060e1d0fb9d6bceb0c407d0597643e2b54ec"
FONT_BUNDLE = "arialuni_sdf_u2021"
FONT_BUNDLE_SHA256 = "63a5cbf2b9c7351c6ff8f7f592be03d2cc79668fad48f3cfe8e0e547af43aa3c"

COMPONENT_URLS = {
    BEPINEX_ARCHIVE: (
        "https://github.com/BepInEx/BepInEx/releases/download/"
        "v6.0.0-pre.2/BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip"
    ),
    XUNITY_ARCHIVE: (
        "https://github.com/bbepis/XUnity.AutoTranslator/releases/download/"
        "v5.6.1/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip"
    ),
    FONT_ARCHIVE: (
        "https://github.com/bbepis/XUnity.AutoTranslator/releases/download/"
        "v5.5.0/TMP_Font_AssetBundles_2025-12-08.7z"
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
    expected[FONT_ARCHIVE] = FONT_ARCHIVE_SHA256
    downloads = []
    for name, expected_hash in expected.items():
        destination = components_root / name
        status = _download_verified(COMPONENT_URLS[name], destination, expected_hash, proxy)
        downloads.append(
            {"name": name, "status": status, "sha256": expected_hash, "url": COMPONENT_URLS[name]}
        )

    font = font_root / FONT_BUNDLE
    if font.is_file() and sha256_file(font) != FONT_BUNDLE_SHA256:
        raise ValueError(f"Existing font has unexpected SHA-256: {font}")
    if not font.is_file():
        font_root.mkdir(parents=True, exist_ok=True)
        with py7zr.SevenZipFile(components_root / FONT_ARCHIVE, mode="r") as archive:
            archive.extract(path=font_root, targets=[FONT_BUNDLE])
    actual_font_hash = sha256_file(font)
    if actual_font_hash != FONT_BUNDLE_SHA256:
        raise ValueError(f"Extracted font SHA-256 mismatch: {actual_font_hash}")
    return {
        "downloads": downloads,
        "font": {"path": str(font.resolve()), "sha256": actual_font_hash},
    }
