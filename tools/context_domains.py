"""Official semantic domains for component-scoped lookup; no global overrides."""
import json
import re
from pathlib import Path

from arklocalizer.mapping import parse_string_map, strip_display_markup
from arklocalizer.story import collect_story_pairs
from arklocalizer.util import walk_paired, contains_han
from tools.build_rich_styles import render_styles


def collect(root: Path, add, templates: list) -> None:
    def table(locale, name):
        path = root / locale / 'gamedata/excel' / name
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    styles = table('cn', 'gamedata_const.json').get('richTextStyles', {})

    def rendered(value):
        return render_styles(value, styles) or value

    def pair(scope, source, target):
        if not source or not target or (scope not in {'operator', 'operator-character', 'operator-autochess'} and not contains_han(target)):
            return
        add(scope, source, rendered(target))
        if scope in {'subprofession', 'operator', 'operator-character', 'operator-autochess', 'bond', 'room', 'building-skill', 'recruit-tag'}:
            add(scope, source.upper(), rendered(target))

    def template(scope, source, target, roles):
        source, target = rendered(source), rendered(target)
        tokens = re.findall(r'\{(\d+|@nickname)(?::[^{}]+)?\}', source)
        target_tokens = re.findall(r'\{(\d+|@nickname)(?::[^{}]+)?\}', target)
        if not tokens or set(tokens) != set(target_tokens) or not set(tokens) <= roles.keys():
            return
        if len(re.sub(r'\{[^{}]+\}', '', strip_display_markup(source)).strip()) < (1 if scope == 'mission' else 8):
            return
        templates.append(dict(scope=scope, source=source, target=target, roles=roles))

    for locale in ('en', 'jp'):
        source_map_path = root / locale / 'i18n/string_map.txt'
        target_map_path = root / 'cn/i18n/string_map.txt'
        if source_map_path.exists() and target_map_path.exists():
            source_map, target_map = parse_string_map(source_map_path), parse_string_map(target_map_path)
            for key in source_map.keys() & target_map.keys():
                for prefix, scope in (('CHAR_ATK_SPEED_', 'attack-speed'), ('CHAR_RESPAWNTIME_', 'redeploy-speed')):
                    if key.startswith(prefix): pair(scope, source_map[key], target_map[key])
        source_chars = table(locale, 'character_table.json'); target_chars = table('cn', 'character_table.json')
        source_tags = {r['tagId']: r['tagName'] for r in table(locale, 'gacha_table.json').get('gachaTags', [])}
        target_tags = {r['tagId']: r['tagName'] for r in table('cn', 'gacha_table.json').get('gachaTags', [])}
        tag_targets = {}
        for key in source_tags.keys() & target_tags.keys():
            tag_targets.setdefault(source_tags[key], set()).add(target_tags[key])
            pair('operator-tag', source_tags[key], target_tags[key])
        for character in source_chars.values():
            tags = character.get('tagList') or []
            if tags and all(len(tag_targets.get(tag, ())) == 1 for tag in tags):
                targets = [next(iter(tag_targets[tag])) for tag in tags]
                # Whole official tag lists only, within the dedicated tag field.
                for separator in (' / ', '/', ' ', '  ', ', ', '、', '・', '\n'):
                    pair('operator-tag', separator.join(tags), separator.join(targets))
        for s, t, p in walk_paired(source_chars, target_chars):
            if p[-1] == 'name' and len(p) <= 3:
                pair('operator', s, t)
        for key in source_chars.keys() & target_chars.keys():
            source, target = source_chars[key], target_chars[key]
            # Character cards/details cannot display traps or summon tokens.
            # E.g. Mountain is both char_264_f12yin (山) and a trap (山脉).
            # Keep the broad domain unchanged for battle/token consumers.
            if key.startswith('char_'):
                for alias in (source['name'], source.get('appellation'), target.get('appellation')):
                    if isinstance(alias, str) and alias.strip():
                        pair('operator-character', alias.strip(), target['name'])
            for alias in (source.get('appellation'), target.get('appellation')):
                if isinstance(alias, str) and alias.strip():
                    pair('operator', alias.strip(), target['name'])
        for filename, section, field, scope in (
            ('uniequip_table.json', 'subProfDict', 'subProfessionName', 'subprofession'),
            ('building_data.json', 'rooms', 'name', 'room'),
            ('building_data.json', 'buffs', 'buffName', 'building-skill'),
            ('building_data.json', 'buffs', 'description', 'building-description'),
            ('gacha_table.json', 'gachaTags', 'tagName', 'recruit-tag'),
        ):
            for s, t, p in walk_paired(table(locale, filename).get(section, {}), table('cn', filename).get(section, {})):
                if p[-1] == field:
                    pair(scope, s, t)
        source_act, target_act = table(locale, 'activity_table.json'), table('cn', 'activity_table.json')
        # The event's reserve operator and a recruitable operator can have the
        # same English name but different CN names (e.g. Raidian / 电弧).
        # Only shared event rows identify which official characters participate.
        for source_id, target_id, path in walk_paired(source_act, target_act):
            if path[-1] not in {'charId', 'backupCharId'} or not any('autochess' in str(part).lower() for part in path): continue
            if source_id != target_id or source_id not in source_chars or target_id not in target_chars: continue
            source, target = source_chars[source_id], target_chars[target_id]
            for alias in (source['name'], source.get('appellation'), target.get('appellation')):
                if isinstance(alias, str) and alias.strip(): pair('operator-autochess', alias.strip(), target['name'])
        for s, t, p in walk_paired(source_act.get('autoChessData', {}).get('bondInfoDict', {}),
                                   target_act.get('autoChessData', {}).get('bondInfoDict', {})):
            if p[-1] == 'name': pair('bond', s, t)
        broadcast_roles = {
            'BOSS_HIT': {'0': 'identity'}, 'SHOP_LEVEL': {'0': 'identity', '1': 'number'},
            'CHAR_DAMAGE': {'0': 'identity', '1': 'operator', '2': 'number'},
            'GOLDEN_CHAR': {'0': 'identity', '1': 'operator'}, 'CHAR_GIFT': {'0': 'identity', '1': 'operator'},
        }
        cn_broadcast = {r['id']: r for r in target_act.get('autoChessData', {}).get('broadcastList', [])}
        for row in source_act.get('autoChessData', {}).get('broadcastList', []):
            other = cn_broadcast.get(row['id'])
            if other and row['type'] in broadcast_roles:
                template('broadcast', row['desc'], other['desc'], broadcast_roles[row['type']])
            if other and '{' not in row['desc'] and '{' not in other['desc']:
                pair('broadcast', row['desc'], other['desc'])
        # Mission routes interpolate game mode and topic names. These are data
        # terms, never arbitrary player input; no general string fragment lookup.
        for filename, section in (('roguelike_topic_table.json', 'topics'),
                                  ('activity_table.json', 'basicInfo'),
                                  ('special_operator_table.json', 'modeData')):
            for s, t, p in walk_paired(table(locale, filename).get(section, {}), table('cn', filename).get(section, {})):
                if p[-1] in {'name', 'topicName', 'typeName', 'activityName'} and len(s) < 100:
                    pair('activity-name', s, t)
        for filename in ('mission_table.json', 'special_operator_table.json'):
            for s, t, p in walk_paired(table(locale, filename), table('cn', filename)):
                if p[-1] in {'description', 'desc', 'title', 'unlockDesc', 'name', 'evolveTabExpNotice', 'noFrontNodeToast', 'noFrontTaskToast'} and '{' not in s:
                    pair('mission', s, t)
                if p[-1] == 'skillGotoToast':
                    template('mission', s, t, {'0': 'activity-name', '1': 'activity-name'})
        for filename in ('main_text.json', 'init_text.json'):
            src, cn = table(locale, filename), table('cn', filename)
            for key in src.keys() & cn.keys():
                s, t = src[key], cn[key]
                if not isinstance(s, str) or not isinstance(t, str): continue
                if key.startswith(('SO_CHAR_', 'SOCHAR_', 'MISSION_SO')):
                    if '{' not in s: pair('mission', s, t)
                    else:
                        roles = {'0': 'number'} if key == 'SO_CHAR_COMMON_TALENT_UNLOCK_CONDITION_EVOLVE' else {'0': 'activity-name', '1': 'activity-name'}
                        template('mission', s, t, roles)

        def story_pair(source, target, provenance):
            if '{@nickname}' in source:
                template('story', source, target, {'@nickname': 'identity'})
            elif '<' in source or '<' in target:
                if '{' not in source and '{' not in target:
                    pair('story', source, target)

        source_story, cn_story = root / locale / 'gamedata/story', root / 'cn/gamedata/story'
        if source_story.exists() and cn_story.exists():
            # Only same-file, identical command sequences: no fuzzy dialogue
            # alignment and no speaker-name replacement with operator aliases.
            collect_story_pairs(source_story, cn_story, story_pair)
