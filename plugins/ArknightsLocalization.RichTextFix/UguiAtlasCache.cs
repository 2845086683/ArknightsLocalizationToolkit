using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using Il2CppInterop.Runtime.InteropTypes;
using UnityEngine;
using UnityEngine.UI;

namespace ArknightsLocalization.RichTextFix;

// UICommentedText caches its own UIVertex array, beyond TextGenerator's cache.
// Unity's FontTextureChanged invalidates TextGenerator, but the game's vertex
// cache can still pass its text/size checks and submit UVs from the old atlas.
internal static class UguiAtlasCache
{
    // Snapshot values on the managed side: IL2CPP's settings wrapper contains
    // a Font reference and must not be compared by managed object identity.
    private readonly record struct Layout(int Font, Color Color, int Size, float Spacing,
        bool Rich, float Scale, FontStyle Style, TextAnchor Anchor, bool Geometry,
        bool BestFit, int Min, int Max, bool Bounds, VerticalWrapMode Vertical,
        HorizontalWrapMode Horizontal, Vector2 Extents, Vector2 Pivot, bool OutOfBounds)
    {
        internal static Layout From(TextGenerationSettings s) => new(s.font == null ? 0 : s.font.GetInstanceID(),
            s.color, s.fontSize, s.lineSpacing, s.richText, s.scaleFactor, s.fontStyle, s.textAnchor,
            s.alignByGeometry, s.resizeTextForBestFit, s.resizeTextMinSize, s.resizeTextMaxSize,
            s.updateBounds, s.verticalOverflow, s.horizontalOverflow, s.generationExtents, s.pivot, s.generateOutOfBounds);
    }
    private sealed class State
    {
        internal Text Text = null!;
        internal Layout Settings;
        internal long Revision;
    }
    private static readonly Dictionary<int, long> revisions = new();
    private static readonly Dictionary<int, State> states = new();
    private static long revision;
    internal static long Invalidations;
    private static int nextCleanup;

    internal static void Enable(Harmony harmony, Type commented)
    {
        harmony.Patch(AccessTools.Method(typeof(Font), "InvokeTextureRebuilt_Internal"),
            prefix: new HarmonyMethod(typeof(UguiAtlasCache), nameof(Rebuilt)));
        harmony.Patch(AccessTools.Method(commented, "OnPopulateMesh", new[] { typeof(VertexHelper) })
            ?? throw new MissingMethodException(commented.FullName, "OnPopulateMesh"),
            prefix: new HarmonyMethod(typeof(UguiAtlasCache), nameof(Validate)));
    }

    private static void Rebuilt(Font __0)
    {
        // Advance BEFORE Unity calls FontTextureChanged on dependent text.
        if (__0 != null) revisions[__0.GetInstanceID()] = ++revision;
    }

    private static void Validate(object __instance)
    {
        if (__instance is not Il2CppObjectBase obj) return;
        Text? text = obj.TryCast<Text>();
        if (text == null) return;
        var settings = Layout.From(text.GetGenerationSettings(text.rectTransform.rect.size));
        long current = revisions.GetValueOrDefault(settings.Font);
        int id = text.GetInstanceID();
        bool unchanged = states.TryGetValue(id, out var state) && state.Text == text
            && state.Revision == current && state.Settings.Equals(settings);
        if (state == null) states[id] = state = new() { Text = text };
        state.Text = text;
        state.Settings = settings;
        state.Revision = current;
        if (Time.frameCount >= nextCleanup)
        {
            nextCleanup = Time.frameCount + 600;
            foreach (int key in states.Where(pair => pair.Value.Text == null).Select(pair => pair.Key).ToArray()) states.Remove(key);
        }
        if (unchanged) return;
        Invalidations++;
        text.GetIl2CppType().GetField("m_tempVerts", (Il2CppSystem.Reflection.BindingFlags)54)?.SetValue(text, null);
    }
}
