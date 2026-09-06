using System;
using System.IO;
using System.Linq;
using System.Reflection;
using BepInEx;
using BepInEx.Logging;
using HarmonyLib;
using TMPro;
using UnityEngine;
using UnityEngine.TextCore.LowLevel;
using XUnity.AutoTranslator.Plugin.Core;

namespace ArknightsLocalization.RichTextFix;

// The bundled native CN font serves UGUI directly and supplies a dynamic TMP
// atlas. No Windows font installation, font enumeration, or system fallback
// is involved. Component hooks also cover text that XUnity never translates.
internal static class CnFontOverride
{
    internal const string BundleName = "arknights_cn_notosanshans";
    internal const string UguiName = "Arknights CN NotoSansHans Medium";
    private const string AssetName = "assets/arklocalizer/notosanshans-medium.font";
    private static ManualLogSource? log;
    private static FieldInfo? overrideFontSetting;
    private static AssetBundle? bundle;
    private static AssetBundle? shaderBundle;
    private static Font? font;
    private static TMP_FontAsset? tmp;
    private static AssetBundle? heavyBundle;
    private static Font? heavy;
    private static TMP_FontAsset? heavyTmp;
    private static bool heavyFailed, heavyTmpFailed;
    private static AssetBundle? songBundle;
    private static Font? song;
    private static TMP_FontAsset? songTmp;
    private static bool songFailed, songTmpFailed;
    private static bool loadFailed;
    private static bool tmpFailed;

    internal static void Enable(ManualLogSource logger)
    {
        log = logger;
        Assembly assembly = typeof(AutoTranslationPlugin).Assembly;
        Type helper = assembly.GetType("XUnity.AutoTranslator.Plugin.Core.Fonts.FontHelper", true)!;
        Type settings = assembly.GetType("XUnity.AutoTranslator.Plugin.Core.Configuration.Settings", true)!;
        overrideFontSetting = AccessTools.Field(settings, "OverrideFont")
            ?? throw new MissingFieldException(settings.FullName, "OverrideFont");
        MethodInfo supported = AccessTools.Method(typeof(AutoTranslationPlugin), "GetSupportedFonts")
            ?? throw new MissingMethodException("XUnity.GetSupportedFonts");
        MethodInfo ugui = AccessTools.Method(helper, "GetTextFont", new[] { typeof(int) })
            ?? throw new MissingMethodException("XUnity.FontHelper.GetTextFont");
        MethodInfo tmpLoad = AccessTools.Method(helper, "GetTextMeshProFont", new[] { typeof(string) })
            ?? throw new MissingMethodException("XUnity.FontHelper.GetTextMeshProFont");
        Harmony harmony = new("arklocalizer.cnfont");
        harmony.Patch(supported, postfix: new HarmonyMethod(typeof(CnFontOverride), nameof(SupportedFontsPostfix)));
        harmony.Patch(ugui, prefix: new HarmonyMethod(typeof(CnFontOverride), nameof(UguiPrefix)));
        harmony.Patch(tmpLoad, prefix: new HarmonyMethod(typeof(CnFontOverride), nameof(TmpPrefix)));
        log.LogInfo("CN font resource hooks ready (no OS fonts required).");
    }

    internal static void EnableComponentCoverage() => AdaptiveFontPolicy.Enable(log!);

    internal static Font? Replacement(string? originalName, string text)
    {
        if (FontCoveragePolicy.PreferSong(originalName))
        {
            Font? display = LoadSong();
            if (display != null && !NativeFontCoverage.NeedsCnFont(display, text)) return display;
        }
        if (FontCoveragePolicy.PreferHeavy(originalName))
        {
            Font? display = LoadHeavy();
            if (display != null && !NativeFontCoverage.NeedsCnFont(display, text)) return display;
        }
        return LoadFont();
    }

    private static Font? LoadHeavy()
    {
        if (heavy != null || heavyFailed) return heavy;
        try
        {
            using Stream stream = typeof(CnFontOverride).Assembly.GetManifestResourceStream("ArknightsLocalization.CnHeavyFont")!;
            using MemoryStream bytes = new();
            stream.CopyTo(bytes);
            heavyBundle = AssetBundle.LoadFromMemory(bytes.ToArray());
            heavy = heavyBundle.LoadAsset<Font>("assets/arklocalizer/cn-heavy.font");
            if (heavy == null || !heavy.dynamic) throw new InvalidOperationException("CN Heavy font is unavailable.");
            UnityEngine.Object.DontDestroyOnLoad(heavy);
            heavy.hideFlags = HideFlags.DontUnloadUnusedAsset;
            log!.LogInfo("Loaded CN SourceHanSansCN-Heavy for incomplete bold/display faces.");
            return heavy;
        }
        catch (Exception exception)
        {
            heavyFailed = true;
            heavy = null;
            log!.LogWarning($"Could not load CN display font; using CN Medium: {exception}");
            return null;
        }
    }

    private static Font? LoadSong()
    {
        if (song != null || songFailed) return song;
        try
        {
            using Stream stream = typeof(CnFontOverride).Assembly.GetManifestResourceStream("ArknightsLocalization.CnSongFont")!;
            using MemoryStream bytes = new();
            stream.CopyTo(bytes);
            songBundle = AssetBundle.LoadFromMemory(bytes.ToArray());
            song = songBundle.LoadAsset<Font>("assets/arklocalizer/cn-song.font");
            if (song == null || !song.dynamic) throw new InvalidOperationException("CN Song font is unavailable.");
            UnityEngine.Object.DontDestroyOnLoad(song);
            song.hideFlags = HideFlags.DontUnloadUnusedAsset;
            log!.LogInfo("Loaded CN FZTeYaSong GBK for incomplete bold/display faces.");
            return song;
        }
        catch (Exception exception)
        {
            songFailed = true;
            song = null;
            log!.LogWarning($"Could not load CN display font; using CN Medium: {exception}");
            return null;
        }
    }

    internal static TMP_FontAsset? TmpReplacement(string? originalName, string text)
    {
        Font? face = Replacement(originalName, text);
        TMP_FontAsset? standard = LoadTmp(); // Ensures the SDF shader is ready.
        if (face == null || standard == null) return standard;
        if (face == song)
        {
            if (songTmp != null || songTmpFailed) return songTmp ?? standard;
            try
            {
                songTmp = TMP_FontAsset.CreateFontAsset(face, 64, 8, GlyphRenderMode.SDFAA,
                    2048, 2048, AtlasPopulationMode.Dynamic, true);
                if (songTmp == null) throw new InvalidOperationException("CN Song TMP creation returned null.");
                songTmp.name = "Arknights CN FZTeYaSong SDF";
                UnityEngine.Object.DontDestroyOnLoad(songTmp);
                songTmp.hideFlags = HideFlags.DontUnloadUnusedAsset;
                return songTmp;
            }
            catch (Exception exception)
            {
                songTmp = null;
                songTmpFailed = true;
                log!.LogWarning($"Could not create CN Song TMP; using CN Medium: {exception}");
                return standard;
            }
        }
        if (face != heavy) return standard;
        if (heavyTmp != null || heavyTmpFailed) return heavyTmp ?? standard;
        try
        {
            heavyTmp = TMP_FontAsset.CreateFontAsset(face, 64, 8, GlyphRenderMode.SDFAA,
                2048, 2048, AtlasPopulationMode.Dynamic, true);
            if (heavyTmp == null) throw new InvalidOperationException("CN Heavy TMP creation returned null.");
            heavyTmp.name = "Arknights CN SourceHanSansCN-Heavy SDF";
            UnityEngine.Object.DontDestroyOnLoad(heavyTmp);
            heavyTmp.hideFlags = HideFlags.DontUnloadUnusedAsset;
            return heavyTmp;
        }
        catch (Exception exception)
        {
            heavyTmp = null;
            heavyTmpFailed = true;
            log!.LogWarning($"Could not create CN Heavy TMP; using CN Medium: {exception}");
            return standard;
        }
    }

    private static void SupportedFontsPostfix(ref string[] __result)
    {
        // This is XUnity's supported-font list, NOT Windows' installed fonts.
        // Register a virtual name so its config validation accepts our loader.
        __result = (__result ?? Array.Empty<string>()).Append(UguiName).Distinct().ToArray();
    }

    private static bool UguiPrefix(ref Font? __result)
    {
        if (!string.Equals(overrideFontSetting?.GetValue(null) as string, UguiName, StringComparison.Ordinal))
            return true;
        __result = LoadFont();
        return false;
    }

    private static bool TmpPrefix(string assetBundle, ref UnityEngine.Object? __result)
    {
        if (!string.Equals(assetBundle, BundleName, StringComparison.Ordinal))
            return true;
        __result = LoadTmp();
        return false;
    }

    internal static Font? LoadFont()
    {
        if (font != null || loadFailed) return font;
        try
        {
            string path = Path.Combine(Paths.GameRootPath, BundleName);
            if (!File.Exists(path)) throw new FileNotFoundException("CN font bundle missing; repair the localization installation.", path);
            bundle = AssetBundle.LoadFromFile(path);
            if (bundle == null) throw new InvalidOperationException("Unity could not load the CN font bundle.");
            font = bundle.LoadAsset<Font>(AssetName);
            if (font == null || !font.dynamic || !font.HasCharacter('汉'))
                throw new InvalidOperationException("CN bundle did not provide a usable dynamic Chinese font.");
            UnityEngine.Object.DontDestroyOnLoad(font);
            font.hideFlags = HideFlags.DontUnloadUnusedAsset;
            log!.LogInfo($"Loaded CN font: {font.name}; source=bundled game asset, not an OS font.");
            return font;
        }
        catch (Exception exception)
        {
            font = null;
            loadFailed = true;
            log!.LogError($"CN font load failed. Repair the installation; retaining original game font: {exception}");
            return null;
        }
    }

    internal static TMP_FontAsset? LoadTmp()
    {
        if (tmp != null || tmpFailed) return tmp;
        Font? source = LoadFont();
        if (source == null) return null;
        try
        {
            // The game can strip the standard TMP shader altogether. Bundle
            // only the shader in this DLL, with no foreign font/glyph assets.
            if (ShaderUtilities.ShaderRef_MobileSDF == null)
            {
                using Stream stream = typeof(CnFontOverride).Assembly.GetManifestResourceStream(
                    "ArknightsLocalization.CnTmpShader")
                    ?? throw new InvalidOperationException("Embedded TMP shader bundle is missing.");
                using MemoryStream bytes = new();
                stream.CopyTo(bytes);
                shaderBundle = AssetBundle.LoadFromMemory(bytes.ToArray());
                Shader? shader = shaderBundle?.LoadAsset<Shader>("assets/arklocalizer/tmp-mobile-sdf.shader");
                if (shader == null) throw new InvalidOperationException("Could not load bundled TMP SDF shader.");
                ShaderUtilities.k_ShaderRef_MobileSDF = shader;
                log!.LogInfo("Loaded bundled TMP SDF shader for CN glyph rendering.");
            }
            tmp = TMP_FontAsset.CreateFontAsset(source, 64, 8, GlyphRenderMode.SDFAA,
                2048, 2048, AtlasPopulationMode.Dynamic, true);
            if (tmp == null) throw new InvalidOperationException("TMP dynamic font creation returned null.");
            tmp.name = "Arknights CN NotoSansHans Medium SDF";
            UnityEngine.Object.DontDestroyOnLoad(tmp);
            tmp.hideFlags = HideFlags.DontUnloadUnusedAsset;
            if (!tmp.TryAddCharacters("汉罗德岛龙门干员战术训练关卡伤害持续治疗部署费用0123456789", out string missing))
                throw new InvalidOperationException($"CN font warmup has missing characters: {missing}");
            log!.LogInfo("CN dynamic TMP atlas ready; UGUI and TMP share NotoSansHans-Medium glyphs.");
            return tmp;
        }
        catch (Exception exception)
        {
            tmp = null;
            tmpFailed = true;
            log!.LogError($"CN TMP font initialization failed; retaining original game font: {exception}");
            return null;
        }
    }
}
