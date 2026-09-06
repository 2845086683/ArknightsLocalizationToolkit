"""Build exact CN rich-text targets from official data, without changing translations.

The companion plugin uses a target only when its visible text exactly matches
the already selected offline translation. Conflicting styles are omitted.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from arklocalizer.mapping import render_skill_description, _skill_level_values, strip_display_markup
from arklocalizer.util import normalize_lookup_text

MACRO = re.compile(r"<(?P<kind>[@$])(?P<name>[^>]+)>|</>")


def render_styles(text: str, styles: dict[str, str]) -> str | None:
    """Resolve official <@style> scopes; preserve literal semantic angle text."""
    stack: list[str] = []
    output: list[str] = []
    offset = 0
    for match in MACRO.finditer(text):
        output.append(text[offset:match.start()])
        offset = match.end()
        if match.group(0) == "</>":
            if not stack:
                return None
            output.append(stack.pop())
        else:
            if match["kind"] == "$":
                # Game terminology links require a component-specific link ID.
                # Retain the documented keyword color, not a fabricated link.
                style = styles.get("ba.kw")
            else:
                style = styles.get(match["name"])
            if style is None or style.count("{0}") != 1:
                return None
            prefix, suffix = style.split("{0}")
            output.append(prefix)
            stack.append(suffix)
    if stack:
        return None
    output.append(text[offset:])
    return normalize_lookup_text("".join(output).replace("\\n", "\n"))


def build(data: Path) -> dict:
    constants = json.loads((data / "gamedata_const.json").read_text(encoding="utf-8"))
    styles = constants["richTextStyles"]
    candidates: dict[str, set[str]] = defaultdict(set)

    def add(text: str) -> None:
        if "<" not in text or len(text) > 16000:
            return
        rendered = render_styles(text, styles)
        if rendered is None or re.search(r"\{[^{}]+\}", rendered):
            return
        plain = normalize_lookup_text(strip_display_markup(rendered))
        if len(plain) >= 8 and plain != rendered and any("\u3400" <= c <= "\u9fff" for c in plain):
            candidates[plain].add(rendered)

    def walk(value) -> None:
        if isinstance(value, dict):
            # Skill levels, talent candidates, equip and building effects.
            if isinstance(value.get("blackboard"), (dict, list)):
                for key in ("description", "desc"):
                    if isinstance(value.get(key), str):
                        rendered = render_skill_description(value[key], _skill_level_values(value))
                        if rendered:
                            add(rendered)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            add(value)

    for path in sorted(data.glob("*.json")):
        if path.name != "gamedata_const.json":
            walk(json.loads(path.read_text(encoding="utf-8")))
    targets = {plain: next(iter(variants)) for plain, variants in sorted(candidates.items()) if len(variants) == 1}
    result = {"schema": 1, "styles": styles, "targets": targets}
    output = PROJECT / "plugins/ArknightsLocalization.RichTextFix/Resources/cn_rich_styles.json.gz"
    output.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    report = {"exact_targets": len(targets), "ambiguous_targets_excluded": sum(len(v) > 1 for v in candidates.values()),
              "style_definitions": len(styles), "compressed_bytes": output.stat().st_size}
    output.with_name("cn_rich_styles.report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report)
    return result


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT / "cache/ArknightsGamedataMulti/cn/gamedata/excel")
