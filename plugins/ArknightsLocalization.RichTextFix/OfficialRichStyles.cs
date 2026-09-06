using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ArknightsLocalization.RichTextFix;

internal static class OfficialRichStyles
{
    private static Dictionary<string, string> targets = new(StringComparer.Ordinal);
    private static Dictionary<string, string> styles = new(StringComparer.Ordinal);
    private static readonly Regex Macro = new(@"<(?<kind>[@$])(?<name>[^>]+)>|</>", RegexOptions.Compiled);
    private static readonly Regex LineWhitespace = new(@"[ \t]*\n[ \t]*", RegexOptions.Compiled);

    internal static int Load()
    {
        using Stream stream = typeof(OfficialRichStyles).Assembly.GetManifestResourceStream("ArknightsLocalization.CnRichStyles")!;
        using GZipStream gzip = new(stream, CompressionMode.Decompress);
        using JsonDocument document = JsonDocument.Parse(gzip);
        foreach (var item in document.RootElement.GetProperty("targets").EnumerateObject())
            targets[Normalize(item.Name)] = item.Value.GetString()!;
        foreach (var item in document.RootElement.GetProperty("styles").EnumerateObject())
            styles[item.Name] = item.Value.GetString()!;
        return targets.Count;
    }

    private static string Normalize(string text) => LineWhitespace.Replace(
        text.Replace("\\r\\n", "\n").Replace("\\n", "\n").Replace("\r\n", "\n").Replace('\r', '\n'), "\n").Trim();

    internal static bool HasTarget(string translation) => targets.ContainsKey(Normalize(translation));

    internal static string Restore(string original, string translation, Func<string, string?> lookup)
    {
        string resolved = ResolveMacros(original);
        // Only recover an authored CN style after the whole translation was
        // selected. Never use this catalog to translate or alter its wording.
        if (targets.TryGetValue(Normalize(translation), out string? authored)
            && RichTextStyleRestorer.PlainText(translation) == translation)
            return RichTextStyleRestorer.Restore(resolved, authored, lookup);
        return RichTextStyleRestorer.Restore(resolved, translation, lookup);
    }

    internal static string ResolveMacros(string text)
    {
        if (!text.Contains("<@") && !text.Contains("<$")) return text;
        Stack<string> closings = new();
        StringBuilder output = new(text.Length);
        int offset = 0;
        foreach (Match match in Macro.Matches(text))
        {
            output.Append(text, offset, match.Index - offset);
            offset = match.Index + match.Length;
            if (match.Value == "</>")
            {
                if (closings.Count != 0) output.Append(closings.Pop());
            }
            else
            {
                string key = match.Groups["kind"].Value == "$" ? "ba.kw" : match.Groups["name"].Value;
                if (!styles.TryGetValue(key, out string? format)) { closings.Push(""); continue; }
                int marker = format.IndexOf("{0}", StringComparison.Ordinal);
                if (marker < 0) { closings.Push(""); continue; }
                output.Append(format[..marker]);
                closings.Push(format[(marker + 3)..]);
            }
        }
        output.Append(text, offset, text.Length - offset);
        while (closings.Count != 0) output.Append(closings.Pop());
        return output.ToString();
    }
}
