using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ArknightsLocalization.RichTextFix;

internal static class DynamicTranslations
{
    private sealed class Rule
    {
        internal string Scope = "", Target = "", Anchor = "";
        internal Regex Pattern = null!;
        internal Dictionary<string, string> Roles = new();
        internal Dictionary<string, string> Groups = new();
    }
    private static readonly Regex Slot = new(@"\{(?<key>\d+|@nickname)(?::[^{}]+)?\}");
    private static readonly Regex Number = new(@"\A[+\-]?[0-9][0-9,.]*(?:%|K|M|万|億|亿)?\z");
    private static readonly List<Rule> rules = new();

    internal static void Load(IEnumerable<string> records)
    {
        rules.Clear();
        foreach (string record in records)
        {
            using JsonDocument json = JsonDocument.Parse(record);
            var root = json.RootElement;
            Rule rule = new() { Scope = root.GetProperty("scope").GetString()!, Target = root.GetProperty("target").GetString()!,
                Roles = JsonSerializer.Deserialize<Dictionary<string, string>>(root.GetProperty("roles").GetRawText())! };
            string source = Normalize(RichTextStyleRestorer.PlainText(root.GetProperty("source").GetString()!));
            int cursor = 0;
            string pattern = "\\A";
            foreach (Match slot in Slot.Matches(source))
            {
                string literal = source[cursor..slot.Index];
                if (literal.Length > rule.Anchor.Length) rule.Anchor = literal;
                pattern += Regex.Escape(literal);
                string key = slot.Groups["key"].Value;
                if (rule.Groups.TryGetValue(key, out string? group)) pattern += "\\k<" + group + ">";
                else
                {
                    group = "p" + rule.Groups.Count;
                    rule.Groups[key] = group;
                    pattern += "(?<" + group + ">[^\\r\\n]{1,128}?)";
                }
                cursor = slot.Index + slot.Length;
            }
            string tail = source[cursor..];
            if (tail.Length > rule.Anchor.Length) rule.Anchor = tail;
            rule.Pattern = new Regex(pattern + Regex.Escape(tail) + "\\z", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(30));
            rules.Add(rule);
        }
    }

    internal static string Normalize(string text) => text.Replace("\\n", "\n").Replace("\r\n", "\n").Trim();

    internal static string? Lookup(string scope, string original)
    {
        string plain = Normalize(RichTextStyleRestorer.PlainText(original));
        string? result = null;
        foreach (Rule rule in rules)
        {
            if (rule.Scope != scope || !plain.Contains(rule.Anchor, StringComparison.Ordinal)) continue;
            Match match;
            try { match = rule.Pattern.Match(plain); }
            catch (RegexMatchTimeoutException) { continue; }
            if (!match.Success) continue;
            Dictionary<string, string> values = new();
            bool valid = true;
            foreach (var entry in rule.Groups)
            {
                string value = match.Groups[entry.Value].Value;
                string role = rule.Roles[entry.Key];
                if (role == "number" && !Number.IsMatch(value)) { valid = false; break; }
                if (role is not ("identity" or "number"))
                {
                    string? translated = ContextTranslations.Lookup(role, value);
                    if (translated == null && role == "activity-name" && !ContextTranslations.IsTarget(role, value)) { valid = false; break; }
                    value = translated ?? value;
                }
                // Identity arguments are never passed to a translation lookup.
                values[entry.Key] = value;
            }
            if (!valid) continue;
            string candidate = Slot.Replace(rule.Target, slot => values[slot.Groups["key"].Value]);
            if (result != null && result != candidate) return null;
            result = candidate;
        }
        return result;
    }
}
