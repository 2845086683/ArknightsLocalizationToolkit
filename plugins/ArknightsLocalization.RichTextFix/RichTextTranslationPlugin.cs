using System;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using BepInEx;
using BepInEx.Unity.IL2CPP;
using XUnity.AutoTranslator.Plugin.Core;

namespace ArknightsLocalization.RichTextFix;

[BepInPlugin(PluginGuid, PluginName, PluginVersion)]
[BepInDependency(
    "gravydevsupreme.xunity.autotranslator",
    BepInDependency.DependencyFlags.HardDependency)]
public sealed class RichTextTranslationPlugin : BasePlugin
{
    public const string PluginGuid = "arklocalizer.richtextfix";
    public const string PluginName = "Arknights Localization Rich Text Fix";
    public const string PluginVersion = "1.0.2";

    private readonly object registrationGate = new();
    private ITranslator? translator;
    private bool registered;
    private int firstTranslationLogged;

    private static readonly Regex BreakTag = new(
        @"<br\s*/?>",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    private static readonly Regex RichTextTag = new(
        @"<[^>]+>",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public override void Load()
    {
        // XUnity's BepInEx entry point is loaded before this plugin, but its
        // AutoTranslationPlugin.Current singleton is created later by an IL2CPP
        // proxy behaviour. Its public completion event runs after the cache and
        // hooks are ready, on the Unity thread.
        AutoTranslatorState.PluginInitializationCompleted += OnXUnityInitialized;
        if (AutoTranslatorState.PluginInitialized)
        {
            OnXUnityInitialized();
        }
        else
        {
            Log.LogInfo("Waiting for XUnity before enabling whole rich-text lookup.");
        }
    }

    private void OnXUnityInitialized()
    {
        try
        {
            lock (registrationGate)
            {
                if (registered)
                {
                    return;
                }

                ITranslator? current = AutoTranslator.Default;
                if (current is null)
                {
                    Log.LogError("XUnity reported initialization without a translator instance.");
                    return;
                }

                current.RegisterOnTranslatingCallback(TranslateWholeRichText);
                translator = current;
                registered = true;

                AutoTranslatorState.PluginInitializationCompleted -= OnXUnityInitialized;
                Log.LogInfo("Whole rich-text lookup enabled for Arknights skill descriptions.");
            }
        }
        catch (Exception exception)
        {
            // XUnity catches event-subscriber exceptions as well, but keeping
            // this boundary produces a clear plugin-specific diagnostic.
            Log.LogError($"Could not register rich-text lookup: {exception}");
        }
    }

    private void TranslateWholeRichText(ComponentTranslationContext context)
    {
        string original = context.OriginalText;
        if (string.IsNullOrWhiteSpace(original))
        {
            return;
        }

        bool containsMarkup = original.IndexOf('<') >= 0;
        bool containsLineBreak = original.IndexOf('\n') >= 0
            || original.IndexOf('\r') >= 0
            || BreakTag.IsMatch(original);
        if (!containsMarkup && !containsLineBreak)
        {
            // Let XUnity's normal exact lookup handle ordinary one-line text.
            return;
        }

        string plain = BreakTag.Replace(original, "\n");
        plain = RichTextTag.Replace(plain, string.Empty);
        plain = plain.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');

        ITranslator? current = translator;
        if (current is null)
        {
            return;
        }

        string? translation = null;
        if (current.TryTranslate(plain, out string wholeTranslation)
            && !string.IsNullOrWhiteSpace(wholeTranslation))
        {
            translation = wholeTranslation;
        }
        else if (containsLineBreak && TryTranslateLines(current, plain, out string lineTranslation))
        {
            // Operator tags and module-enhanced traits are assembled into one
            // TMP component from independently translated data fields. Fall
            // back to an all-or-nothing line lookup when no composite key is
            // present, so the UI never shows a Chinese/English mixture.
            translation = lineTranslation;
        }

        if (!string.IsNullOrWhiteSpace(translation))
        {
            // A whole-string replacement is intentional. Reusing the original
            // rich-text spans would color the wrong words when CN reorders a
            // value or keyword relative to JP/EN.
            context.OverrideTranslatedText(translation);
            if (Interlocked.Exchange(ref firstTranslationLogged, 1) == 0)
            {
                Log.LogInfo("Applied the first whole rich-text translation.");
            }
        }
    }

    private static bool TryTranslateLines(ITranslator current, string text, out string translation)
    {
        string[] lines = text.Split('\n');
        if (lines.Length < 2)
        {
            translation = string.Empty;
            return false;
        }

        bool translatedAny = false;
        StringBuilder builder = new(text.Length);
        for (int index = 0; index < lines.Length; index++)
        {
            if (index > 0)
            {
                builder.Append('\n');
            }

            string line = lines[index].Trim();
            if (line.Length == 0)
            {
                continue;
            }
            if (!current.TryTranslate(line, out string translatedLine)
                || string.IsNullOrWhiteSpace(translatedLine))
            {
                translation = string.Empty;
                return false;
            }
            builder.Append(translatedLine);
            translatedAny = true;
        }
        translation = builder.ToString();
        return translatedAny;
    }
}
