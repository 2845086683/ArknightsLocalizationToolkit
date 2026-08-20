from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .story import collect_story_pairs
from .util import (
    contains_han,
    field_is_probably_identifier,
    looks_like_source_language,
    normalize_lookup_text,
    placeholder_counter,
    read_json,
    RICH_TEXT_TAG_RE,
    walk_paired,
    write_json,
)
from .xunity import write_translation_file


CATEGORY_PRIORITY = {"i18n": 10, "tables": 20, "story": 30}
TABLE_ROOT_WRAPPERS = {
    "battle_equip_table.json": "equips",
    "chapter_table.json": "chapters",
    "char_master_table.json": "master_data_bundles",
    "character_table.json": "characters",
    "charm_table.json": "charmList",
    "handbook_team_table.json": "handbook_teams",
    "init_text.json": "strings",
    "main_text.json": "strings",
    "replicate_table.json": "replications",
    "skill_table.json": "skills",
    "story_review_table.json": "story_reviews",
    "story_table.json": "stories",
    "token_table.json": "characters",
}
DISPLAY_TAG_RE = RICH_TEXT_TAG_RE
SKILL_PLACEHOLDER_RE = re.compile(r"\{(?P<token>[^{}\r\n]+)\}")

# A few labels on the operator screens are compiled into the client rather
# than present as standalone values in string_map.txt.  Keep this deliberately
# small and terminology-only; authoritative table text still comes from the
# matched CN game data.
BUILTIN_OVERRIDES: dict[str, dict[str, str]] = {
    "jp": {
        "カスタム": "自定义",
        "スキル": "技能",
        "レア度": "稀有度",
        "職業": "职业",
        "詳細": "详情",
        # These short stage-screen labels are compiled TMP strings and do not
        # exist as standalone values in the JP data dump. They are
        # context-free official UI terminology, so keeping them here also
        # avoids unsafe guesses from event-specific table collisions.
        "自動指揮": "代理指挥",
        "強襲作戦": "突袭作战",
        "敵情報": "敌情信息",
        "地図情報": "地图信息",
        "演習": "演习",
        "期間限定": "限时",
        # ``init_text`` contains several context-specific targets for reset;
        # use the neutral label so XUnity's context-free lookup remains safe.
        "リセット": "重置",
        # This Yostar account button has no same-key counterpart in the CN
        # table, but its standalone meaning is unambiguous.
        "アカウント管理": "账号管理",
        "昇進段階1開放": "精英阶段1解锁",
        "昇進段階1強化": "精英阶段1强化",
        "昇進段階2開放": "精英阶段2解锁",
        "昇進段階2強化": "精英阶段2强化",
        "昇進段階2に昇進後解放": "精英阶段2后解锁",
    },
    "en": {
        # Operator-detail labels and deployment tags are context-free TMP
        # strings in the PC client. Several collide with unrelated official
        # uses (for example enemy attack type "Ranged"), so the generic
        # collision filter intentionally drops them unless this UI-specific
        # terminology override is present.
        "Attributes": "属性",
        "Trust": "信赖值",
        "Melee": "近战位",
        "Ranged": "远程位",
        # PC settings contain several compiled labels that are absent from
        # the EN/CN data tables. Keep only context-free terms here: ``Key``
        # collides with the item noun and ``Daily`` with the mission tab, so
        # those ambiguous labels must not be overridden globally.
        "Reset": "重置",
        "General Settings": "常规设置",
        "Audio": "声音",
        "Display": "显示",
        "Account Management": "账号管理",
        "ON": "开启",
        "OFF": "关闭",
        "Home Only": "仅主页",
        "Every Time": "每次",
        "Once a Day": "每日一次",
        "On Login": "登录时",
    },
}


@dataclass
class TargetEvidence:
    count: int = 0
    categories: Counter[str] = field(default_factory=Counter)
    provenance: list[str] = field(default_factory=list)


class MappingAccumulator:
    def __init__(self, source_locale: str):
        self.source_locale = source_locale
        self.values: dict[str, dict[str, TargetEvidence]] = defaultdict(dict)
        self.rejected = Counter()
        self.placeholder_mismatches: list[dict[str, Any]] = []
        self.overrides: dict[str, tuple[str, str]] = {}
        self.resolved_collisions: list[dict[str, Any]] = []
        self.total_seen = 0

    def add_override(self, source: str, target: str, provenance: str) -> None:
        source = normalize_lookup_text(source.replace("\x00", ""))
        target = normalize_lookup_text(target.replace("\x00", ""))
        if source and target and source != target:
            self.overrides[source] = (target, provenance)

    def add(
        self,
        source: str,
        target: str,
        category: str,
        provenance: str,
        path: tuple[str, ...] = (),
    ) -> None:
        self.total_seen += 1
        source = normalize_lookup_text(source.replace("\x00", ""))
        target = normalize_lookup_text(target.replace("\x00", ""))
        if not source or not target or source == target:
            self.rejected["empty_or_identical"] += 1
            return
        if len(source) > 1000 or len(target) > 4000:
            self.rejected["too_long"] += 1
            return
        if field_is_probably_identifier(path):
            self.rejected["identifier_field"] += 1
            return
        if not contains_han(target):
            self.rejected["target_without_han"] += 1
            return
        if not looks_like_source_language(source, self.source_locale):
            self.rejected["source_language_filter"] += 1
            return
        source_placeholders = placeholder_counter(source)
        target_placeholders = placeholder_counter(target)
        if source_placeholders != target_placeholders:
            self.rejected["placeholder_mismatch"] += 1
            if len(self.placeholder_mismatches) < 10000:
                self.placeholder_mismatches.append(
                    {
                        "source": source,
                        "target": target,
                        "source_placeholders": dict(source_placeholders),
                        "target_placeholders": dict(target_placeholders),
                        "provenance": provenance,
                    }
                )
            return

        evidence = self.values[source].setdefault(target, TargetEvidence())
        evidence.count += 1
        evidence.categories[category] += 1
        if len(evidence.provenance) < 8:
            evidence.provenance.append(provenance)

    def finalize(self) -> tuple[dict[str, list[tuple[str, str]]], list[dict[str, Any]]]:
        by_category: dict[str, list[tuple[str, str]]] = defaultdict(list)
        collisions: list[dict[str, Any]] = []
        for source, targets in self.values.items():
            override = self.overrides.get(source)
            if override is not None:
                target, _ = override
                by_category["i18n"].append((source, target))
                continue
            if len(targets) != 1:
                ranked = sorted(
                    targets.items(),
                    key=lambda item: (-item[1].count, item[0]),
                )
                total = sum(evidence.count for _, evidence in ranked)
                winner, winner_evidence = ranked[0]
                runner_up_count = ranked[1][1].count
                # XUnity performs a context-free lookup.  When official data
                # overwhelmingly uses one translation, retaining that target
                # gives better UI coverage than discarding every occurrence.
                # Ties and weak/one-off majorities remain quality-gated.
                if (
                    winner_evidence.count >= 3
                    and winner_evidence.count >= runner_up_count + 2
                    and winner_evidence.count / total >= 0.60
                ):
                    target, evidence = winner, winner_evidence
                    self.resolved_collisions.append(
                        {
                            "source": source,
                            "target": target,
                            "winner_count": evidence.count,
                            "total_count": total,
                        }
                    )
                else:
                    collisions.append(
                        {
                            "source": source,
                            "targets": [
                                {
                                    "target": target,
                                    "count": evidence.count,
                                    "categories": dict(evidence.categories),
                                    "provenance": evidence.provenance,
                                }
                                for target, evidence in sorted(targets.items())
                            ],
                        }
                    )
                    continue
            else:
                target, evidence = next(iter(targets.items()))
            category = min(
                evidence.categories,
                key=lambda name: CATEGORY_PRIORITY.get(name, 99),
            )
            by_category[category].append((source, target))
        for source, (target, _) in self.overrides.items():
            if source not in self.values:
                by_category["i18n"].append((source, target))
        for pairs in by_category.values():
            pairs.sort(key=lambda pair: (pair[0].casefold(), pair[0]))
        collisions.sort(key=lambda item: item["source"].casefold())
        return dict(by_category), collisions


def strip_display_markup(text: str) -> str:
    """Return the text Arknights ultimately shows after rich/custom tags."""
    return DISPLAY_TAG_RE.sub("", text)


def _add_display_pair(
    accumulator: MappingAccumulator,
    source: str,
    target: str,
    category: str,
    provenance: str,
    path: tuple[str, ...] = (),
) -> int:
    """Add source data plus the tag-free form seen by hooked text widgets."""
    accumulator.add(source, target, category, provenance, path)
    plain_source = strip_display_markup(source)
    plain_target = strip_display_markup(target)
    if plain_source != source or plain_target != target:
        accumulator.add(
            plain_source,
            plain_target,
            category,
            provenance + ":display",
            path,
        )
        return 2
    return 1


def _blackboard_values(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key).casefold(): item for key, item in value.items()}
    if not isinstance(value, list):
        return {}
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict) or "key" not in item:
            continue
        resolved = item.get("valueStr")
        if resolved is None or resolved == "":
            resolved = item.get("value")
        result[str(item["key"]).casefold()] = resolved
    return result


def _format_blackboard_value(value: Any, format_spec: str) -> str | None:
    if not format_spec:
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            numeric = Decimal(str(value))
            if numeric == numeric.to_integral_value():
                return str(int(numeric))
            return format(numeric.normalize(), "f")
        return str(value) if value is not None else None
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    percent = format_spec.endswith("%")
    number_format = format_spec[:-1] if percent else format_spec
    if percent:
        numeric *= 100
    if not number_format or any(char not in "0." for char in number_format):
        return None
    decimals = len(number_format.partition(".")[2])
    quantum = Decimal(1).scaleb(-decimals)
    numeric = numeric.quantize(quantum, rounding=ROUND_HALF_UP)
    suffix = "%" if percent else ""
    return f"{numeric:.{decimals}f}{suffix}"


def render_skill_description(text: str, blackboard: Any) -> str | None:
    """Resolve Arknights skill placeholders to the strings shown in the UI."""
    values = _blackboard_values(blackboard)
    failed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal failed
        token = match.group("token")
        key, separator, format_spec = token.partition(":")
        negate = key.startswith("-")
        lookup = key[1:] if negate else key
        value = values.get(lookup.casefold())
        if value is None:
            failed = True
            return match.group(0)
        if negate:
            try:
                value = -Decimal(str(value))
            except (InvalidOperation, ValueError):
                failed = True
                return match.group(0)
        formatted = _format_blackboard_value(value, format_spec if separator else "")
        if formatted is None:
            failed = True
            return match.group(0)
        return formatted

    rendered = SKILL_PLACEHOLDER_RE.sub(replace, text)
    return None if failed else rendered


def _skill_level_values(level: dict[str, Any]) -> dict[str, Any]:
    """Merge values used by UI macros, including fields outside blackboard."""
    values = _blackboard_values(level.get("blackboard"))
    if level.get("duration") is not None:
        values.setdefault("duration", level["duration"])
    return values


def _collect_skill_display_variants(
    source_data: Any,
    target_data: Any,
    accumulator: MappingAccumulator,
) -> dict[str, int]:
    if isinstance(source_data, dict) and set(source_data) == {"skills"}:
        source_data = source_data["skills"]
    if isinstance(target_data, dict) and set(target_data) == {"skills"}:
        target_data = target_data["skills"]
    if not isinstance(source_data, dict) or not isinstance(target_data, dict):
        return {"levels": 0, "rendered": 0, "unresolved": 0}
    stats = {"levels": 0, "rendered": 0, "unresolved": 0}
    for skill_id in source_data.keys() & target_data.keys():
        source_levels = source_data[skill_id].get("levels", [])
        target_levels = target_data[skill_id].get("levels", [])
        if not isinstance(source_levels, list) or len(source_levels) != len(target_levels):
            continue
        for index, (source_level, target_level) in enumerate(zip(source_levels, target_levels)):
            if not isinstance(source_level, dict) or not isinstance(target_level, dict):
                continue
            source = source_level.get("description")
            target = target_level.get("description")
            if not isinstance(source, str) or not isinstance(target, str):
                continue
            stats["levels"] += 1
            rendered_source = render_skill_description(source, _skill_level_values(source_level))
            rendered_target = render_skill_description(target, _skill_level_values(target_level))
            if rendered_source is None or rendered_target is None:
                stats["unresolved"] += 1
                continue
            _add_display_pair(
                accumulator,
                rendered_source,
                rendered_target,
                "tables",
                f"table:skill_table.json:{skill_id}/levels/{index}/rendered",
                (skill_id, "levels", str(index), "description"),
            )
            stats["rendered"] += 1
    return stats


def _collect_parameterized_display_variants(
    source_data: Any,
    target_data: Any,
    accumulator: MappingAccumulator,
    table_name: str,
) -> dict[str, int]:
    """Resolve UI text beside a local blackboard outside ``skill_table``.

    Operator traits, branch traits and module overrides use the same
    ``{key:format}`` macros as skills, but live in ``character_table`` and
    ``battle_equip_table``.  The game substitutes those macros before XUnity
    sees the text, so template-only mappings cannot match the displayed value.
    """

    stats = {"containers": 0, "fields": 0, "rendered": 0, "unresolved": 0}

    def walk(source: Any, target: Any, path: tuple[str, ...]) -> None:
        if isinstance(source, dict) and isinstance(target, dict):
            common_keys = source.keys() & target.keys()
            candidate_keys: list[str] = []
            if "blackboard" in common_keys:
                candidate_keys = [
                    str(key)
                    for key in common_keys
                    if isinstance(source[key], str)
                    and isinstance(target[key], str)
                    and SKILL_PLACEHOLDER_RE.search(source[key]) is not None
                ]
            if candidate_keys:
                stats["containers"] += 1
                source_values = _skill_level_values(source)
                target_values = _skill_level_values(target)
                for key in candidate_keys:
                    stats["fields"] += 1
                    rendered_source = render_skill_description(source[key], source_values)
                    rendered_target = render_skill_description(target[key], target_values)
                    if rendered_source is None or rendered_target is None:
                        stats["unresolved"] += 1
                        continue
                    field_path = path + (key,)
                    _add_display_pair(
                        accumulator,
                        rendered_source,
                        rendered_target,
                        "tables",
                        f"table:{table_name}:{'/'.join(field_path)}:rendered",
                        field_path,
                    )
                    stats["rendered"] += 1

            for key in common_keys:
                walk(source[key], target[key], path + (str(key),))
            return

        if isinstance(source, list) and isinstance(target, list) and len(source) == len(target):
            for index, (source_item, target_item) in enumerate(zip(source, target)):
                walk(source_item, target_item, path + (str(index),))

    walk(source_data, target_data, ())
    return stats


def _mission_semantic_signature(mission: Any) -> tuple[Any, ...] | None:
    """Return locale-independent mission mechanics used across renumberings.

    JP/EN and CN periodically keep the same daily/weekly task under different
    IDs (for example ``weekly_5xx`` versus ``weekly_7xx``). Matching on the
    localized description would defeat the purpose, while this combination is
    the actual task condition consumed by the client.
    """
    if not isinstance(mission, dict):
        return None
    params = mission.get("param")
    if not isinstance(params, list) or any(
        not isinstance(value, (str, int, float, bool)) for value in params
    ):
        return None
    required = ("type", "template", "templateType")
    if any(not isinstance(mission.get(field), (str, int)) for field in required):
        return None
    point = mission.get("periodicalPoint")
    if point is not None and not isinstance(point, (int, float)):
        return None
    return (
        str(mission["type"]),
        str(mission["template"]),
        str(mission["templateType"]),
        tuple(str(value) for value in params),
        point,
    )


def _collect_mission_semantic_variants(
    source_data: Any,
    target_data: Any,
    accumulator: MappingAccumulator,
) -> dict[str, int]:
    """Recover descriptions skipped solely because mission IDs diverged.

    A signature is accepted only when every matching CN entry agrees on one
    description. Repeated historical copies are therefore useful evidence,
    while genuinely ambiguous mechanics stay excluded from the context-free
    XUnity dictionary.
    """
    stats = {
        "source_missing_keys": 0,
        "matched_signatures": 0,
        "ambiguous_target_signatures": 0,
        "aligned_source_entries": 0,
        "emitted_pairs": 0,
    }
    if not isinstance(source_data, dict) or not isinstance(target_data, dict):
        return stats
    source_missions = source_data.get("missions")
    target_missions = target_data.get("missions")
    if not isinstance(source_missions, dict) or not isinstance(target_missions, dict):
        return stats

    target_by_signature: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for mission in target_missions.values():
        signature = _mission_semantic_signature(mission)
        if signature is not None:
            target_by_signature[signature].append(mission)

    proposed_pairs: dict[tuple[str, str], str] = {}
    matched_signatures: set[tuple[Any, ...]] = set()
    ambiguous_signatures: set[tuple[Any, ...]] = set()
    for mission_id, mission in source_missions.items():
        if mission_id in target_missions or not isinstance(mission, dict):
            continue
        stats["source_missing_keys"] += 1
        signature = _mission_semantic_signature(mission)
        candidates = target_by_signature.get(signature, []) if signature is not None else []
        if not candidates:
            continue
        targets = {
            normalize_lookup_text(candidate["description"].replace("\x00", ""))
            for candidate in candidates
            if isinstance(candidate.get("description"), str)
        }
        if len(targets) != 1:
            if signature is not None:
                ambiguous_signatures.add(signature)
            continue
        source = mission.get("description")
        if not isinstance(source, str):
            continue
        target = next(iter(targets))
        matched_signatures.add(signature)
        stats["aligned_source_entries"] += 1
        proposed_pairs.setdefault(
            (source, target),
            f"table:mission_table.json:missions/{mission_id}/description:semantic",
        )

    for (source, target), provenance in proposed_pairs.items():
        _add_display_pair(
            accumulator,
            source,
            target,
            "tables",
            provenance,
            ("missions", "description"),
        )
    stats["matched_signatures"] = len(matched_signatures)
    stats["ambiguous_target_signatures"] = len(ambiguous_signatures)
    stats["emitted_pairs"] = len(proposed_pairs)
    return stats


def parse_string_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.startswith("[") or "]" not in line:
            continue
        closing = line.find("]")
        key = line[1:closing]
        value = line[closing + 1 :]
        if key:
            result[key] = value
    return result


def _collect_i18n(
    source_root: Path,
    target_root: Path,
    accumulator: MappingAccumulator,
    fallback_source_root: Path | None = None,
) -> dict[str, int]:
    source_path = source_root / "i18n" / "string_map.txt"
    if not source_path.is_file() and fallback_source_root is not None:
        source_path = fallback_source_root / "i18n" / "string_map.txt"
    source_map = parse_string_map(source_path)
    target_map = parse_string_map(target_root / "i18n" / "string_map.txt")
    common = source_map.keys() & target_map.keys()
    for key in common:
        _add_display_pair(accumulator, source_map[key], target_map[key], "i18n", f"i18n:{key}")
    return {"source": len(source_map), "target": len(target_map), "common": len(common)}


def _collect_tables(
    source_root: Path,
    target_root: Path,
    accumulator: MappingAccumulator,
    fallback_source_root: Path | None = None,
) -> dict[str, Any]:
    source_excel = source_root / "gamedata" / "excel"
    target_excel = target_root / "gamedata" / "excel"
    source_files: dict[str, Path] = {}
    if fallback_source_root is not None:
        fallback_excel = fallback_source_root / "gamedata" / "excel"
        source_files.update({path.name.casefold(): path for path in fallback_excel.glob("*.json")})
    source_files.update({path.name.casefold(): path for path in source_excel.glob("*.json")})
    target_files = {path.name.casefold(): path for path in target_excel.glob("*.json")}
    common = sorted(source_files.keys() & target_files.keys())
    pair_count = 0
    observed_count = 0
    per_table: dict[str, int] = {}
    skill_display = {"levels": 0, "rendered": 0, "unresolved": 0}
    parameterized_display = {
        "containers": 0,
        "fields": 0,
        "rendered": 0,
        "unresolved": 0,
        "per_table": {},
    }
    mission_semantic = {
        "source_missing_keys": 0,
        "matched_signatures": 0,
        "ambiguous_target_signatures": 0,
        "aligned_source_entries": 0,
        "emitted_pairs": 0,
    }
    unwrapped_roots: dict[str, list[str]] = {"source": [], "target": []}
    for name in common:
        source_data = read_json(source_files[name])
        target_data = read_json(target_files[name])
        wrapper = TABLE_ROOT_WRAPPERS.get(name)
        if wrapper is not None:
            if isinstance(source_data, dict) and set(source_data) == {wrapper}:
                source_data = source_data[wrapper]
                unwrapped_roots["source"].append(name)
            if isinstance(target_data, dict) and set(target_data) == {wrapper}:
                target_data = target_data[wrapper]
                unwrapped_roots["target"].append(name)
        table_pairs = 0
        for source, target, path in walk_paired(source_data, target_data):
            observed_count += _add_display_pair(
                accumulator,
                source,
                target,
                "tables",
                f"table:{name}:{'/'.join(path)}",
                path,
            )
            table_pairs += 1
        if name == "skill_table.json":
            skill_display = _collect_skill_display_variants(
                source_data,
                target_data,
                accumulator,
            )
        elif name == "mission_table.json":
            mission_semantic = _collect_mission_semantic_variants(
                source_data,
                target_data,
                accumulator,
            )
        else:
            display_stats = _collect_parameterized_display_variants(
                source_data,
                target_data,
                accumulator,
                name,
            )
            if display_stats["fields"]:
                parameterized_display["per_table"][name] = display_stats
                for key in ("containers", "fields", "rendered", "unresolved"):
                    parameterized_display[key] += display_stats[key]
        pair_count += table_pairs
        per_table[name] = table_pairs
    return {
        "source_tables": len(source_files),
        "target_tables": len(target_files),
        "common_tables": len(common),
        "paired_string_fields": pair_count,
        "candidate_observations": observed_count,
        "skill_display": skill_display,
        "mission_semantic": mission_semantic,
        "parameterized_display": parameterized_display,
        "unwrapped_roots": unwrapped_roots,
        "per_table": per_table,
    }


def _write_collisions(path: Path, collisions: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("source", "target", "count", "categories", "provenance"))
        for collision in collisions:
            for target in collision["targets"]:
                writer.writerow(
                    (
                        collision["source"],
                        target["target"],
                        target["count"],
                        ";".join(sorted(target["categories"])),
                        " | ".join(target["provenance"]),
                    )
                )


def _write_placeholder_mismatches(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "source",
                "target",
                "source_placeholders",
                "target_placeholders",
                "provenance",
            )
        )
        for item in items:
            writer.writerow(
                (
                    item["source"],
                    item["target"],
                    item["source_placeholders"],
                    item["target_placeholders"],
                    item["provenance"],
                )
            )


def build_translation_pack(
    data_root: Path,
    output_root: Path,
    source_locale: str,
    *,
    include_story: bool = True,
    max_story_files: int | None = None,
    local_source_root: Path | None = None,
    local_story_root: Path | None = None,
    allow_fuzzy_story: bool = False,
) -> dict[str, Any]:
    if source_locale not in {"en", "jp"}:
        raise ValueError("source_locale must be 'en' or 'jp'")
    public_source_root = data_root / source_locale
    target_root = data_root / "cn"
    if not public_source_root.is_dir() or not target_root.is_dir():
        raise FileNotFoundError(f"Missing locale data below {data_root}")
    source_root = local_source_root.resolve() if local_source_root is not None else public_source_root
    if not source_root.is_dir():
        raise FileNotFoundError(f"Local source data root not found: {source_root}")

    accumulator = MappingAccumulator(source_locale)
    for source, target in BUILTIN_OVERRIDES[source_locale].items():
        accumulator.add_override(source, target, "builtin-ui")
    i18n_stats = _collect_i18n(
        source_root,
        target_root,
        accumulator,
        fallback_source_root=public_source_root,
    )
    table_stats = _collect_tables(
        source_root,
        target_root,
        accumulator,
        fallback_source_root=public_source_root,
    )
    story_stats: dict[str, int] | None = None
    if include_story:
        story_root = local_story_root.resolve() if local_story_root is not None else source_root
        source_story = story_root / "gamedata" / "story"
        if not source_story.is_dir() or not any(source_story.rglob("*.txt")):
            source_story = public_source_root / "gamedata" / "story"
        story_stats = collect_story_pairs(
            source_story,
            target_root / "gamedata" / "story",
            lambda source, target, provenance: accumulator.add(
                source, target, "story", provenance
            ),
            max_files=max_story_files,
            allow_fuzzy=allow_fuzzy_story,
        )

    by_category, collisions = accumulator.finalize()
    text_root = output_root / "Translation" / "zh" / "Text"
    file_names = {
        "i18n": "10_i18n.txt",
        "tables": "20_tables.txt",
        "story": "30_story.txt",
    }
    for category, file_name in file_names.items():
        write_translation_file(
            text_root / file_name,
            by_category.get(category, []),
            f"Arknights {source_locale.upper()} -> zh-CN ({category})",
        )

    reports = output_root / "reports"
    _write_collisions(reports / "collisions.csv", collisions)
    _write_placeholder_mismatches(
        reports / "placeholder_mismatches.csv",
        accumulator.placeholder_mismatches,
    )
    report = {
        "source_locale": source_locale,
        "target_locale": "cn",
        "input_data_root": str(data_root.resolve()),
        "source_data_root": str(source_root.resolve()),
        "source_story_root": str(source_story.resolve()) if include_story else None,
        "public_source_fallback": str(public_source_root.resolve()),
        "include_story": include_story,
        "allow_fuzzy_story": allow_fuzzy_story,
        "i18n": i18n_stats,
        "tables": table_stats,
        "story": story_stats,
        "candidate_observations": accumulator.total_seen,
        "accepted_unique": sum(len(pairs) for pairs in by_category.values()),
        "accepted_by_category": {
            category: len(pairs) for category, pairs in sorted(by_category.items())
        },
        "collision_sources": len(collisions),
        "resolved_collision_sources": len(accumulator.resolved_collisions),
        "builtin_overrides": len(BUILTIN_OVERRIDES[source_locale]),
        "rejected": dict(accumulator.rejected),
        "placeholder_mismatch_samples": len(accumulator.placeholder_mismatches),
    }
    write_json(output_root / "pack-report.json", report)
    return report
