using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.TextCore.LowLevel;

namespace ArknightsLocalization.RichTextFix;

internal static class NativeFontCoverage
{
    private sealed class Coverage
    {
        internal Font Font = null!;
        internal readonly Dictionary<int, bool> Glyphs = new();
    }
    private static readonly Dictionary<int, Coverage> fonts = new();

    internal static bool NeedsCnFont(Font? original, string text)
    {
        if (original == null) return FontCoveragePolicy.NeedsCnFont(text, _ => false);
        int id = original.GetInstanceID();
        if (!fonts.TryGetValue(id, out var coverage)) fonts[id] = coverage = new() { Font = original };
        bool loaded = false, usable = false;
        return FontCoveragePolicy.NeedsCnFont(text, code =>
        {
            if (coverage.Glyphs.TryGetValue(code, out bool available)) return available;
            if (!original.dynamic)
                available = original.characterInfo.Any(character => character.index == code);
            else
            {
                // Font.HasCharacter can report OS/Unity fallback coverage (even
                // Arial appeared Chinese-capable in the actual player). Query
                // embedded face glyphs so a mixed regional fallback is not
                // mistaken for a complete original Chinese typeface.
                if (!loaded)
                {
                    usable = FontEngine.LoadFontFace(original, 64) == FontEngineError.Success;
                    loaded = true;
                }
                available = usable && FontEngine.GetGlyphIndex((uint)code) != 0;
            }
            coverage.Glyphs[code] = available;
            return available;
        });
    }

    internal static void Cleanup()
    {
        foreach (int id in fonts.Where(pair => pair.Value.Font == null).Select(pair => pair.Key).ToArray())
            fonts.Remove(id);
    }
}
