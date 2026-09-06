"""Build additive UI-scoped translations from matching official data fields.

Never writes the global dictionaries. Ambiguities within a scope stay excluded.
"""
from collections import defaultdict
import gzip
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from arklocalizer.mapping import parse_string_map, strip_display_markup
from arklocalizer.util import contains_han


def build(root: Path, output: Path | None = None) -> dict:
    candidates = defaultdict(lambda: defaultdict(set))

    def add(scope, source, target):
        if not isinstance(source, str) or not isinstance(target, str):
            return
        source = strip_display_markup(source).strip()
        if source and target:
            # Include identity evidence: a foreign name that is already a CN
            # name must still participate in ambiguity checks.
            candidates[scope][source].add(target)

    def table(locale, name, wrapper):
        data = json.loads((root / locale / 'gamedata/excel' / name).read_text(encoding='utf-8'))
        return data.get(wrapper, data)

    cn_chars = table('cn', 'character_table.json', 'characters')
    cn_skills = table('cn', 'skill_table.json', 'skills')
    cn_strings = parse_string_map(root / 'cn/i18n/string_map.txt')
    for locale in ('jp', 'en'):
        names = defaultdict(set)
        chars = table(locale, 'character_table.json', 'characters')
        skills = table(locale, 'skill_table.json', 'skills')
        for key in chars.keys() & cn_chars.keys():
            source, target = chars[key], cn_chars[key]
            names['operator'].add((source['name'], target['name']))
            for talent, cn_talent in zip(source.get('talents') or [], target.get('talents') or []):
                # Match unlock condition/potential rather than array position.
                lookup = {(json.dumps(c['unlockCondition'], sort_keys=True), c['requiredPotentialRank']): c
                          for c in cn_talent.get('candidates') or []}
                for c in talent.get('candidates') or []:
                    other = lookup.get((json.dumps(c['unlockCondition'], sort_keys=True), c['requiredPotentialRank']))
                    if other and c.get('name') and other.get('name'):
                        names['talent'].add((c['name'], other['name']))
        for key in skills.keys() & cn_skills.keys():
            for source, target in zip(skills[key]['levels'], cn_skills[key]['levels']):
                if source.get('name') and target.get('name'):
                    names['skill'].add((source['name'], target['name']))
        for role, pairs in names.items():
            for source, target in pairs:
                add(role, source, target)
        strings = parse_string_map(root / locale / 'i18n/string_map.txt')
        # Serialized Text.textId mostly references main_text/init_text; the
        # public string_map alone misses compiled prefab labels (&&... keys).
        for filename in ('init_text.json', 'main_text.json'):
            source_ids = table(locale, filename, 'strings')
            target_ids = table('cn', filename, 'strings')
            for key in source_ids.keys() & target_ids.keys():
                source, target = source_ids[key], target_ids[key]
                if isinstance(source, str) and isinstance(target, str) and contains_han(target) and '{' not in source and '{' not in target:
                    add('id:' + key, source, target)
        for key in strings.keys() & cn_strings.keys():
            if '{' not in strings[key] and '{' not in cn_strings[key]:
                add('id:' + key, strings[key], cn_strings[key])
            if key.startswith('CHAR_EVOLVE_'):
                scope = 'promotion-summary'
            elif key.startswith('EVOLVE_NEW_'):
                scope = 'promotion-detail'
            else:
                continue
            source, target = strings[key], cn_strings[key]
            role = 'talent' if 'TALENT' in key else 'skill' if 'SKILL' in key else None
            if key in ('CHAR_EVOLVE_ADD_COST', 'CHAR_EVOLVE_RDC_COST', 'CHAR_EVOLVE_UP_BLOCK'):
                add('number:' + scope, source, target)
            if '{0}' in source and role:
                for name, cn_name in names[role]:
                    add(scope, source.replace('{0}', name), target.replace('{0}', cn_name))
                    # A previous whole-format regex may have already translated
                    # the label or argument. These variants are still bounded
                    # to this official template and this field's name list.
                    add(scope, source.replace('{0}', cn_name), target.replace('{0}', cn_name))
                    add(scope, target.replace('{0}', name), target.replace('{0}', cn_name))
            elif '{' not in source and '{' not in target:
                add(scope, source, target)
    from tools.context_domains import collect
    templates = []
    collect(root, add, templates)
    result = {scope: {source: next(iter(targets)) for source, targets in sorted(entries.items())
                      if len(targets) == 1}
              for scope, entries in sorted(candidates.items())}
    # Ambiguity is also retained so a typed field can reject an unrelated
    # global majority winner rather than silently using it.
    for scope, entries in candidates.items():
        blocked = {source: "" for source, targets in entries.items() if len(targets) > 1}
        if blocked: result["ambiguous:" + scope] = blocked
    unique_templates = {json.dumps(t, sort_keys=True, ensure_ascii=False): t for t in templates}
    result["dynamic"] = {str(i): key for i, key in enumerate(sorted(unique_templates))}
    report = {scope: {'entries': len(result[scope]),
                      'ambiguous': [source for source, targets in sorted(entries.items()) if len(targets) > 1]}
              for scope, entries in sorted(candidates.items())}
    output = output or PROJECT / 'plugins/ArknightsLocalization.RichTextFix/Resources/context_translations.json.gz'
    output.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':')).encode('utf-8'), mtime=0))
    output.with_suffix('.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    report = build(PROJECT / 'cache/ArknightsGamedataMulti')
    print(json.dumps({scope: {'entries': r['entries'], 'ambiguous': len(r['ambiguous'])}
                      for scope, r in report.items() if not scope.startswith('id:')}))
    print('Text IDs:', sum(k.startswith('id:') and r['entries'] > 0 for k, r in report.items()))
