using System;
using System.Linq;
using System.Text;

namespace ArknightsLocalization.RichTextFix;

internal static class FontCoveragePolicy
{
    internal static bool PreferSong(string? name) => name != null &&
        (name.Contains("方正特雅宋", StringComparison.Ordinal) || name.Contains("FZTeYaSong", StringComparison.OrdinalIgnoreCase));

    internal static bool PreferHeavy(string? name) => name != null &&
        (name.EndsWith("-Heavy", StringComparison.OrdinalIgnoreCase)
         || name.EndsWith("-Bold", StringComparison.OrdinalIgnoreCase)
         || name.EndsWith("-Black", StringComparison.OrdinalIgnoreCase));

    internal static bool NeedsCnFont(string text, Func<int, bool> hasGlyph)
    {
        string visible = RichTextStyleRestorer.PlainText(text);
        // Numeric counters, English names, abbreviations and icon fonts keep
        // their original face. Markup attributes are not visible characters.
        if (!visible.EnumerateRunes().Any(rune => IsHan(rune.Value))) return false;
        foreach (Rune rune in visible.EnumerateRunes())
            if ((IsHan(rune.Value) || rune.Value is >= 0x3000 and <= 0x303f or >= 0xff01 and <= 0xff65)
                && !hasGlyph(rune.Value)) return true;
        return false;
    }

    private static bool IsHan(int code) => code is >= 0x3400 and <= 0x4dbf or >= 0x4e00 and <= 0x9fff
        or >= 0xf900 and <= 0xfaff or >= 0x20000 and <= 0x323af;
}
