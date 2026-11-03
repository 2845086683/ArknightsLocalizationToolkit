"""Portable publication-safety tests (stdlib only, including the real builder)."""
from __future__ import annotations

import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arklocalizer import auto_update as updater
from arklocalizer.util import read_json, sha256_file, write_json
from arklocalizer.xunity import validate_translation_pack, write_translation_file


def file_hashes(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): sha256_file(path)
            for path in root.rglob("*") if path.is_file()}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.data = self.project / "data"
        self.work = self.project / "work"
        self.work.mkdir()
        package = self.project / "arklocalizer"
        package.mkdir()
        (package / "mapping.py").write_text("# fixture builder\n", encoding="utf-8")
        workflow = self.project / ".github/workflows/update-wordlibraries.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("# fixture workflow\n", encoding="utf-8")
        self.data.mkdir()
        strings = {
            "en": ("Start Game", "Sample Material", "Welcome back, Doctor."),
            "jp": ("ゲームスタート", "テスト素材", "お帰りなさい、ドクター。"),
            "cn": ("开始游戏", "测试材料", "欢迎回来，博士。"),
        }
        for locale, (ui, item, story) in strings.items():
            root = self.data / locale
            write_json(root / "hot_update_list.json", {"versionId": f"26-11-03-{locale}"})
            (root / "i18n").mkdir(parents=True)
            (root / "i18n/string_map.txt").write_text(f"[start]{ui}\n", encoding="utf-8")
            write_json(root / "gamedata/excel/item_table.json", {"items": {"item_1": {"name": item}}})
            (root / "gamedata/story").mkdir(parents=True)
            (root / "gamedata/story/start.txt").write_text(story + "\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.snapshot = {"repository": updater.UPSTREAM, "ref": updater.UPSTREAM_REF,
                         "commit": self.git("rev-parse", "HEAD"),
                         "trees": {locale: self.git("rev-parse", f"HEAD:{locale}")
                                   for locale in ("en", "cn")}}
        for locale in updater.LOCALES:
            runtime = self.project / updater.official_relative(locale)
            text = runtime / "BepInEx" / updater.TEXT
            for index, name in enumerate(updater.PACK_FILES):
                pairs = [] if name == "15_format_regex.txt" else [(f"old {locale} {index}", "旧译文")]
                write_translation_file(text / name, pairs, "Baseline")
            (runtime / "WORDLIBRARY.json").write_text('{"id":"keep-me"}', encoding="utf-8")
            (runtime / "plugin.dll").write_bytes(b"unchanged plugin")
            entries = [{"path": path.relative_to(runtime).as_posix(), "bytes": path.stat().st_size,
                        "sha256": sha256_file(path)} for path in sorted(text.glob("*.txt"))]
            entries.append({"path": "plugin.dll", "bytes": 16, "sha256": sha256_file(runtime / "plugin.dll")})
            write_json(runtime / updater.MANIFEST,
                       {"source_locale": locale, "offline_only": True, "font": "keep-font", "files": entries})
        self.plan = {"schema": 1, "locale": "en", "upstream": self.snapshot, "builder_sha256": updater.builder_digest(self.project)}
        write_json(self.work / "build-plan.json", self.plan)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.data), *args], text=True).strip()

    def test_real_build_preserves_runtime_and_is_repeatable(self):
        before = {locale: read_json(self.project / updater.official_relative(locale) / updater.MANIFEST)
                  for locale in updater.LOCALES}
        updater.build(self.project, self.data, self.work, "en")
        self.assertEqual(read_json(self.project / updater.STATE / "en.json")["upstream"], self.plan["upstream"])
        for locale in updater.LOCALES:
            runtime = self.project / updater.official_relative(locale)
            manifest = read_json(runtime / updater.MANIFEST)
            self.assertEqual(manifest["font"], "keep-font")
            self.assertEqual(manifest["files"][-1], before[locale]["files"][-1])
            self.assertEqual((runtime / "plugin.dll").read_bytes(), b"unchanged plugin")
            self.assertEqual((runtime / "WORDLIBRARY.json").read_text(), '{"id":"keep-me"}')
            for entry in manifest["files"]:
                self.assertEqual(entry["sha256"], sha256_file(runtime / entry["path"]))
                self.assertEqual(entry["bytes"], (runtime / entry["path"]).stat().st_size)
            validation = validate_translation_pack(runtime / "BepInEx")
            self.assertTrue(validation["ok"])
            self.assertGreaterEqual(validation["mappings"], 3)
            if locale == "en":
                self.assertEqual(manifest["wordlibrary_build"]["resource_versions"]["en"], "26-11-03-en")
        hashes = file_hashes(self.project / "runtime")
        updater.build(self.project, self.data, self.work, "en")
        self.assertEqual(hashes, file_hashes(self.project / "runtime"))

    def test_en_can_publish_when_jp_data_is_broken(self):
        jp = self.project / updater.official_relative("jp")
        hashes = file_hashes(jp)
        shutil.rmtree(self.data / "jp")
        self.git("add", "-A")
        self.git("commit", "-qm", "JP unavailable")
        self.plan["upstream"]["commit"] = self.git("rev-parse", "HEAD")
        write_json(self.work / "build-plan.json", self.plan)
        updater.build(self.project, self.data, self.work, "en")
        self.assertEqual(hashes, file_hashes(jp))
        self.assertTrue((self.project / updater.STATE / "en.json").exists())
        self.assertFalse((self.project / updater.STATE / "jp.json").exists())

    def test_check_skips_unrelated_commit_but_rebuilds_cn_and_code_changes(self):
        write_json(self.project / updater.STATE / "en.json", self.plan)
        with patch.object(updater, "upstream_snapshot", return_value=self.snapshot):
            self.assertFalse(updater.needs_build(self.plan, updater.check(self.project, self.work, "en")))
        unrelated = copy.deepcopy(self.plan)
        unrelated["upstream"]["commit"] = "a" * 40
        self.assertFalse(updater.needs_build(self.plan, unrelated))
        for locale in ("en", "cn"):
            changed = copy.deepcopy(self.plan)
            changed["upstream"]["trees"][locale] = "b" * 40
            self.assertTrue(updater.needs_build(self.plan, changed))
        changed = dict(self.plan, builder_sha256="changed")
        self.assertTrue(updater.needs_build(self.plan, changed))
        self.assertTrue(updater.needs_build({}, self.plan))

    def test_jp_build_has_its_own_state_and_version(self):
        en = self.project / updater.official_relative("en")
        hashes = file_hashes(en)
        plan = copy.deepcopy(self.plan)
        plan["locale"] = "jp"
        plan["upstream"]["trees"] = {region: self.git("rev-parse", f"HEAD:{region}")
                                       for region in ("jp", "cn")}
        write_json(self.work / "build-plan.json", plan)
        updater.build(self.project, self.data, self.work, "jp")
        self.assertEqual(hashes, file_hashes(en))
        self.assertFalse((self.project / updater.STATE / "en.json").exists())
        self.assertEqual(read_json(self.project / updater.STATE / "jp.json")["resource_versions"],
                         {"jp": "26-11-03-jp", "cn": "26-11-03-cn"})

    def test_snapshot_ignores_other_server(self):
        tree = {"tree": [{"type": "tree", "path": region, "sha": region * 20}
                         for region in ("en", "jp", "cn", "tw")]}
        with patch.object(updater, "github_json", side_effect=[{"object": {"sha": "a" * 40}}, tree]):
            self.assertEqual(set(updater.upstream_snapshot("en")["trees"]), {"en", "cn"})

    def test_hash_mismatch_aborts(self):
        path = self.project / updater.official_relative("en") / "BepInEx" / updater.TEXT / "10_i18n.txt"
        path.write_text("tampered=value\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Baseline manifest hash mismatch"):
            updater.build(self.project, self.data, self.work, "en")
        self.assertFalse((self.project / updater.STATE / "en.json").exists())

    def test_wrong_commit_or_dirty_upstream_aborts(self):
        wrong = copy.deepcopy(self.plan)
        wrong["upstream"]["commit"] = "a" * 40
        with self.assertRaisesRegex(ValueError, "pinned build plan"):
            updater.validate_inputs(self.data, wrong)
        (self.data / "cn/i18n/string_map.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "local changes"):
            updater.validate_inputs(self.data, self.plan)

    def test_missing_story_aborts_even_with_updated_plan(self):
        shutil.rmtree(self.data / "cn/gamedata/story")
        self.git("add", "-A")
        self.git("commit", "-qm", "incomplete upstream")
        plan = copy.deepcopy(self.plan)
        plan["upstream"]["commit"] = self.git("rev-parse", "HEAD")
        plan["upstream"]["trees"]["cn"] = self.git("rev-parse", "HEAD:cn")
        with self.assertRaisesRegex(ValueError, "Missing gamedata/story: cn"):
            updater.validate_inputs(self.data, plan)

    def test_category_drop_cannot_hide_behind_total_growth(self):
        baseline = {"ok": True, "files": [{"name": name, "mappings": 100} for name in updater.PACK_FILES]}
        current = copy.deepcopy(baseline)
        current["files"][0]["mappings"] = 10000
        current["files"][-1]["mappings"] = 89
        with self.assertRaisesRegex(ValueError, "mappings dropped"):
            updater.validate_counts(current, baseline, "en")
        current["files"][-1]["mappings"] = 90
        updater.validate_counts(current, baseline, "en")

    def test_empty_pack_conflicts_and_unexpected_files_fail(self):
        baseline = {"ok": True, "files": [{"name": name, "mappings": 0} for name in updater.PACK_FILES]}
        with self.assertRaisesRegex(ValueError, "mappings dropped"):
            updater.validate_counts(baseline, baseline, "en")
        with self.assertRaisesRegex(ValueError, "invalid translation"):
            updater.validate_counts(dict(baseline, ok=False), baseline, "en")
        with self.assertRaisesRegex(ValueError, "file set"):
            updater.validate_counts(dict(baseline, files=[]), baseline, "en")


if __name__ == "__main__":
    unittest.main()
