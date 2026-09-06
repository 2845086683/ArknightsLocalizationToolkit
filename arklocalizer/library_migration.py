"""Move legacy library layouts into runtime without losing user dictionaries."""
import json
import os
import shutil
import uuid
from pathlib import Path

from .libraries import (MANIFEST, METADATA, REPOSITORY, discover, identify,
                        library_root, official_id, official_relative, verify)
from .util import sha256_file, write_json


def _inside(project, path):
    resolved = path.resolve()
    if resolved == project or not resolved.is_relative_to(project):
        raise ValueError(f"迁移路径超出项目目录：{path}")
    return resolved


def _fingerprint(path):
    root = path.resolve()
    result = {}
    for item in path.rglob('*'):
        if not item.resolve().is_relative_to(root):
            raise ValueError(f"词库包含指向外部的链接：{item}")
        if item.is_file() and item.relative_to(path).as_posix() != METADATA:
            result[item.relative_to(path).as_posix()] = sha256_file(item)
    return result


def migrate(project: Path, config=None):
    project = project.resolve()
    root = library_root(project)
    _inside(project, root)
    journal = project / 'work/runtime-migration.json'
    moved = json.loads(journal.read_text(encoding='utf-8')) if journal.exists() else {}
    old_roots = [project / '词库', project / 'outputs/runtime', project / 'output/runtime']
    previous = getattr(config, 'last_runtime', '')
    if config is not None and not previous:
        for folder in [project / '词库', project / 'outputs', project / 'output']:
            pointer = folder / f'current-{config.locale}-runtime.txt'
            if pointer.is_file():
                previous = pointer.read_text(encoding='utf-8-sig').strip()
                if previous: break
    existing = discover(project)
    for old_root in old_roots:
        if not old_root.exists(): continue
        _inside(project, old_root)
        for manifest in sorted(old_root.glob(f'*/{MANIFEST}')):
            source = manifest.parent
            if source.name.startswith('.'): continue
            _inside(project, source)
            data = verify(source)
            locale = data['source_locale']
            if not (source / METADATA).exists() and source.name == f'{locale}-zh-offline-final':
                write_json(source / METADATA, dict(schema=1, id=official_id(locale), locale=locale,
                    name=f"官方中文词库（{'美服' if locale == 'en' else '日服'}）",
                    repository=REPOSITORY, remote_path=official_relative(locale)))
            item = identify(source)
            current = next((x for x in existing if x.id == item.id), None)
            signature = _fingerprint(source)
            if current is not None and signature == _fingerprint(current.path):
                # Record selection recovery before removing an exact duplicate.
                moved[str(source.resolve())] = current.id
                write_json(journal, moved)
                _inside(project, source)
                shutil.rmtree(source)
                continue
            metadata = json.loads((source / METADATA).read_text(encoding='utf-8-sig'))
            if current is not None:
                # Same ID with different contents: keep both, never choose by age.
                metadata.update(id=str(uuid.uuid4()), name=metadata['name'] + '（旧目录保留副本）')
            identifier = metadata['id']
            name = f'{locale}-zh-offline-final' if identifier == official_id(locale) else source.name
            destination = root / name
            if destination.exists(): destination = root / identifier
            if destination.exists(): destination = root / (identifier + '-' + uuid.uuid4().hex[:8])
            _inside(project, destination)
            root.mkdir(parents=True, exist_ok=True)
            moved[str(source.resolve())] = identifier
            write_json(journal, moved)
            write_json(source / METADATA, metadata)
            os.replace(source, destination)
            existing.append(identify(destination))
        # Keep update backups accessible in the unified library folder.
        backups = old_root / '.backups'
        if backups.exists():
            _inside(project, backups)
            target = root / '.backups'
            target.mkdir(parents=True, exist_ok=True)
            for source in list(backups.iterdir()):
                _inside(project, source)
                destination = target / source.name
                if destination.exists(): destination = target / (source.name + '-' + uuid.uuid4().hex[:8])
                _inside(project, destination)
                os.replace(source, destination)
            backups.rmdir()
        if not any(old_root.iterdir()): old_root.rmdir()
    # Preserve notes, old build products and pointer files, outside the active
    # library directory. Empty legacy containers disappear as well.
    for old in [project / '词库', project / 'outputs', project / 'output']:
        if not old.exists(): continue
        _inside(project, old)
        if not any(old.iterdir()): old.rmdir(); continue
        archive = project / 'work/legacy-layout' / old.name
        if archive.exists(): archive = archive.with_name(old.name + '-' + uuid.uuid4().hex[:8])
        _inside(project, archive)
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, archive)
    for item in existing:
        if item.repository == REPOSITORY:
            metadata = json.loads((item.path / METADATA).read_text(encoding='utf-8-sig'))
            if metadata.get('remote_path') in {f'outputs/runtime/{item.locale}-zh-offline-final', f'output/runtime/{item.locale}-zh-offline-final'}:
                metadata['remote_path'] = official_relative(item.locale)
                write_json(item.path / METADATA, metadata)
    existing = discover(project)
    if config is not None and previous:
        identifier = moved.get(str(Path(previous).resolve()))
        match = next((x for x in existing if x.id == identifier or x.path == Path(previous).resolve()), None)
        if match is not None and match.locale == config.locale:
            config.selected_libraries.setdefault(config.locale, match.id)
            config.last_runtime = str(match.path)
    return existing
