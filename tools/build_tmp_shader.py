"""Extract only the standard TMP mobile SDF shader from the pinned XUnity bundle.

No Arial font, material, glyphs or texture atlas are retained. The small shader
bundle is embedded in the companion DLL so font rendering works even in players
that strip unused TMP shaders. Run with the project's Conda Python.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import UnityPy


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "cache/fonts/arialuni_sdf_u2021"
DESTINATION = PROJECT / "plugins/ArknightsLocalization.RichTextFix/Resources/cn_tmp_shader.bundle"


def build() -> None:
    expected = "63a5cbf2b9c7351c6ff8f7f592be03d2cc79668fad48f3cfe8e0e547af43aa3c"
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != expected:
        raise ValueError("Expected the pinned XUnity Unity 2021 font bundle")
    environment = UnityPy.load(str(SOURCE))
    shader = next(obj for obj in environment.objects if obj.type.name == "Shader"
                  and obj.read_typetree()["m_ParsedForm"]["m_Name"] == "TextMeshPro/Mobile/Distance Field")
    if shader.read_typetree()["m_Dependencies"] or shader.assets_file.externals:
        raise ValueError("Expected a self-contained SDF shader")
    asset_bundle = next(obj for obj in environment.objects if obj.type.name == "AssetBundle")
    data = asset_bundle.read_typetree()
    data["m_Name"] = data["m_AssetBundleName"] = "arklocalizer_tmp_shader"
    pointer = {"m_FileID": 0, "m_PathID": shader.path_id}
    data["m_PreloadTable"] = [pointer]
    data["m_Container"] = [("assets/arklocalizer/tmp-mobile-sdf.shader",
                            {"preloadIndex": 0, "preloadSize": 1, "asset": pointer})]
    asset_bundle.save_typetree(data)
    serialized = shader.assets_file
    serialized.objects = {shader.path_id: shader, asset_bundle.path_id: asset_bundle}
    serialized.name = "CAB-" + hashlib.sha256(b"arklocalizer-tmp-shader" + shader.get_raw_data()).hexdigest()[:32]
    bundle = next(iter(environment.files.values()))
    bundle.files = {serialized.name: serialized}
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_bytes(bundle.save(packer="lz4"))
    print(DESTINATION, DESTINATION.stat().st_size, hashlib.sha256(DESTINATION.read_bytes()).hexdigest())


if __name__ == "__main__":
    build()
