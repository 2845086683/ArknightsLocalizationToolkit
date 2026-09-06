using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
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
    public const string PluginVersion = "1.8.0";

    private const uint GetWindowOwner = 4;
    private const int ShowWindowHide = 0;
    private const int ConsoleHidePollMilliseconds = 100;
    private const int ConsoleHideTimeoutMilliseconds = 120_000;

    private readonly object registrationGate = new();
    private ITranslator? translator;
    private bool registered;
    private int firstTranslationLogged;
    private int firstFragmentFallbackBlockedLogged;

    private static readonly Regex BreakTag = new(
        @"<br\s*/?>",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    private static readonly Regex WhitespaceAroundLineBreak = new(
        @"[ \t]*\n[ \t]*",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private static readonly Regex RuntimeNumber = new(
        @"\A[0-9]+(?:\.[0-9]+)?\z",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public override void Load()
    {
        try { ContextTranslations.Load(); }
        catch (Exception exception) { Log.LogWarning($"Could not load contextual translations: {exception}"); }
        try { Log.LogInfo($"Loaded {OfficialRichStyles.Load()} exact CN rich-text style targets."); }
        catch (Exception exception) { Log.LogWarning($"Could not load CN rich styles; using source span recovery: {exception}"); }
        try
        {
            CnFontOverride.Enable(Log);
        }
        catch (Exception exception)
        {
            Log.LogError($"Could not enable CN font loading: {exception}");
        }
        StartConsoleAutoHide();

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

                try
                {
                    CnFontOverride.EnableComponentCoverage();
                }
                catch (Exception exception)
                {
                    Log.LogError($"Could not enable full CN font coverage: {exception}");
                }
                current.RegisterOnTranslatingCallback(TranslateWholeRichText);
                ComponentTranslationStabilization.Enable();
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

        string? role = ComponentFields.Role(context.Component);
        if (role == "identity")
        {
            context.IgnoreComponent();
            return;
        }
        string? typed = role == null ? null : DynamicTranslations.Lookup(role, original)
            ?? ContextTranslations.Lookup(role, original);
        if (typed == null && role == "operator-autochess" && !ContextTranslations.IsAmbiguous(role, original))
            typed = ContextTranslations.Lookup("operator", original);
        if (typed != null)
        {
            context.OverrideTranslatedText(OfficialRichStyles.Restore(original, typed, _ => null));
            return;
        }
        if (role != null && (ContextTranslations.IsAmbiguous(role, original)
            || (role == "operator-autochess" && ContextTranslations.IsAmbiguous("operator", original))))
        {
            context.IgnoreComponent();
            return;
        }

        string? scoped = ComponentContext.Lookup(context.Component, original, true)
            ?? ComponentContext.LookupId(context.Component, original);
        if (scoped != null)
        {
            context.OverrideTranslatedText(OfficialRichStyles.Restore(original, scoped, _ => null));
            return;
        }
        bool containsMarkup = original.IndexOf('<') >= 0;
        bool containsLineBreak = original.IndexOf('\n') >= 0
            || original.IndexOf('\r') >= 0
            || BreakTag.IsMatch(original);
        if (!containsMarkup && !containsLineBreak)
        {
            // Some game paths resolve/remove data macros before assigning the
            // component. The exact CN target still carries the lost style.
            ITranslator? active = translator;
            if (active != null && active.TryTranslate(original, out string target)
                && target != original && OfficialRichStyles.HasTarget(target))
                context.OverrideTranslatedText(OfficialRichStyles.Restore(original, target, _ => null));
            else if (active != null && (!active.TryTranslate(original, out string existing) || existing == original))
            {
                scoped = ComponentContext.Lookup(context.Component, original, false);
                if (scoped != null) context.OverrideTranslatedText(OfficialRichStyles.Restore(original, scoped, _ => null));
            }
            return;
        }

        string plain = RichTextStyleRestorer.PlainText(original);
        plain = plain.Replace("\\r\\n", "\n", StringComparison.Ordinal)
            .Replace("\\n", "\n", StringComparison.Ordinal)
            .Replace("\\r", "\n", StringComparison.Ordinal)
            .Replace("\r\n", "\n", StringComparison.Ordinal)
            .Replace('\r', '\n');
        plain = WhitespaceAroundLineBreak.Replace(plain, "\n").Trim();

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
        else if (TryTranslateSargonAgreement(plain, out string sargonTranslation))
        {
            // Sargon's in-battle description is the only current agreement
            // that formats a stack-dependent duration before assigning the
            // rich-text component. XUnity's component callback can miss the
            // generated regex entry depending on the active translation
            // scope, so handle this one official, tightly identified format
            // without relaxing fragment fallback for any other component.
            translation = sargonTranslation;
        }
        else if (containsLineBreak && TryTranslateLines(current, plain, out string lineTranslation))
        {
            // Operator tags and module-enhanced traits are assembled into one
            // TMP component from independently translated data fields. Fall
            // back to an all-or-nothing line lookup when no composite key is
            // present, so the UI never shows a Chinese/English mixture.
            translation = lineTranslation;
        }

        if (string.IsNullOrWhiteSpace(translation))
            translation = ComponentContext.Lookup(context.Component, original, false);

        if (!string.IsNullOrWhiteSpace(translation))
        {
            // Keep the whole-string lookup, then transfer only styles whose
            // target spans can be identified without guessing word order.
            // A no-op translation must not strip literal markup in <noparse>.
            if (translation == plain) { context.IgnoreComponent(); return; }
            context.OverrideTranslatedText(OfficialRichStyles.Restore(original, translation,
                fragment => current.TryTranslate(fragment, out string result) ? result : null));
            if (Interlocked.Exchange(ref firstTranslationLogged, 1) == 0)
            {
                Log.LogInfo("Applied the first whole rich-text translation.");
            }
        }
        else if (containsMarkup)
        {
            // Returning the default behaviour here lets XUnity split the
            // component around its rich-text tags. Auto Chess descriptions
            // are assembled dynamically and reuse one TMP component, so a
            // partial hit can leave mismatched closing tags and poison later
            // agreement selections. Keep this update untranslated instead;
            // the next text assigned to the component is evaluated afresh.
            context.IgnoreComponent();
            if (Interlocked.Exchange(ref firstFragmentFallbackBlockedLogged, 1) == 0)
            {
                Log.LogInfo("Blocked XUnity fragment fallback for an unmatched rich-text component.");
            }
        }
    }

    private void StartConsoleAutoHide()
    {
        if (!OperatingSystem.IsWindows() || GetConsoleWindow() == IntPtr.Zero)
        {
            return;
        }

        Thread worker = new(HideConsoleAfterGameWindowAppears)
        {
            IsBackground = true,
            Name = "ArknightsLocalization.ConsoleAutoHide",
        };
        worker.Start();
    }

    private void HideConsoleAfterGameWindowAppears()
    {
        try
        {
            IntPtr consoleWindow = GetConsoleWindow();
            if (consoleWindow == IntPtr.Zero)
            {
                return;
            }

            uint processId = unchecked((uint)Process.GetCurrentProcess().Id);
            int attempts = ConsoleHideTimeoutMilliseconds / ConsoleHidePollMilliseconds;
            for (int attempt = 0; attempt < attempts; attempt++)
            {
                if (HasVisibleGameWindow(processId, consoleWindow))
                {
                    ShowWindowAsync(consoleWindow, ShowWindowHide);
                    Log.LogInfo("Game window detected; BepInEx console moved to the background.");
                    return;
                }
                Thread.Sleep(ConsoleHidePollMilliseconds);
            }
            Log.LogWarning("Timed out waiting for the game window; leaving the BepInEx console visible.");
        }
        catch (Exception exception)
        {
            // Console presentation is cosmetic. Never let it affect the
            // translation callback or game startup when Windows APIs fail.
            Log.LogWarning($"Could not auto-hide the BepInEx console: {exception.Message}");
        }
    }

    private static bool HasVisibleGameWindow(uint processId, IntPtr consoleWindow)
    {
        bool found = false;
        EnumWindows(
            (window, _) =>
            {
                if (window == consoleWindow
                    || !IsWindowVisible(window)
                    || GetWindow(window, GetWindowOwner) != IntPtr.Zero)
                {
                    return true;
                }

                GetWindowThreadProcessId(window, out uint windowProcessId);
                if (windowProcessId != processId)
                {
                    return true;
                }

                found = true;
                return false;
            },
            IntPtr.Zero);
        return found;
    }

    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetConsoleWindow();

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern IntPtr GetWindow(IntPtr window, uint command);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShowWindowAsync(IntPtr window, int command);

    private static bool TryTranslateSargonAgreement(string text, out string translation)
    {
        const string japanesePrefix = "【サルゴン】所属者のスキル発動時、";
        const string japaneseDurationSuffix = "秒間全ての【サルゴン】所属者の攻撃速度+12";
        const string japaneseEffectMarker =
            "<戦場に異なる【サルゴン】所属者を6名配置>上述の効果持続中、" +
            "全ての【サルゴン】所属者の攻撃力+12%（最大+300%）";

        const string englishPrefix =
            "When any [Sargon] Operator activates a skill, all [Sargon] Operators " +
            "+12 ASPD (up to +300), lasting for ";
        const string englishDurationSuffix = " seconds (affected by no. of stacks)";
        const string englishEffectMarker =
            "Skill activations will also cause all [Sargon] Operators to gain " +
            "+12% ATK (up to +300%)";

        string duration;
        bool includesStrategy;
        if (text.StartsWith(japanesePrefix, StringComparison.Ordinal)
            && text.Contains(japaneseEffectMarker, StringComparison.Ordinal)
            && TryExtractDuration(
                text,
                japanesePrefix.Length,
                japaneseDurationSuffix,
                out duration))
        {
            includesStrategy = text.Contains("戦術【ナラントゥヤ】", StringComparison.Ordinal);
        }
        else if (text.StartsWith(englishPrefix, StringComparison.Ordinal)
            && text.Contains("<With 6 different [Sargon] Operators on ", StringComparison.Ordinal)
            && text.Contains(englishEffectMarker, StringComparison.Ordinal)
            && TryExtractDuration(
                text,
                englishPrefix.Length,
                englishDurationSuffix,
                out duration))
        {
            includesStrategy = text.Contains(
                "When using Narantuya's Strategy",
                StringComparison.Ordinal);
        }
        else
        {
            translation = string.Empty;
            return false;
        }

        translation =
            "【萨尔贡】干员开启技能时，所有【萨尔贡】干员攻击速度+12（至多+300），持续" +
            duration +
            "秒（受层数影响）\n" +
            "<在场6名不同【萨尔贡】干员>开启技能还会使所有【萨尔贡】干员攻击力+12%（至多+300%）";
        if (includesStrategy)
        {
            translation +=
                "\n选择策略【娜仁图亚】时，<在场6名不同【萨尔贡】干员>的效果会有所改变";
        }
        return true;
    }

    private static bool TryExtractDuration(
        string text,
        int startIndex,
        string suffix,
        out string duration)
    {
        int endIndex = text.IndexOf(suffix, startIndex, StringComparison.Ordinal);
        if (endIndex <= startIndex)
        {
            duration = string.Empty;
            return false;
        }

        duration = text.Substring(startIndex, endIndex - startIndex).Trim();
        if (!RuntimeNumber.IsMatch(duration))
        {
            duration = string.Empty;
            return false;
        }
        return true;
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
