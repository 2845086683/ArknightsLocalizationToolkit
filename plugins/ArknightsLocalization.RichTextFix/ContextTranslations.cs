using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json;

namespace ArknightsLocalization.RichTextFix;

internal static class ContextTranslations
{
    private static Dictionary<string, Dictionary<string, string>> catalog = new();

    internal static void Load()
    {
        using Stream stream = typeof(ContextTranslations).Assembly.GetManifestResourceStream("ArknightsLocalization.ContextTranslations")!;
        using GZipStream gzip = new(stream, CompressionMode.Decompress);
        catalog = JsonSerializer.Deserialize<Dictionary<string, Dictionary<string, string>>>(gzip)!;
        if (catalog.TryGetValue("dynamic", out var templates)) DynamicTranslations.Load(templates.Values);
    }

    internal static bool IsTarget(string scope, string value) => catalog.TryGetValue(scope, out var entries)
        && entries.Values.Any(target => RichTextStyleRestorer.PlainText(target).Trim() == value);
    internal static bool IsAmbiguous(string scope, string value) => catalog.TryGetValue("ambiguous:" + scope, out var entries)
        && entries.ContainsKey(RichTextStyleRestorer.PlainText(value).Trim());

    internal static string? Lookup(string scope, string original)
    {
        string plain = RichTextStyleRestorer.PlainText(original).Trim();
        if (catalog.TryGetValue(scope, out var entries) && entries.TryGetValue(plain, out string? target)) return target;
        if (!catalog.TryGetValue("number:" + scope, out var formats)) return null;
        string? result = null;
        foreach (var format in formats)
        {
            int slot = format.Key.IndexOf("{0}", StringComparison.Ordinal);
            if (slot < 0) continue;
            string prefix = format.Key[..slot], suffix = format.Key[(slot + 3)..];
            if (!plain.StartsWith(prefix, StringComparison.Ordinal) || !plain.EndsWith(suffix, StringComparison.Ordinal)) continue;
            int length = plain.Length - prefix.Length - suffix.Length;
            if (length is < 1 or > 6) continue;
            string number = plain.Substring(prefix.Length, length);
            if (!number.All(c => c is >= '0' and <= '9')) continue;
            string candidate = format.Value.Replace("{0}", number, StringComparison.Ordinal);
            if (result != null && result != candidate) return null;
            result = candidate;
        }
        return result;
    }

    internal static string? ScopeForType(string type) => type switch
    {
        "Torappu.UI.CharacterInfo.CharacterEvolveTextContainer" => "promotion-summary",
        "Torappu.UI.CharacterInfo.CharacterEvolveDetailNewSkillView" or
        "Torappu.UI.CharacterInfo.CharacterEvolveDetailNormalText" or
        "Torappu.UI.CharacterInfo.CharacterEvolveDetailSingleLineText" => "promotion-detail",
        "Torappu.UI.CharacterInfo.CharacterInfoSkillView" => "skill",
        "Torappu.UI.CharacterInfo.CharacterInfoRightTalentView" or
        "Torappu.UI.CharacterInfo.CharacterInfoRightProfTalentView" or
        "Torappu.UI.CharacterInfo.CharacterInfoTalentContentItem" or
        "Torappu.UI.CharacterInfo.CharacterInfoTalentUnlockNotifyView" or
        "Torappu.UI.CharacterInfo.CharacterTokenTalentItemView" => "talent",
        _ => null
    };
}
