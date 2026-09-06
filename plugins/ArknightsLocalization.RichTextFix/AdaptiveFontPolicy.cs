using System;
using System.Collections.Generic;
using System.Linq;
using BepInEx.Logging;
using HarmonyLib;
using Il2CppInterop.Runtime.InteropTypes;
using TMPro;
using UnityEngine;
using UnityEngine.UI;
using XUnity.AutoTranslator.Plugin.Core;

namespace ArknightsLocalization.RichTextFix;

// Remember the game's face separately from the temporary Chinese replacement.
// Native instance IDs are stable across different managed IL2CPP wrappers.
internal static class AdaptiveFontPolicy
{
    private sealed class UguiState
    {
        internal Text Component = null!;
        internal Font? Original, Applied;
        internal string? LastText;
        internal bool NeedsCn;
    }
    private sealed class TmpState
    {
        internal TMP_Text Component = null!;
        internal TMP_FontAsset? Original, Applied;
        internal TMP_FontAsset? MaterialFace;
        internal Material? OriginalMaterial, AppliedMaterial, CnMaterial;
        internal string? LastText;
        internal bool NeedsCn;
    }
    private static readonly Dictionary<int, UguiState> uguiStates = new();
    private static readonly Dictionary<int, TmpState> tmpStates = new();
    private static ManualLogSource log = null!;
    private static bool enabled, errorLogged;
    private static int applying, nextCleanupFrame;

    internal static void Enable(ManualLogSource logger)
    {
        if (enabled) return;
        log = logger;
        if (CnFontOverride.LoadFont() == null) return;
        CnFontOverride.LoadTmp();
        Harmony harmony = new("arklocalizer.cnfont.adaptive");
        Patch(harmony, typeof(Text), "set_font", nameof(UguiFontAssigned), true);
        Patch(harmony, typeof(Text), "set_text", nameof(UguiEnsure), true);
        Patch(harmony, typeof(Text), "OnEnable", nameof(UguiEnsure), true);
        // Apply before the renderer disables font-texture rebuild callbacks.
        // Switching for the first time inside GetGenerationSettings is too late:
        // cloned/pool rows can keep vertices from a different font atlas.
        Patch(harmony, typeof(Text), "OnPopulateMesh", nameof(UguiEnsure));
        Patch(harmony, typeof(Text), "UpdateGeometry", nameof(UguiEnsure));
        Type? commented = AccessTools.TypeByName("Torappu.UI.UICommentedText");
        if (commented != null)
        {
            Patch(harmony, commented, "OnPopulateMesh", nameof(UguiEnsure));
            UguiAtlasCache.Enable(harmony, commented);
        }
        // Also cover preferred-size queries.
        Patch(harmony, typeof(Text), "GetGenerationSettings", nameof(UguiEnsure));
        Patch(harmony, typeof(TMP_Text), "set_font", nameof(TmpFontAssigned), true);
        Patch(harmony, typeof(TMP_Text), "set_text", nameof(TmpEnsure), true);
        Patch(harmony, typeof(TMP_Text), "ParseInputText", nameof(TmpEnsure));
        foreach (Type type in new[] { typeof(TextMeshProUGUI), typeof(TextMeshPro) })
        {
            Patch(harmony, type, "OnEnable", nameof(TmpEnsure), true);
            Patch(harmony, type, "GenerateTextMesh", nameof(TmpEnsure));
        }
        Type info = typeof(AutoTranslationPlugin).Assembly.GetType("XUnity.AutoTranslator.Plugin.Core.TextTranslationInfo", true)!;
        // Also neutralizes blanket replacement in older installed INI files.
        Patch(harmony, info, "ChangeFont", nameof(ChangeFontPrefix));
        enabled = true;
        foreach (Text text in Resources.FindObjectsOfTypeAll<Text>())
            if (text.gameObject.scene.IsValid()) UguiEnsure(text);
        foreach (TMP_Text text in Resources.FindObjectsOfTypeAll<TMP_Text>())
            if (text.gameObject.scene.IsValid()) TmpEnsure(text);
        log.LogInfo("Adaptive CN font coverage ready: preserve original faces/materials; replace only incomplete Chinese text; restore faces on reused numeric/Latin UI.");
    }

    private static void Patch(Harmony harmony, Type type, string method, string hook, bool postfix = false)
    {
        var target = AccessTools.Method(type, method, method == "OnPopulateMesh" ? new[] { typeof(VertexHelper) } : null)
            ?? throw new MissingMethodException(type.FullName, method);
        HarmonyMethod patch = new(typeof(AdaptiveFontPolicy), hook);
        if (postfix) harmony.Patch(target, postfix: patch);
        else harmony.Patch(target, prefix: patch);
    }

    private static void UguiFontAssigned(Text __instance)
    {
        if (applying != 0 || __instance == null) return;
        if (!uguiStates.TryGetValue(__instance.GetInstanceID(), out var state)) return;
        state.Original = state.Applied = __instance.font;
        state.LastText = null;
    }

    private static void TmpFontAssigned(TMP_Text __instance)
    {
        if (applying != 0 || __instance == null) return;
        if (!tmpStates.TryGetValue(__instance.GetInstanceID(), out var state)) return;
        state.Original = state.Applied = __instance.font;
        state.OriginalMaterial = state.AppliedMaterial = __instance.fontSharedMaterial;
        state.LastText = null;
    }

    private static void UguiEnsure(Text __instance)
    {
        if (applying != 0 || __instance == null) return;
        applying++; // Text getters can synchronously re-enter through XUnity.
        try
        {
            Cleanup();
            int id = __instance.GetInstanceID();
            Font? current = __instance.font;
            if (!uguiStates.TryGetValue(id, out var state))
                uguiStates[id] = state = new() { Component = __instance, Original = current, Applied = current };
            if (current != state.Applied)
            {
                state.Original = current; // Serialized field/theme update bypassed the setter.
                state.LastText = null;
            }
            string text = __instance.text ?? "";
            if (state.LastText != text)
            {
                state.NeedsCn = NativeFontCoverage.NeedsCnFont(state.Original, text);
                state.LastText = text;
            }
            Font? desired = state.NeedsCn ? CnFontOverride.Replacement(state.Original?.name, text) ?? state.Original : state.Original;
            applying++;
            try
            {
                if (__instance.font != desired)
                {
                    __instance.font = desired;
                    __instance.cachedTextGenerator.Invalidate();
                    __instance.cachedTextGeneratorForLayout.Invalidate();
                    __instance.SetAllDirty();
                }
            }
            finally { applying--; }
            state.Applied = __instance.font;
        }
        catch (Exception exception) { LogError(exception); }
        finally { applying--; }
    }

    private static void TmpEnsure(TMP_Text __instance)
    {
        if (applying != 0 || __instance == null) return;
        applying++; // Text getters can synchronously re-enter through XUnity.
        try
        {
            Cleanup();
            int id = __instance.GetInstanceID();
            TMP_FontAsset? current = __instance.font;
            Material? material = __instance.fontSharedMaterial;
            if (!tmpStates.TryGetValue(id, out var state))
                tmpStates[id] = state = new() { Component = __instance, Original = current, Applied = current,
                    OriginalMaterial = material, AppliedMaterial = material };
            if (current != state.Applied)
            {
                state.Original = current;
                state.OriginalMaterial = material;
                state.LastText = null;
            }
            else if (material != state.AppliedMaterial) state.OriginalMaterial = material;
            string text = __instance.text ?? "";
            if (state.LastText != text)
            {
                // Inspect the primary face, without mixing regional fallback
                // glyphs into one Chinese string. Dynamic faces may grow first.
                state.NeedsCn = FontCoveragePolicy.NeedsCnFont(text,
                    code => state.Original != null && HasTmpGlyph(state.Original, code));
                state.LastText = text;
            }
            TMP_FontAsset? desired = state.NeedsCn ? CnFontOverride.TmpReplacement(state.Original?.name, text) ?? state.Original : state.Original;
            Material? desiredMaterial = state.OriginalMaterial;
            if (desired != state.Original && desired != null)
            {
                if (state.CnMaterial == null || state.MaterialFace != desired)
                {
                    if (state.CnMaterial != null) UnityEngine.Object.Destroy(state.CnMaterial);
                    state.CnMaterial = new Material(desired.material) { name = "CN glyphs / original UI style" };
                    state.CnMaterial.hideFlags = HideFlags.DontUnloadUnusedAsset;
                    state.MaterialFace = desired;
                }
                CopyAppearance(state.OriginalMaterial, state.CnMaterial, desired.material);
                desiredMaterial = state.CnMaterial;
            }
            applying++;
            try
            {
                if (__instance.font != desired) __instance.font = desired;
                if (__instance.fontSharedMaterial != desiredMaterial) __instance.fontSharedMaterial = desiredMaterial;
            }
            finally { applying--; }
            state.Applied = __instance.font;
            state.AppliedMaterial = __instance.fontSharedMaterial;
        }
        catch (Exception exception) { LogError(exception); }
        finally { applying--; }
    }

    private static readonly string[] Colors = { "_FaceColor", "_OutlineColor", "_UnderlayColor" };
    private static readonly string[] Floats = { "_FaceDilate", "_OutlineWidth", "_OutlineSoftness",
        "_UnderlayOffsetX", "_UnderlayOffsetY", "_UnderlayDilate", "_UnderlaySoftness" };
    private static readonly string[] Keywords = { "OUTLINE_ON", "UNDERLAY_ON", "UNDERLAY_INNER" };

    private static bool HasTmpGlyph(TMP_FontAsset face, int code)
    {
        if (code <= char.MaxValue) return face.HasCharacter((char)code, false, true);
        if (face.HasCharacter(code)) return true;
        face.TryAddCharacters(char.ConvertFromUtf32(code), out _);
        return face.HasCharacter(code);
    }

    private static void CopyAppearance(Material? source, Material target, Material defaults)
    {
        // Start from CN SDF metrics/atlas/shader. Transfer only visual effects,
        // not the regional atlas texture, gradient scale or glyph weights.
        foreach (string property in Colors)
            if (target.HasProperty(property))
                target.SetColor(property, source != null && source.HasProperty(property)
                    ? source.GetColor(property) : defaults.GetColor(property));
        foreach (string property in Floats)
            if (target.HasProperty(property))
                target.SetFloat(property, source != null && source.HasProperty(property)
                    ? source.GetFloat(property) : defaults.GetFloat(property));
        foreach (string keyword in Keywords)
            if (source != null && source.IsKeywordEnabled(keyword)) target.EnableKeyword(keyword);
            else target.DisableKeyword(keyword);
    }

    private static bool ChangeFontPrefix(object ui)
    {
        if (ui is not Il2CppObjectBase obj) return true;
        Text? ugui = obj.TryCast<Text>();
        if (ugui != null) { UguiEnsure(ugui); return false; }
        TMP_Text? text = obj.TryCast<TMP_Text>();
        if (text != null) { TmpEnsure(text); return false; }
        return true;
    }

    private static void Cleanup()
    {
        int frame = Time.frameCount;
        if (frame < nextCleanupFrame) return;
        nextCleanupFrame = frame + 600;
        NativeFontCoverage.Cleanup();
        foreach (int key in uguiStates.Where(pair => pair.Value.Component == null).Select(pair => pair.Key).ToArray())
            uguiStates.Remove(key);
        foreach (int key in tmpStates.Where(pair => pair.Value.Component == null).Select(pair => pair.Key).ToArray())
        {
            if (tmpStates[key].CnMaterial != null) UnityEngine.Object.Destroy(tmpStates[key].CnMaterial);
            tmpStates.Remove(key);
        }
    }

    private static void LogError(Exception exception)
    {
        if (errorLogged) return;
        errorLogged = true;
        log.LogWarning($"Could not apply adaptive CN font to a component: {exception}");
    }
}
