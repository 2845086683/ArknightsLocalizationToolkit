using Il2CppInterop.Runtime.InteropTypes;
using UnityEngine;
using UnityEngine.UI;

namespace ArknightsLocalization.RichTextFix;

internal static class ComponentContext
{
    internal static string? LookupId(object component, string original)
    {
        if (component is not Il2CppObjectBase obj) return null;
        Text? text = obj.TryCast<Text>();
        return text != null && !string.IsNullOrEmpty(text.textId)
            ? ContextTranslations.Lookup("id:" + text.textId, original) : null;
    }

    internal static string? Lookup(object component, string original, bool promotionOnly)
    {
        if (promotionOnly && ContextTranslations.Lookup("promotion-summary", original) == null
            && ContextTranslations.Lookup("promotion-detail", original) == null) return null;
        if (component is not Il2CppObjectBase obj) return null;
        Component? native = obj.TryCast<Component>();
        if (native == null) return null;
        if (!promotionOnly)
        {
            string? keyed = LookupId(component, original);
            if (keyed != null) return keyed;
            if (ContextTranslations.Lookup("skill", original) == null
                && ContextTranslations.Lookup("talent", original) == null
                && ContextTranslations.Lookup("operator", original) == null) return null;
        }
        // Pooled rows can change parents. Do not cache source-only results.
        for (Transform? parent = native.transform; parent != null; parent = parent.parent)
        {
            foreach (MonoBehaviour owner in parent.GetComponents<MonoBehaviour>())
            {
                if (owner == null) continue;
                string? scope = ContextTranslations.ScopeForType(owner.GetIl2CppType().FullName);
                if (scope == null || promotionOnly != scope.StartsWith("promotion-")) continue;
                string? target = ContextTranslations.Lookup(scope, original);
                if (target != null) return target;
            }
        }
        return null;
    }
}
