"""Bundle the CN Heavy face for missing-glyph replacements in display text."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import UnityPy

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from arklocalizer.cn_font import make_font_bundle


def build(game: Path, role: str = "heavy") -> None:
    name = {"heavy": "SourceHanSansCN-Heavy", "song": "方正特雅宋_GBK"}[role]
    for path in (game / "Arknights_Data").glob("sharedassets*.assets"):
        environment = UnityPy.load(str(path))
        source = next((obj for obj in environment.objects if obj.type.name == "Font" and obj.read().m_Name == name), None)
        if source is not None:
            break
    else:
        raise FileNotFoundError(f"{name} not found in CN client {game}")
    shell = UnityPy.load(str(PROJECT / "cache/fonts/arknights_cn_notosanshans"))
    bundle = UnityPy.load(make_font_bundle(source, shell))
    asset_bundle = next(obj for obj in bundle.objects if obj.type.name == "AssetBundle")
    data = asset_bundle.read_typetree()
    data["m_Name"] = data["m_AssetBundleName"] = "arklocalizer_cn_" + role
    data["m_Container"] = [(f"assets/arklocalizer/cn-{role}.font", data["m_Container"][0][1])]
    asset_bundle.save_typetree(data)
    output = PROJECT / f"plugins/ArknightsLocalization.RichTextFix/Resources/cn_{role}.bundle"
    output.write_bytes(next(iter(bundle.files.values())).save(packer="lz4"))
    report = {"name": name, "source_asset": path.name,
              "font_data_sha256": hashlib.sha256(bytes(source.read_typetree()["m_FontData"])).hexdigest(),
              "bundle_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "bytes": output.stat().st_size}
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", type=Path)
    parser.add_argument("--role", choices=("heavy", "song"), default="heavy")
    args = parser.parse_args()
    build(args.game, args.role)
