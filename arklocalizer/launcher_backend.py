from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .scan import scan_client, validate_game_executable
from .util import PROJECT_ROOT, write_json
from .xunity import DEFAULT_TMP_FONT


APP_NAME = "ArknightsLocalizationToolkit"


@dataclass
class LauncherConfig:
    game_executable: str = ""
    locale: str = "en"
    proxy: str = ""
    update_repositories: bool = True
    last_runtime: str = ""
    selected_libraries: dict[str, str] = field(default_factory=dict)
    check_library_updates: bool = True
    check_software_updates: bool = True
    update_source: str = "github"
    update_proxy_mode: str = "auto"
    mirror_url: str = "https://gh-proxy.com/"

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "LauncherConfig":
        if not isinstance(value, dict):
            raise ValueError("启动器配置必须是 JSON 对象")
        selections = value.get("selected_libraries", {})
        if not isinstance(selections, dict): selections = {}
        locale = str(value.get("locale", "en")).casefold()
        if locale not in {"en", "jp"}:
            locale = "en"
        return cls(
            game_executable=str(value.get("game_executable", "")),
            locale=locale,
            proxy=str(value.get("proxy", "")),
            update_repositories=bool(value.get("update_repositories", True)),
            last_runtime=str(value.get("last_runtime", "")),
            selected_libraries={k: str(v) for k, v in selections.items() if k in {"en", "jp"}},
            check_library_updates=bool(value.get("check_library_updates", True)),
            check_software_updates=bool(value.get("check_software_updates", True)),
            update_source="mirror" if value.get("update_source") == "mirror" else "github",
            update_proxy_mode=value.get("update_proxy_mode") if value.get("update_proxy_mode") in {"auto", "direct", "custom"} else "auto",
            mirror_url=str(value.get("mirror_url", "https://gh-proxy.com/")),
        )


def project_root() -> Path:
    if configured := os.environ.get("ARKLOCALIZER_HOME"):
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def config_path() -> Path:
    if configured := os.environ.get("ARKLOCALIZER_CONFIG"):
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_NAME / "launcher.json"


def load_config(path: Path | None = None) -> LauncherConfig:
    path = path or config_path()
    defaults = LauncherConfig(
        game_executable=os.environ.get("ARKLOCALIZER_GAME_EXE", ""),
        locale=os.environ.get("ARKLOCALIZER_LOCALE", "en").casefold(),
        proxy=os.environ.get("ARKLOCALIZER_PROXY", ""),
    )
    if not path.is_file():
        return defaults
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
        stored = LauncherConfig.from_mapping(values)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return defaults
    if not stored.game_executable:
        stored.game_executable = defaults.game_executable
    if "proxy" not in values:
        stored.proxy = defaults.proxy
    return stored


def save_config(config: LauncherConfig, path: Path | None = None) -> Path:
    path = path or config_path()
    normalize_proxy(config.proxy)
    from .updates import normalize_mirror
    normalize_mirror(config.mirror_url)
    if config.update_proxy_mode not in {"auto", "direct", "custom"}:
        raise ValueError("更新代理模式无效")
    if config.update_proxy_mode == "custom" and not config.proxy.strip():
        raise ValueError("仅使用填写代理模式需要填写代理地址")
    if config.locale not in {"en", "jp"}:
        raise ValueError("服务器区域必须是 en 或 jp")
    write_json(path, {"schema": 1, **asdict(config)})
    return path


def normalize_proxy(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("代理格式应类似 http://127.0.0.1:7890；留空则使用系统配置")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("代理端口超出范围")
    return value


def infer_locale(executable: Path) -> str | None:
    value = str(executable).casefold()
    if "arknights_jp" in value or "yostar_jp" in value:
        return "jp"
    if "arknights_en" in value or "yostar_en" in value:
        return "en"
    return None


def configured_game(config: LauncherConfig) -> tuple[Path, Path]:
    if not config.game_executable:
        raise FileNotFoundError("尚未选择 Arknights.exe")
    return validate_game_executable(Path(config.game_executable))


def runtime_for(project: Path, config: LauncherConfig) -> Path:
    from .libraries import migrate_legacy, official_id
    libraries = [x for x in migrate_legacy(project, config) if x.locale == config.locale]
    selected = config.selected_libraries.get(config.locale)
    if selected:
        match = next((item for item in libraries if item.id == selected), None)
        if match is None: raise FileNotFoundError("所选词库已移动或区服不匹配，请重新选择词库")
        return match.path
    if libraries: return next((x.path for x in libraries if x.id == official_id(config.locale)), libraries[0].path)
    candidates: list[Path] = []
    if config.last_runtime:
        candidates.append(Path(config.last_runtime))
    for candidate in candidates:
        manifest = candidate / "ARKLOCALIZER_MANIFEST.json"
        if not manifest.is_file():
            continue
        try:
            locale = json.loads(manifest.read_text(encoding="utf-8")).get("source_locale")
        except (OSError, json.JSONDecodeError):
            continue
        if locale == config.locale:
            return candidate.resolve()
    raise FileNotFoundError(f"发布包中缺少 {config.locale.upper()} 的内置运行时，请重新解压完整发布包")


def command_environment(config: LauncherConfig, project: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["ARKLOCALIZER_HOME"] = str(project)
    environment["ARKLOCALIZER_GAME_EXE"] = config.game_executable
    environment["ARKLOCALIZER_LOCALE"] = config.locale
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if proxy := normalize_proxy(config.proxy):
        environment["ARKLOCALIZER_PROXY"] = proxy
        environment["HTTP_PROXY"] = proxy
        environment["HTTPS_PROXY"] = proxy
    return environment


def python_executable(project: Path) -> Path:
    return project / ".conda-env" / "python.exe"


def cli_command(project: Path, *arguments: str) -> list[str]:
    python = python_executable(project)
    if not python.is_file():
        raise FileNotFoundError(f"构建环境不存在，请先点击“初始化构建环境”：{python}")
    return [str(python), "-m", "arklocalizer.cli", *arguments]


def setup_commands(project: Path, config: LauncherConfig) -> list[tuple[str, list[str]]]:
    """Return a complete, script-free initialization command sequence."""
    python = python_executable(project)
    proxy = normalize_proxy(config.proxy)
    commands: list[tuple[str, list[str]]] = []
    if not python.is_file():
        # Prefer the real executable.  ``conda`` often resolves to conda.bat on
        # Windows, which cannot be launched reliably by every frozen Python
        # runtime without introducing an extra shell/quoting layer.
        conda = shutil.which("conda.exe") or shutil.which("conda")
        if conda is None:
            raise FileNotFoundError("未找到 conda；请先安装 Anaconda 或 Miniconda 并重新打开启动器")
        commands.append(
            ("创建 Python 3.12 构建环境", [conda, "create", "--yes", "--prefix", str(project / ".conda-env"), "python=3.12"])
        )
    pip = [str(python), "-m", "pip", "install", "--requirement", str(project / "requirements.txt")]
    if proxy:
        pip += ["--proxy", proxy]
    commands.append(("安装/更新 Python 依赖", pip))
    prepare = [str(python), "-m", "arklocalizer.cli", "prepare-components"]
    update = [str(python), "-m", "arklocalizer.cli", "update-data"]
    if proxy:
        prepare += ["--proxy", proxy]
        update += ["--proxy", proxy]
    commands.extend(
        (
            ("下载并校验固定版本运行组件", prepare),
            ("初始化/更新公开数据与 Schema", update),
            ("检查构建环境", [str(python), "-m", "arklocalizer.cli", "doctor"]),
        )
    )
    return commands


def rebuild_commands(
    project: Path,
    config: LauncherConfig,
    *,
    include_repository_update: bool,
) -> tuple[Path, list[tuple[str, list[str]]]]:
    _, game_dir = configured_game(config)
    python = python_executable(project)
    if not python.is_file():
        raise FileNotFoundError("构建环境不存在，请先点击“初始化构建环境”")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    build_root = project / "work" / "builds" / f"{config.locale}-{stamp}"
    tables = build_root / "client-tables"
    story = build_root / "client-story"
    pack = build_root / "pack"
    from .libraries import library_root
    runtime = library_root(project) / f"{config.locale}-{stamp}"
    report = build_root / "pack-validation.json"
    font = project / "cache" / "fonts" / DEFAULT_TMP_FONT

    proxy = normalize_proxy(config.proxy)
    prepare = cli_command(project, "prepare-components")
    if proxy:
        prepare += ["--proxy", proxy]
    commands: list[tuple[str, list[str]]] = [("校验固定版本运行组件", prepare)]
    required_repositories = (
        project / "cache" / "ArknightsGamedataMulti" / ".git",
        project / "vendor" / "ArknightsFlatbuffers" / ".git",
        project / "vendor" / "Ark-Unpacker" / ".git",
    )
    if include_repository_update or not all(path.is_dir() for path in required_repositories):
        update = cli_command(project, "update-data")
        if proxy:
            update += ["--proxy", proxy]
        commands.append(("更新多服词表、Schema 与解析参考", update))
    commands.extend(
        (
            (
                "扫描并提取客户端数据表",
                cli_command(project, "extract-client", "--game-dir", str(game_dir), "--scope", "tables", "--output", str(tables)),
            ),
            (
                "扫描并提取客户端剧情",
                cli_command(project, "extract-client", "--game-dir", str(game_dir), "--scope", "story", "--output", str(story)),
            ),
            (
                "生成冲突过滤后的中文词表",
                cli_command(
                    project,
                    "build-pack",
                    "--locale",
                    config.locale,
                    "--local-source-root",
                    str(tables / "decoded" / "dyn"),
                    "--local-story-root",
                    str(story / "decoded" / "dyn"),
                    "--output",
                    str(pack),
                ),
            ),
            ("严格校验词表", cli_command(project, "validate-pack", "--pack", str(pack), "--report", str(report))),
            (
                "封装离线汉化运行时",
                cli_command(
                    project,
                    "stage-runtime",
                    "--locale",
                    config.locale,
                    "--pack",
                    str(pack),
                    "--font",
                    str(font),
                    "--output",
                    str(runtime),
                ),
            ),
        )
    )
    return runtime, commands


def record_runtime(project: Path, config: LauncherConfig, runtime: Path) -> None:
    from .libraries import import_library
    library = import_library(project, runtime)
    config.selected_libraries[config.locale] = library.id
    runtime = library.path
    runtime = runtime.resolve()
    pointer = project / "work" / f"current-{config.locale}-runtime.txt"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(pointer.suffix + ".tmp")
    temporary.write_text(str(runtime) + "\n", encoding="utf-8")
    os.replace(temporary, pointer)
    config.last_runtime = str(runtime)
    save_config(config)


def launch_game(config: LauncherConfig) -> subprocess.Popen[bytes]:
    executable, game_dir = configured_game(config)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([str(executable)], cwd=game_dir, creationflags=flags)


def scan_configured_client(config: LauncherConfig) -> dict[str, Any]:
    try:
        default_runtime = runtime_for(project_root(), config)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = scan_client(Path(config.game_executable))
        result["translation_pack"] = {
            "checked": False,
            "reason": "default_runtime_unavailable",
            "error": str(error),
            "update_available": False,
        }
        return result
    return scan_client(Path(config.game_executable), default_runtime=default_runtime)
