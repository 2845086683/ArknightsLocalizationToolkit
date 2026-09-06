"""Import the CN client's own dynamic UI font without installing an OS font."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import UnityPy

from .extractor import effective_anon_bundles, validate_game_directory
from .util import is_within, sha256_file, write_json
from .xunity import DEFAULT_TMP_FONT


CN_FONT_BUNDLE = DEFAULT_TMP_FONT
CN_FONT_NAME = "NotoSansHans-Medium"
CN_FONT_ASSET = "assets/arklocalizer/notosanshans-medium.font"


def _font_object(environment: Any, name: str) -> Any | None:
    return next((obj for obj in environment.objects
                 if obj.type.name == "Font" and obj.read().m_Name == name), None)


def make_font_bundle(source: Any, template_environment: Any) -> bytes:
    """Use a same-version native font bundle as a shell, not a TMP atlas.

    The shell has only Font/Material/Texture2D/AssetBundle objects and a builtin
    shader reference. Drop the original Font's cross-file fallbacks, and remap
    its material/texture pointers to the shell's local objects.
    """
    fonts = [obj for obj in template_environment.objects if obj.type.name == "Font"]
    bundles = [obj for obj in template_environment.objects if obj.type.name == "AssetBundle"]
    if len(fonts) != 1 or len(bundles) != 1:
        raise ValueError("Expected a standalone native font bundle")
    target, asset_bundle = fonts[0], bundles[0]
    if source.assets_file.unity_version != target.assets_file.unity_version:
        raise ValueError("CN font and its bundle shell use different Unity versions")
    if any(obj.type.name not in {"Font", "Material", "Texture2D", "AssetBundle"}
           for obj in template_environment.objects):
        raise ValueError("Font shell contains unrelated game resources")
    if any(ext.path != "Library/unity default resources" for ext in target.assets_file.externals):
        raise ValueError("Font shell depends on external game assets")
    for obj in template_environment.objects:
        if obj.type.name == "Texture2D" and obj.read_typetree().get("m_StreamData", {}).get("size"):
            raise ValueError("Font shell has an external texture stream")

    original = source.read_typetree()
    shell = target.read_typetree()
    font = copy.deepcopy(original)
    if not font.get("m_FontData") or font.get("m_CharacterRects"):
        raise ValueError("CN font must contain dynamic font data")
    font["m_DefaultMaterial"] = shell["m_DefaultMaterial"]
    font["m_Texture"] = shell["m_Texture"]
    font["m_FallbackFonts"] = []
    target.save_typetree(font)

    bundle_data = asset_bundle.read_typetree()
    if bundle_data["m_Dependencies"]:
        raise ValueError("Font shell has external bundle dependencies")
    bundle_data["m_Name"] = CN_FONT_BUNDLE
    bundle_data["m_AssetBundleName"] = CN_FONT_BUNDLE
    entry = next(value for _, value in bundle_data["m_Container"]
                 if value["asset"]["m_PathID"] == target.path_id)
    bundle_data["m_Container"] = [(CN_FONT_ASSET, entry)]
    asset_bundle.save_typetree(bundle_data)

    # Avoid a CAB identity collision if the original CN shell also exists in
    # the target locale. Only this independent four-object file is retained.
    bundle = next(iter(template_environment.files.values()))
    if len(bundle.files) != 1:
        raise ValueError("Font shell must have a single serialized file")
    serialized = target.assets_file
    digest = hashlib.sha256(bytes(font["m_FontData"])).hexdigest()
    serialized.name = "CAB-" + hashlib.sha256(("arklocalizer-cn-" + digest).encode()).hexdigest()[:32]
    bundle.files = {serialized.name: serialized}
    return bundle.save(packer="lz4")


def validate_cn_font(path: Path) -> dict[str, Any]:
    env = UnityPy.load(str(path))
    font = _font_object(env, CN_FONT_NAME)
    if font is None:
        raise ValueError(f"CN font asset is missing from {path}")
    tree = font.read_typetree()
    if not tree["m_FontData"] or tree["m_FallbackFonts"]:
        raise ValueError("CN font must be self-contained with embedded font data")
    if CN_FONT_ASSET not in env.container:
        raise ValueError("CN font asset address is missing")
    return {
        "name": CN_FONT_NAME,
        "unity_version": font.assets_file.unity_version,
        "font_data_sha256": hashlib.sha256(bytes(tree["m_FontData"])).hexdigest(),
        "font_data_bytes": len(tree["m_FontData"]),
        "sha256": sha256_file(path),
    }


def import_cn_font(game_dir: Path, output_root: Path) -> dict[str, Any]:
    game_dir = validate_game_directory(game_dir)
    if is_within(output_root, game_dir):
        raise ValueError("Font output must be outside the source CN client")
    output = output_root / CN_FONT_BUNDLE
    if output.exists():
        raise FileExistsError(f"Font already exists; use a new output directory: {output}")
    data = game_dir / "Arknights_Data"
    source = None
    source_path = None
    for path in sorted(data.glob("sharedassets*.assets")):
        env = UnityPy.load(str(path))
        source = _font_object(env, CN_FONT_NAME)
        if source is not None:
            source_path = path
            break
    if source is None:
        raise FileNotFoundError(f"{CN_FONT_NAME} not found in the supplied CN client")

    # Bundle names are hashes and can change after updates. Discover the
    # native-font shell by content rather than hard-coding one client's hash.
    for candidate in effective_anon_bundles(game_dir):
        env = UnityPy.load(str(candidate.path))
        if _font_object(env, "SourceHanSansCN-Bold") is not None:
            payload = make_font_bundle(source, env)
            break
    else:
        raise FileNotFoundError("Standalone SourceHanSansCN-Bold font bundle not found in CN client")
    output_root.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    report = {
        "source_client": str(game_dir),
        "source_asset": str(source_path),
        "source_asset_sha256": sha256_file(source_path),
        "shell_asset": str(candidate.path),
        "shell_asset_sha256": sha256_file(candidate.path),
        "bundle": CN_FONT_BUNDLE,
        **validate_cn_font(output),
    }
    write_json(output_root / f"{CN_FONT_BUNDLE}.json", report)
    return report
