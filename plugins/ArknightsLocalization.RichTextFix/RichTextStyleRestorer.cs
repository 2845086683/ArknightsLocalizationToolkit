using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace ArknightsLocalization.RichTextFix;

// Transfer styles using textual evidence, never proportional character offsets
// or fragment-by-fragment translation. The authoritative whole translation wins.
internal static class RichTextStyleRestorer
{
    private static readonly Regex Tag = new(
        @"(?<break><br\s*/?>)|<(?<close>/)?(?<name>alpha|align|b|br|color|cspace|font|i|indent|line-height|line-indent|link|lowercase|mark|material|margin|mspace|nobr|noparse|page|pos|rotate|s|size|smallcaps|space|sprite|style|sub|sup|u|uppercase|voffset|width)(?<args>(?:=[^>]*|\s+[^>]*?)?)>|<(?<hex>#[0-9a-f]{6}(?:[0-9a-f]{2})?)>|(?<custom><[@$][^>]*>|</>)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);
    private static readonly Regex Number = new(@"[0-9０-９]+(?:[.．][0-9０-９]+)?[%％]?",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly HashSet<string> Standalone = new(StringComparer.OrdinalIgnoreCase)
        { "br", "sprite", "space", "page" };

    private sealed class SpanStyle
    {
        internal string Name = "", Open = "", Close = "";
        internal int Start, End, Order;
    }
    private sealed class Document
    {
        internal string Plain = "";
        internal readonly List<SpanStyle> Styles = new();
        internal readonly List<(int Position, string Tag)> Inline = new();
    }
    private readonly record struct Placement(int Start, int End, SpanStyle Style);

    internal static string PlainText(string text) => Parse(text).Plain;

    internal static string Restore(string original, string translation, Func<string, string?> lookup)
    {
        // An authored rich target already knows the correct target-language
        // boundaries. Do not duplicate tags or overwrite its styles.
        Document source = Parse(original);
        if (source.Plain == translation) return original;
        Document targetDocument = Parse(translation);
        if (targetDocument.Styles.Count != 0 || targetDocument.Inline.Count != 0)
        {
            // Preserve a component-level source wrapper (e.g. small disabled
            // label or bold panel heading) around authored inner CN highlights.
            foreach (SpanStyle style in source.Styles.OrderByDescending(s => s.Order))
                if (style.Start == 0 && style.End == source.Plain.Length
                    && !targetDocument.Styles.Any(s => s.Name == style.Name))
                    translation = style.Open + translation + style.Close;
            return translation;
        }
        if (source.Styles.Count == 0 && source.Inline.Count == 0) return translation;
        List<Placement> placements = new();
        foreach (SpanStyle style in source.Styles)
        {
            if (style.End <= style.Start) continue;
            string fragment = source.Plain.Substring(style.Start, style.End - style.Start).Trim();
            if (fragment.Length == 0) continue;
            if (fragment == source.Plain.Trim())
            {
                placements.Add(new(0, translation.Length, style));
                continue;
            }
            string? translated = lookup(fragment);
            if (!string.IsNullOrWhiteSpace(translated)
                && TryPlace(source.Plain, fragment, PlainText(translated).Trim(), translation, style, placements)) continue;
            if (TryPlace(source.Plain, fragment, fragment, translation, style, placements)) continue;

            // Values survive word-order changes. Only place an unambiguous
            // occurrence (or a group entirely covered by this same source span).
            // A link/font/style span must match the full phrase; applying it to
            // an incidental number could change interaction or font semantics.
            if (style.Name is "link" or "font" or "material" or "style" or "noparse") continue;
            foreach (string number in Number.Matches(fragment).Select(m => m.Value).Distinct())
                TryPlace(source.Plain, number, number, translation, style, placements, numeric: true);
        }
        Dictionary<int, List<string>> inline = new();
        foreach (var item in source.Inline)
        {
            int position = -1;
            if (item.Position == 0) position = 0;
            else if (item.Position == source.Plain.Length) position = translation.Length;
            else
            {
                string prefix = source.Plain[..item.Position].Trim();
                string? translatedPrefix = lookup(prefix);
                if (!string.IsNullOrEmpty(translatedPrefix) && translation.StartsWith(translatedPrefix, StringComparison.Ordinal))
                    position = translatedPrefix.Length;
            }
            if (position < 0) continue;
            if (!inline.TryGetValue(position, out var tags)) inline[position] = tags = new();
            tags.Add(item.Tag);
        }
        return Render(translation, placements, inline);
    }

    private static bool TryPlace(string source, string fragment, string translated, string target,
        SpanStyle style, List<Placement> placements, bool numeric = false)
    {
        if (translated.Length == 0) return false;
        List<(int Start, int End)> sourceHits = Find(source, fragment, numeric);
        List<(int Start, int End)> targetHits = Find(target, translated, numeric);
        // Repeated labels/values with different styles are ambiguous after a
        // translation reorders them. Never guess by occurrence order.
        if (sourceHits.Count == 0 || sourceHits.Count != targetHits.Count
            || sourceHits.Any(hit => hit.Start < style.Start || hit.End > style.End)) return false;
        foreach (var hit in targetHits) placements.Add(new(hit.Start, hit.End, style));
        return targetHits.Count > 0;
    }

    private static List<(int Start, int End)> Find(string text, string value, bool numeric)
    {
        List<(int Start, int End)> result = new();
        if (numeric)
        {
            string key = value.Normalize(NormalizationForm.FormKC);
            foreach (Match match in Number.Matches(text))
                if (match.Value.Normalize(NormalizationForm.FormKC) == key)
                    result.Add((match.Index, match.Index + match.Length));
            return result;
        }
        for (int start = 0; start <= text.Length - value.Length;)
        {
            int index = text.IndexOf(value, start, StringComparison.Ordinal);
            if (index < 0) break;
            int end = index + value.Length;
            // Do not style "10" inside "100", or ATK inside a longer word.
            bool left = index > 0 && IsAsciiWord(value[0]) && IsAsciiWord(text[index - 1]);
            bool right = end < text.Length && IsAsciiWord(value[^1]) && IsAsciiWord(text[end]);
            if (!left && !right) result.Add((index, end));
            start = end;
        }
        return result;
    }

    private static bool IsAsciiWord(char c) => c is >= '0' and <= '9' or >= 'a' and <= 'z' or >= 'A' and <= 'Z';

    private static string Render(string text, List<Placement> placements, Dictionary<int, List<string>> inline)
    {
        if (placements.Count == 0 && inline.Count == 0) return text;
        // Sweep boundaries rather than characters. Closing/reopening the common
        // stack makes even overlapping mapped ranges valid, balanced markup.
        var boundaries = placements.SelectMany(p => new[] { p.Start, p.End })
            .Concat(inline.Keys).Append(0).Append(text.Length).Distinct().OrderBy(x => x).ToArray();
        List<SpanStyle> active = new();
        StringBuilder output = new(text.Length + placements.Count * 30);
        for (int i = 0; i < boundaries.Length; i++)
        {
            int position = boundaries[i];
            var next = placements.Where(p => p.Start <= position && position < p.End)
                .Select(p => p.Style).Distinct().OrderBy(s => s.Order).ToList();
            int common = 0;
            while (common < active.Count && common < next.Count && active[common] == next[common]) common++;
            for (int j = active.Count - 1; j >= common; j--) output.Append(active[j].Close);
            for (int j = common; j < next.Count; j++) output.Append(next[j].Open);
            active = next;
            if (inline.TryGetValue(position, out var tags)) foreach (string tag in tags) output.Append(tag);
            if (i + 1 < boundaries.Length) output.Append(text, position, boundaries[i + 1] - position);
        }
        return output.ToString();
    }

    private static Document Parse(string text)
    {
        Document document = new();
        StringBuilder plain = new();
        List<SpanStyle> stack = new();
        bool noParse = false;
        int offset = 0, order = 0;
        foreach (Match match in Tag.Matches(text))
        {
            plain.Append(text, offset, match.Index - offset);
            offset = match.Index + match.Length;
            string name = match.Groups["name"].Value.ToLowerInvariant();
            bool closing = match.Groups["close"].Success;
            if (noParse && !(name == "noparse" && closing)) { plain.Append(match.Value); continue; }
            if (match.Groups["custom"].Success) continue; // Game-resolved markup, not Unity output tags.
            if (match.Groups["hex"].Success) name = "color";
            if (name == "br" || match.Groups["break"].Success) { plain.Append('\n'); continue; }
            if (Standalone.Contains(name))
            {
                if (!closing) document.Inline.Add((plain.Length, match.Value));
                continue;
            }
            if (closing)
            {
                int index = stack.FindLastIndex(style => style.Name == name);
                if (index < 0) continue; // Discard unmatched closing formatting tags.
                for (int i = stack.Count - 1; i >= index; i--)
                {
                    stack[i].End = plain.Length;
                    document.Styles.Add(stack[i]);
                    stack.RemoveAt(i);
                }
                if (name == "noparse") noParse = false;
            }
            else
            {
                stack.Add(new() { Name = name, Open = match.Value, Close = "</" + name + ">", Start = plain.Length, Order = order++ });
                if (name == "noparse") noParse = true;
            }
        }
        plain.Append(text, offset, text.Length - offset);
        // A source can contain an unclosed tag; never emit an unbalanced target.
        foreach (SpanStyle style in stack) { style.End = plain.Length; document.Styles.Add(style); }
        document.Plain = plain.ToString();
        return document;
    }
}
