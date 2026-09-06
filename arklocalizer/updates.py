"""Pinned Git dictionary updates and verified GitHub Release launcher updates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from . import __version__
from .libraries import (MANIFEST, METADATA, REPOSITORY, TRANSLATIONS, Library, discover, identify,
                        read_manifest, relative_path, safe_child, translation_entries, verify,
                        official_id, official_relative, migrate_legacy)
from .util import sha256_file, write_json

DEFAULT_MIRROR = "https://gh-proxy.com/"
EXE_NAME = "明日方舟汉化启动器.exe"


def normalize_mirror(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("镜像地址必须是 HTTPS 前缀，例如 https://gh-proxy.com/")
    return value.strip().rstrip("/") + "/"


class GitHub:
    def __init__(self, config, *, cancelled=lambda: False, log=lambda _: None):
        self.mirror = normalize_mirror(config.mirror_url) if config.update_source == "mirror" else ""
        self.cancelled, self.log = cancelled, log
        self.proxy_mode = config.update_proxy_mode
        if self.proxy_mode == "custom" and not config.proxy:
            raise ValueError("仅使用填写代理模式需要填写代理地址")
        self.proxies = ({} if self.proxy_mode == "direct" else
                        {"http": config.proxy, "https": config.proxy} if config.proxy else urllib.request.getproxies())
        # Passing an explicit empty handler is essential: build_opener() alone
        # silently reloads environment/Windows proxies, including stopped local proxies.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler(self.proxies))
        self.direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.using_direct = self.proxy_mode == "direct"
        self.using_contents_api = False

    def _route(self, proxy=None):
        if proxy:
            parsed = urllib.parse.urlsplit(proxy)
            return f"代理 {parsed.hostname}:{parsed.port or 80}"
        return "镜像站 " + urllib.parse.urlsplit(self.mirror).hostname if self.mirror else "GitHub"

    def _failure(self, error, proxy=None):
        reason = getattr(error, "reason", error)
        if isinstance(reason, ssl.SSLError):
            detail = "TLS/证书校验失败，请检查系统时间和代理证书"
        elif isinstance(reason, TimeoutError):
            detail = "连接超时"
        elif isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) == 10061:
            detail = "连接被拒绝，目标服务可能未运行"
        elif isinstance(reason, socket.gaierror):
            detail = "无法解析服务器地址，请检查 DNS 或更新来源"
        else:
            detail = "网络连接失败"
        hint = "请启动代理或在更新设置中选择自动连接/不使用代理。" if proxy else "请检查网络，或切换更新来源/配置可用代理。"
        return RuntimeError(f"{self._route(proxy)}：{detail}。{hint}")

    def open(self, url):
        contents_url = self._contents_url(url)
        if contents_url and self.using_contents_api:
            return self._open(contents_url)
        try:
            return self._open(url)
        except RuntimeError as error:
            cause = error.__cause__
            reason = getattr(cause, "reason", cause)
            if (not contents_url or not isinstance(cause, (urllib.error.URLError, OSError))
                    or isinstance(reason, ssl.SSLError) or self.cancelled()):
                raise
            self.log("原始文件地址连接失败，改用 GitHub 文件接口读取同一 Git 提交…")
            response = self._open(contents_url)
            self.using_contents_api = True
            return response

    @staticmethod
    def _contents_url(url):
        prefix = f"https://raw.githubusercontent.com/{REPOSITORY}/"
        if not url.startswith(prefix): return None
        commit, separator, path = url[len(prefix):].partition('/')
        if not separator or not re.fullmatch(r'[0-9a-f]{40}', commit): return None
        return f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={commit}"

    def _open(self, url):
        if self.cancelled(): raise RuntimeError("更新已取消")
        host = urllib.parse.urlsplit(url).hostname
        if host not in {"api.github.com", "raw.githubusercontent.com", "github.com"}:
            raise ValueError("更新地址必须来自 GitHub")
        headers = {"User-Agent": "ArkLocalizer/" + __version__}
        if url.startswith(f"https://api.github.com/repos/{REPOSITORY}/contents/"):
            headers['Accept'] = 'application/vnd.github.raw+json'
        request = urllib.request.Request(self.mirror + url, headers=headers)
        proxy = None if self.using_direct else self.proxies.get("https")
        opener = self.direct_opener if self.using_direct else self.opener
        try:
            return opener.open(request, timeout=20)
        except urllib.error.HTTPError:
            raise  # Authentication, rate limits and missing files are not proxy failures.
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            if proxy and self.proxy_mode == "auto" and not isinstance(reason, ssl.SSLError):
                if self.cancelled(): raise RuntimeError("更新已取消")
                self.log(f"{self._route(proxy)}连接失败，正在不使用代理重试当前更新来源…")
                try:
                    # ProxyHandler mutates Request.host / tunnel state even
                    # when connecting fails. A fresh request must be used here.
                    retry_request = urllib.request.Request(self.mirror + url, headers=headers)
                    response = self.direct_opener.open(retry_request, timeout=20)
                except urllib.error.HTTPError:
                    raise
                except (urllib.error.URLError, OSError) as retry_error:
                    raise RuntimeError(f"{self._route(proxy)}连接失败；{self._failure(retry_error)}") from retry_error
                self.using_direct = True
                self.log("重试成功，本次更新将继续不使用代理；已保存的代理配置保持不变。")
                return response
            raise self._failure(error, proxy) from error

    def json(self, url, *, optional=False):
        try:
            with self.open(url) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024: raise ValueError("更新清单过大")
            return json.loads(data)
        except urllib.error.HTTPError as error:
            if optional and error.code == 404: return None
            hint = "请求次数达到限制，请稍后重试或切换来源" if error.code in {403, 429} else "请检查更新来源或镜像地址"
            raise RuntimeError(f"{self._route()} 返回 HTTP {error.code}，{hint}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError(f"{self._route()} 未返回有效更新清单，请检查镜像或代理是否返回了网页") from error

    def download(self, url: str, destination: Path, size: int, digest: str):
        if not isinstance(size, int) or not 0 <= size <= 2 * 1024**3 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("更新文件缺少有效的大小或 SHA-256 校验值")
        destination.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        sha = hashlib.sha256()
        with self.open(url) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                if self.cancelled(): raise RuntimeError("更新已取消")
                count += len(chunk)
                if count > size: raise ValueError("下载大小超出清单，已停止")
                sha.update(chunk)
                output.write(chunk)
        if count != size or sha.hexdigest() != digest:
            raise ValueError("下载校验失败，现有版本未修改")


def raw_url(commit: str, path: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", commit): raise ValueError("Git 版本标识无效")
    relative_path(path)
    return f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/" + urllib.parse.quote(path, safe="/")


def version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match: raise ValueError(f"不支持的软件版本号：{value}")
    return tuple(map(int, match.groups()))


def check_updates(project: Path, config, *, force=False, client=None) -> dict:
    client = client or GitHub(config)
    result = {"libraries": [], "software": None, "errors": []}
    if force or config.check_library_updates:
        try:
            repo = client.json(f"https://api.github.com/repos/{REPOSITORY}")
            ref = urllib.parse.quote(repo["default_branch"], safe="")
            commit = client.json(f"https://api.github.com/repos/{REPOSITORY}/commits/{ref}")["sha"]
            for library in migrate_legacy(project, config):
                if library.repository != REPOSITORY or not library.remote_path: continue
                remote_path = library.remote_path
                if library.remote_path in {official_relative(library.locale), f"outputs/runtime/{library.locale}-zh-offline-final"}:
                    remote_path = official_relative(library.locale)
                    manifest = client.json(raw_url(commit, remote_path + "/" + MANIFEST), optional=True)
                    if manifest is None:
                        # Older Git commits/releases still contain the old layout.
                        remote_path = f"outputs/runtime/{library.locale}-zh-offline-final"
                        manifest = client.json(raw_url(commit, remote_path + "/" + MANIFEST))
                else:
                    manifest = client.json(raw_url(commit, remote_path + "/" + MANIFEST))
                if manifest.get("source_locale") != library.locale: raise ValueError("远程词库区服不匹配")
                remote = translation_entries(manifest)
                for entry in remote: safe_child(library.path, entry["path"])
                local = translation_entries(read_manifest(library.path))
                signature = lambda entries: sorted((e["path"], e["sha256"], e["bytes"]) for e in entries)
                result["libraries"].append({"id": library.id, "name": library.name, "locale": library.locale,
                    "commit": commit, "manifest": manifest, "remote_path": remote_path,
                    "available": signature(remote) != signature(local)})
        except Exception as error:
            result["errors"].append(f"词库检查失败：{error}")
    if force or config.check_software_updates:
        try:
            release = client.json(f"https://api.github.com/repos/{REPOSITORY}/releases/latest", optional=True)
            if release:
                assets = [a for a in release.get("assets", []) if a["name"].startswith("ArknightsLocalizationToolkit") and a["name"].endswith(".zip")]
                result["software"] = {"version": release["tag_name"], "notes": release.get("body", ""),
                    "url": release["html_url"], "asset": assets[0] if len(assets) == 1 else None,
                    "available": version_key(release["tag_name"]) > version_key(__version__)}
        except Exception as error:
            result["errors"].append(f"软件检查失败：{error}")
    return result


def update_library(library: Library, update: dict, client: GitHub) -> Library:
    if update["id"] != library.id or update["locale"] != library.locale:
        raise ValueError("更新目标与所选词库不一致")
    if update["manifest"].get("source_locale") != library.locale:
        raise ValueError("远程词库区服不匹配")
    verify(library.path)
    manifest = read_manifest(library.path)
    remote = translation_entries(update["manifest"])
    staging = library.path.with_name(".update-" + uuid.uuid4().hex)
    backup = library.path.parent / ".backups" / f"{library.id}-{uuid.uuid4().hex}"
    try:
        shutil.copytree(library.path, staging)
        for entry in translation_entries(manifest):
            safe_child(staging, entry["path"]).unlink()
        for entry in remote:
            client.log(f"下载 {library.locale.upper()} / {Path(entry['path']).name}")
            client.download(raw_url(update["commit"], update["remote_path"] + "/" + entry["path"]),
                            safe_child(staging, entry["path"]), entry["bytes"], entry["sha256"])
        previous_text = translation_entries(manifest)
        manifest["files"] = [entry for entry in manifest["files"] if entry not in previous_text] + remote
        write_json(staging / MANIFEST, manifest)
        meta = json.loads((staging / METADATA).read_text(encoding="utf-8-sig"))
        meta["commit"] = update["commit"]
        write_json(staging / METADATA, meta)
        verify(staging)
        if client.cancelled(): raise RuntimeError("更新已取消")
        backup.parent.mkdir(parents=True, exist_ok=True)
        os.replace(library.path, backup)
        try: os.replace(staging, library.path)
        except BaseException:
            os.replace(backup, library.path)
            raise
    finally:
        if staging.exists(): shutil.rmtree(staging)
    return identify(library.path)


def extract_release(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as package:
        files = package.infolist()
        if sum(item.file_size for item in files) > 4 * 1024**3: raise ValueError("发布包解压体积超出限制")
        seen = set()
        for item in files:
            target = safe_child(destination, item.filename.rstrip("/"))
            key = str(target).casefold()
            if key in seen or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("发布包包含重复路径或符号链接")
            seen.add(key)
        candidates = [item for item in files if Path(item.filename).name == EXE_NAME]
        if len(candidates) != 1: raise ValueError("发布包中未找到唯一的启动器程序")
        # Remaining release contents are staged separately, never over live files.
        item = candidates[0]
        if item.file_size > 256 * 1024**2: raise ValueError("启动器文件大小异常")
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / EXE_NAME
        with package.open(item) as source, target.open("wb") as output: shutil.copyfileobj(source, output)
        if target.read_bytes()[:2] != b"MZ": raise ValueError("发布包启动器不是 Windows 程序")
        return target


def prepare_software_update(project: Path, release: dict, client: GitHub) -> Path:
    asset = release.get("asset")
    if not asset or not str(asset.get("digest", "")).startswith("sha256:"):
        raise ValueError("此 Release 没有唯一的完整软件包或 SHA-256，无法自动安装；请打开发布页面")
    url = asset["browser_download_url"]
    if not url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):
        raise ValueError("发布文件不属于当前 Origin")
    directory = project / "work/updates" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    archive = directory / "release.zip"
    try:
        client.log("正在下载软件发布包…")
        client.download(url, archive, asset["size"], asset["digest"].split(":", 1)[1])
        candidate = extract_release(archive, directory / "verified")
        client.log("发布包校验通过，正在准备完整软件与官方词库；保留自建词库…")
        actions = stage_release_runtimes(project, archive, directory, client.cancelled)
        write_json(candidate.parent / "runtime-plan.json", actions)
        if client.cancelled(): raise RuntimeError("更新已取消")
        return candidate
    except BaseException:
        if directory.resolve().parent == (project / 'work/updates').resolve(): shutil.rmtree(directory)
        raise


# Local state is never replaced by release contents. Libraries are handled by ID.
PRESERVED_RELEASE_ROOTS = {"runtime", "outputs", "output", "词库", "work", "cache", "vendor",
                           ".git", ".conda-env", ".pytest_cache", "__pycache__", "launcher.json"}


def stage_release_runtimes(project: Path, archive: Path, directory: Path, cancelled=lambda: False) -> list[dict]:
    """Stage complete official libraries and release code; leave custom libraries untouched."""
    actions = []
    project = project.resolve()
    libraries = []
    for path in (project / 'runtime').glob('*/' + METADATA):
        if path.parent.name.startswith('.'): continue
        meta = json.loads(path.read_text(encoding='utf-8-sig'))
        if meta.get('locale') in {'en', 'jp'} and meta.get('id') == official_id(meta['locale']):
            libraries.append(identify(path.parent))

    def action(target, staging, *, locale=None):
        target = safe_child(project, target.relative_to(project).as_posix())
        files = ([staging] if staging.is_file() else sorted(p for p in staging.rglob('*') if p.is_file()))
        actions.append(dict(target=str(target), staging=str(staging.resolve()),
            backup=str((directory / ('backup-' + uuid.uuid4().hex)).resolve()),
            kind='file' if staging.is_file() else 'directory', locale=locale,
            files=[dict(path=p.relative_to(staging).as_posix() if staging.is_dir() else '',
                        bytes=p.stat().st_size, sha256=sha256_file(p)) for p in files]))

    with zipfile.ZipFile(archive) as package:
        executable = next(i for i in package.infolist() if Path(i.filename).name == EXE_NAME)
        package_root = executable.filename[:-len(EXE_NAME)]
        entries = [i for i in package.infolist() if not i.is_dir() and i.filename.startswith(package_root)]

        def unpack(item, staging, relative):
            if cancelled(): raise RuntimeError("更新已取消")
            if item.file_size > 512 * 1024**2: raise ValueError("发布文件大小异常")
            path = safe_child(staging, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as src, path.open('wb') as dst: shutil.copyfileobj(src, dst)

        manifests = [i for i in entries if re.fullmatch(
            r'(?:runtime|outputs/runtime|词库)/[^/]+/' + MANIFEST, i.filename[len(package_root):])]
        by_locale = {}
        for item in sorted(manifests, key=lambda i: (not i.filename[len(package_root):].startswith('runtime/'), i.filename)):
            if item.file_size > 4 * 1024**2: raise ValueError("发布包运行时清单过大")
            manifest = json.loads(package.read(item))
            locale = manifest.get('source_locale')
            if locale not in {'en', 'jp'}: continue
            if item.filename.rsplit('/', 2)[-2] not in {f'{locale}-zh-offline-final', official_id(locale)}: continue
            by_locale.setdefault(locale, item.filename.rsplit('/', 1)[0] + '/')
        if set(by_locale) != {'en', 'jp'}:
            raise ValueError("此 Release 缺少双服完整官方词库，请从发布页面下载完整软件包")
        for locale, prefix in by_locale.items():
            staging = directory / ('runtime-' + uuid.uuid4().hex)
            for item in entries:
                if item.filename.startswith(prefix): unpack(item, staging, item.filename[len(prefix):])
            incoming = verify(staging)
            translation_entries(incoming)  # Software releases must now include their official words.
            current = next((x for x in libraries if x.id == official_id(locale)), None)
            target = current.path if current else project / official_relative(locale)
            if current is None and target.exists():
                # A custom library may occupy the conventional official folder.
                target = project / 'runtime' / official_id(locale)
                if target.exists(): raise ValueError("官方词库目标位置已被其它文件占用")
            meta_path = staging / METADATA
            meta = json.loads(meta_path.read_text(encoding='utf-8-sig')) if meta_path.exists() else {}
            meta.update(schema=1, id=official_id(locale), locale=locale, repository=REPOSITORY,
                        remote_path=official_relative(locale))
            meta.setdefault('name', current.name if current else f"官方中文词库（{locale.upper()}）")
            write_json(meta_path, meta)
            action(target, staging, locale=locale)

        # Replace each supplied software directory as a unit, removing obsolete
        # modules while preserving unrelated local state and every custom library.
        for name in sorted({i.filename[len(package_root):].split('/')[0] for i in entries}):
            if name == EXE_NAME or name.casefold() in {x.casefold() for x in PRESERVED_RELEASE_ROOTS}: continue
            staging = directory / ('release-' + uuid.uuid4().hex)
            selected = [i for i in entries if i.filename[len(package_root):].split('/')[0] == name]
            for item in selected: unpack(item, staging, item.filename[len(package_root):])
            action(project / name, staging / name)
    return actions


def schedule_software_install(project: Path, candidate: Path) -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError(f"源码模式不替换正在运行的 Python；已校验的新启动器位于：{candidate}")
    target = Path(sys.executable).resolve()
    if target.parent != project.resolve() or not candidate.resolve().is_relative_to((project / "work/updates").resolve()):
        raise ValueError("软件更新目录校验失败")
    plan = candidate.parent / "install.json"
    actions_path = candidate.parent / "runtime-plan.json"
    actions = json.loads(actions_path.read_text(encoding="utf-8")) if actions_path.exists() else []
    write_json(plan, {"pid": os.getpid(), "parent_pid": os.getppid(), "root": str(project.resolve()), "candidate": str(candidate.resolve()),
                     "target": str(target), "sha256": sha256_file(candidate), "runtimes": actions})
    helper = candidate.parent / "install.ps1"
    helper.write_text(INSTALL_SCRIPT, encoding="utf-8-sig")
    powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-File", str(helper), "-PlanPath", str(plan)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return plan


INSTALL_SCRIPT = r'''param([string]$PlanPath, [switch]$NoRestart)
$ErrorActionPreference = 'Stop'
function FileDigest([string]$filePath) {
  $stream = [IO.File]::OpenRead($filePath)
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
  finally { $algorithm.Dispose(); $stream.Dispose() }
}
$p = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$rootPath = [IO.Path]::GetFullPath($p.root).TrimEnd('\')
$targetPath = [IO.Path]::GetFullPath($p.target)
$candidatePath = [IO.Path]::GetFullPath($p.candidate)
$backupPath = $targetPath + '.previous'
$installed = [Collections.Generic.List[object]]::new()
$exeInstalled = $false
try {
  if ([IO.Path]::GetDirectoryName($targetPath) -ne $rootPath -or -not $candidatePath.StartsWith($rootPath + '\work\updates\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid update paths' }
  if ((FileDigest $candidatePath) -ne $p.sha256) { throw 'Update hash mismatch' }
  Wait-Process -Id $p.pid -Timeout 120 -ErrorAction SilentlyContinue
  if (Get-Process -Id $p.pid -ErrorAction SilentlyContinue) { throw 'Launcher is still running' }
  # PyInstaller one-file has a bootloader parent that can still hold the EXE.
  if ($p.parent_pid) {
    $parentLauncher = Get-Process -Id $p.parent_pid -ErrorAction SilentlyContinue
    if ($parentLauncher -and $parentLauncher.Path -eq $targetPath) {
      Wait-Process -Id $p.parent_pid -Timeout 120 -ErrorAction SilentlyContinue
      if (Get-Process -Id $p.parent_pid -ErrorAction SilentlyContinue) { throw 'Launcher bootloader is still running' }
    }
  }
  foreach ($action in $p.runtimes) {
    $dest = [IO.Path]::GetFullPath($action.target)
    $stage = [IO.Path]::GetFullPath($action.staging)
    $backup = [IO.Path]::GetFullPath($action.backup)
    if (-not $stage.StartsWith($rootPath + '\work\updates\', [StringComparison]::OrdinalIgnoreCase) -or -not $backup.StartsWith($rootPath + '\work\updates\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid staging paths' }
    if ($action.locale) {
      if ([IO.Path]::GetDirectoryName($dest) -ne ($rootPath + '\runtime') -or $action.kind -ne 'directory') { throw 'Invalid library destination' }
      $expectedId = switch ($action.locale) { 'en' { 'a25e07ca-a482-5555-b750-ea0b9536f68b' } 'jp' { '203cbbd9-3c99-5849-ba7d-978208397999' } default { throw 'Invalid locale' } }
      $meta = Get-Content -LiteralPath (Join-Path $stage 'WORDLIBRARY.json') -Raw -Encoding UTF8 | ConvertFrom-Json
      if ($meta.id -ne $expectedId -or $meta.locale -ne $action.locale) { throw 'Invalid official library' }
      if (Test-Path -LiteralPath $dest) {
        $currentMeta = Get-Content -LiteralPath (Join-Path $dest 'WORDLIBRARY.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($currentMeta.id -ne $expectedId -or $currentMeta.locale -ne $action.locale) { throw 'Refusing to replace custom library' }
      }
    } else {
      $name = [IO.Path]::GetFileName($dest)
      if ([IO.Path]::GetDirectoryName($dest) -ne $rootPath -or $dest -eq $targetPath -or $name -in @('runtime','outputs','output','词库','work','cache','vendor','.git','.conda-env','.pytest_cache','__pycache__','launcher.json')) { throw 'Invalid software destination' }
    }
    if ($action.kind -notin @('file','directory')) { throw 'Invalid update entry kind' }
    foreach ($entry in $action.files) {
      $entryPath = if ($action.kind -eq 'file') { $stage } else { [IO.Path]::GetFullPath((Join-Path $stage $entry.path)) }
      if ($action.kind -eq 'directory' -and -not $entryPath.StartsWith($stage + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid release file path' }
      if ((Get-Item -LiteralPath $entryPath).Length -ne $entry.bytes -or (FileDigest $entryPath) -ne $entry.sha256) { throw 'Release file hash mismatch' }
    }
  }
  foreach ($action in $p.runtimes) {
    $existed = Test-Path -LiteralPath $action.target
    $action | Add-Member -NotePropertyName existed -NotePropertyValue $existed -Force
    if ($existed) { Move-Item -LiteralPath $action.target -Destination $action.backup }
    try {
      [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($action.target)) | Out-Null
      Move-Item -LiteralPath $action.staging -Destination $action.target
    } catch {
      if ($existed) { Move-Item -LiteralPath $action.backup -Destination $action.target }
      throw
    }
    $installed.Add($action)
  }
  [IO.File]::Replace($candidatePath, $targetPath, $backupPath, $true)
  $exeInstalled = $true
  if (-not $NoRestart) { Start-Process -FilePath $targetPath -WorkingDirectory $rootPath -WindowStyle Hidden }
  'Software update installed' | Set-Content -LiteralPath ($PlanPath + '.log') -Encoding UTF8
} catch {
  $failure = $_ | Out-String
  if (-not $exeInstalled) {
    for ($index = $installed.Count - 1; $index -ge 0; $index--) {
      $action = $installed[$index]
      Move-Item -LiteralPath $action.target -Destination $action.staging
      if ($action.existed) { Move-Item -LiteralPath $action.backup -Destination $action.target }
    }
  }
  $failure | Set-Content -LiteralPath ($PlanPath + '.log') -Encoding UTF8
}
'''
