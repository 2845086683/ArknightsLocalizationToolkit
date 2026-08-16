from __future__ import annotations

import argparse
import json
from pathlib import Path

from .components import prepare_official_components
from .doctor import doctor, print_doctor_report
from .extractor import extract_and_decode
from .mapping import build_translation_pack
from .repositories import update_public_repositories
from .runtime import install_runtime, stage_runtime, uninstall_runtime
from .scan import scan_client
from .util import PROJECT_ROOT, write_json
from .xunity import validate_translation_pack


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arklocalizer",
        description="Arknights EN/JP offline Simplified Chinese localization toolkit",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subcommands.add_parser(
        "doctor", help="Validate dependencies, schemas, archives, and an optional configured client"
    )
    doctor_parser.add_argument("--game-dir", type=_path)
    doctor_parser.add_argument("--locale", choices=("en", "jp", "cn", "selected"))

    scan = subcommands.add_parser("scan-client", help="Inspect a selected Arknights.exe and runtime state")
    scan.add_argument("--game-exe", type=_path, required=True)

    prepare = subcommands.add_parser(
        "prepare-components",
        help="Download and verify pinned official runtime components and the Unity 2021 font",
    )
    prepare.add_argument("--proxy", help="Optional HTTP(S) proxy, for example http://127.0.0.1:12343")
    prepare.add_argument(
        "--components",
        type=_path,
        default=PROJECT_ROOT / "cache" / "official-components",
    )
    prepare.add_argument(
        "--fonts",
        type=_path,
        default=PROJECT_ROOT / "cache" / "fonts",
    )

    update_data = subcommands.add_parser(
        "update-data",
        help="Clone or fast-forward public game-data, schema, unpacker, and XUnity repositories",
    )
    update_data.add_argument("--proxy", help="Optional HTTP(S) proxy")

    extract = subcommands.add_parser("extract-client", help="Extract and decode local PC-client TextAssets")
    extract.add_argument("--game-dir", type=_path, required=True)
    extract.add_argument("--output", type=_path, required=True)
    extract.add_argument("--scope", choices=("tables", "story", "all"), default="tables")
    extract.add_argument(
        "--schema-root",
        type=_path,
        default=PROJECT_ROOT / "vendor" / "ArknightsFlatbuffers" / "yostar",
    )
    extract.add_argument(
        "--flatc",
        type=_path,
        default=PROJECT_ROOT / "tools" / "flatc" / "flatc.exe",
    )

    build = subcommands.add_parser("build-pack", help="Build a collision-checked XUnity translation pack")
    build.add_argument("--locale", choices=("en", "jp"), required=True)
    build.add_argument(
        "--data-root",
        type=_path,
        default=PROJECT_ROOT / "cache" / "ArknightsGamedataMulti",
    )
    build.add_argument("--output", type=_path, required=True)
    build.add_argument(
        "--local-source-root",
        type=_path,
        help="Optional decoded local root containing gamedata/ and i18n/; public source fills gaps",
    )
    build.add_argument(
        "--local-story-root",
        type=_path,
        help="Optional decoded local root containing gamedata/story; public story is the fallback",
    )
    build.add_argument("--no-story", action="store_true")
    build.add_argument("--max-story-files", type=int)
    build.add_argument(
        "--allow-fuzzy-story",
        action="store_true",
        help="Also align story files whose command structure differs (less conservative)",
    )

    stage = subcommands.add_parser("stage-runtime", help="Assemble official BepInEx/XUnity and an offline pack")
    stage.add_argument("--locale", choices=("en", "jp"), required=True)
    stage.add_argument("--pack", type=_path, required=True)
    stage.add_argument("--output", type=_path, required=True)
    stage.add_argument(
        "--components",
        type=_path,
        default=PROJECT_ROOT / "cache" / "official-components",
    )
    stage.add_argument("--font", type=_path)

    validate = subcommands.add_parser("validate-pack", help="Strictly parse and validate a pack")
    validate.add_argument("--pack", type=_path, required=True)
    validate.add_argument("--report", type=_path)

    install = subcommands.add_parser("install", help="Plan or apply a staged runtime install")
    install.add_argument("--stage", type=_path, required=True)
    install.add_argument("--game-dir", type=_path, required=True)
    install.add_argument("--apply", action="store_true", help="Actually copy files; otherwise dry-run")
    install.add_argument("--report", type=_path)
    install.add_argument("--summary", action="store_true", help="Print counts instead of every action")

    uninstall = subcommands.add_parser("uninstall", help="Plan or apply safe manifest-based uninstall")
    uninstall.add_argument("--game-dir", type=_path, required=True)
    uninstall.add_argument("--apply", action="store_true")
    uninstall.add_argument("--report", type=_path)
    uninstall.add_argument("--summary", action="store_true", help="Print action counts only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        report = doctor(game_dir=args.game_dir, locale=args.locale)
        print_doctor_report(report)
        return 0 if report["ok"] else 2
    if args.command == "scan-client":
        report = scan_client(args.game_exe)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "prepare-components":
        report = prepare_official_components(
            args.components,
            args.fonts,
            proxy=args.proxy,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "update-data":
        report = update_public_repositories(PROJECT_ROOT, proxy=args.proxy or "")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "extract-client":
        report = extract_and_decode(
            args.game_dir,
            args.output,
            args.schema_root,
            args.flatc,
            scope=args.scope,
        )
        summary = {
            "extracted": report["extraction"]["text_asset_count"],
            "extract_errors": report["extraction"]["error_count"],
            "decoded": report["decoding"]["stats"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if not summary["extract_errors"] else 2
    if args.command == "build-pack":
        report = build_translation_pack(
            args.data_root,
            args.output,
            args.locale,
            include_story=not args.no_story,
            max_story_files=args.max_story_files,
            local_source_root=args.local_source_root,
            local_story_root=args.local_story_root,
            allow_fuzzy_story=args.allow_fuzzy_story,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "stage-runtime":
        report = stage_runtime(
            args.components,
            args.pack,
            args.output,
            args.locale,
            font_bundle=args.font,
        )
        print(json.dumps({"files": len(report["files"]), "font": report["font"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate-pack":
        report = validate_translation_pack(args.pack)
        if args.report:
            write_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    if args.command == "install":
        report = install_runtime(args.stage, args.game_dir, apply=args.apply)
        if args.report:
            write_json(args.report, report)
        output = report
        if args.summary:
            output = {
                key: report.get(key)
                for key in (
                    "game_directory",
                    "running_processes",
                    "create",
                    "replace",
                    "unchanged",
                    "applied",
                    "backup_root",
                )
                if key in report
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    if args.command == "uninstall":
        report = uninstall_runtime(args.game_dir, apply=args.apply)
        if args.report:
            write_json(args.report, report)
        output = report
        if args.summary:
            counts: dict[str, int] = {}
            for action in report["actions"]:
                name = action["action"]
                counts[name] = counts.get(name, 0) + 1
            output = {
                "game_directory": report["game_directory"],
                "running_processes": report["running_processes"],
                "applied": report["applied"],
                "counts": counts,
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
