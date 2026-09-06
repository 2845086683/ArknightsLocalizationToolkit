using System;
using System.Reflection;
using HarmonyLib;
using Il2CppInterop.Runtime.InteropTypes;
using TMPro;
using UnityEngine;
using UnityEngine.UI;
using XUnity.AutoTranslator.Plugin.Core;

namespace ArknightsLocalization.RichTextFix;

// XUnity's stabilization fast path skips component callbacks. A text that
// starts as an untranslated label can therefore miss its new, pooled role.
// Run the existing callback only for explicitly bound fields, and use XUnity's
// own setter so translation state, reentrancy and font updates stay consistent.
internal static class ComponentTranslationStabilization
{
    private static PropertyInfo setting = null!, ignored = null!, behaviour = null!;
    private static MethodInfo callback = null!, setText = null!;
    [ThreadStatic] private static bool checking;

    internal static void Enable()
    {
        Type plugin = typeof(AutoTranslationPlugin);
        Type info = plugin.Assembly.GetType("XUnity.AutoTranslator.Plugin.Core.TextTranslationInfo", true)!;
        setting = AccessTools.Property(info, "IsCurrentlySettingText");
        ignored = AccessTools.Property(info, "ShouldIgnore");
        behaviour = AccessTools.Property(typeof(ComponentTranslationContext), "Behaviour");
        callback = AccessTools.Method(plugin, "InvokeOnTranslatingCallback");
        setText = AccessTools.Method(plugin, "SetTranslatedText");
        new Harmony("arklocalizer.context.stabilization").Patch(
            AccessTools.Method(plugin, "TranslateImmediate"),
            prefix: new HarmonyMethod(typeof(ComponentTranslationStabilization), nameof(Translate)));
    }

    private static bool Translate(object __instance, object __0, string? __1, object? __2, bool __3, ref string? __result)
    {
        if (checking || __2 == null || __0 is not Il2CppObjectBase component ||
            (bool)setting.GetValue(__2)! || (bool)ignored.GetValue(__2)!) return true;
        checking = true;
        try
        {
            if (!__3 && component.TryCast<Behaviour>() is { } native && !native.isActiveAndEnabled) return true;
            if (ComponentFields.Role(__0) == null) return true;
            string? original = __1 ?? component.TryCast<Text>()?.text ?? component.TryCast<TMP_Text>()?.text;
            if (string.IsNullOrWhiteSpace(original)) return true;
            var context = (ComponentTranslationContext?)callback.Invoke(__instance, new object[] { __0, original, __2 });
            if (context == null) return true;
            int action = Convert.ToInt32(behaviour.GetValue(context));
            if (action == 3) { __result = null; return false; } // IgnoreComponent, including identity fields.
            if (action != 2 || context.OverriddenTranslatedText == null) return true;
            string target = context.OverriddenTranslatedText;
            setText.Invoke(__instance, new object[] { __0, target, original, __2 });
            __result = target;
            return false;
        }
        finally { checking = false; }
    }
}
