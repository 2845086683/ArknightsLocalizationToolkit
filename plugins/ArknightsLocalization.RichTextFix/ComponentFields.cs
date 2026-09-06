using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using Il2CppInterop.Runtime.InteropTypes;
using Il2CppInterop.Runtime.InteropTypes.Arrays;
using UnityEngine;

namespace ArknightsLocalization.RichTextFix;

// Fields are explicitly reviewed against both clients' native metadata. A
// field must point to this exact text object; proximity to a page is not enough.
internal static class ComponentFields
{
    private static readonly Dictionary<string, Dictionary<string, string>> roles = Load();
    private static readonly Dictionary<string, List<(Il2CppSystem.Reflection.FieldInfo Field, string Role)>> bindings = new();
    private static Dictionary<string, Dictionary<string, string>> Load()
    {
        using Stream stream = typeof(ComponentFields).Assembly.GetManifestResourceStream("ArknightsLocalization.ComponentFields")!;
        return JsonSerializer.Deserialize<Dictionary<string, Dictionary<string, string>>>(stream)!;
    }

    internal static string? Role(object component)
    {
        if (component is not Il2CppObjectBase obj) return null;
        Component? text = obj.TryCast<Component>();
        if (text == null) return null;
        for (Transform? parent = text.transform; parent != null; parent = parent.parent)
        foreach (MonoBehaviour owner in parent.GetComponents<MonoBehaviour>())
        {
            if (owner == null) continue;
            var ownerType = owner.GetIl2CppType();
            if (!bindings.TryGetValue(ownerType.FullName, out var resolved))
            {
                resolved = new();
                for (Il2CppSystem.Type? type = ownerType; type != null; type = type.BaseType)
                {
                    string name = (type.IsGenericType ? type.GetGenericTypeDefinition() : type).FullName;
                    if (!roles.TryGetValue(name, out var fields)) continue;
                    foreach (var entry in fields)
                    {
                        var field = type.GetField(entry.Key, (Il2CppSystem.Reflection.BindingFlags)54); // DeclaredOnly, Instance, Public, NonPublic
                        if (field != null) resolved.Add((field, entry.Value));
                    }
                }
                bindings[ownerType.FullName] = resolved;
            }
            // Cache metadata only. References can change when game objects are pooled.
            foreach (var entry in resolved)
            {
                var reference = entry.Field.GetValue(owner);
                var value = reference?.TryCast<Component>();
                if (value != null && value == text) return entry.Role;
                // Only explicitly catalogued Text[] fields are eligible. Read
                // current elements each time: pooled panels replace these arrays.
                if (reference != null && entry.Field.FieldType.IsArray &&
                    entry.Field.FieldType.GetElementType().FullName == "UnityEngine.UI.Text")
                    foreach (var element in new Il2CppReferenceArray<UnityEngine.UI.Text>(reference.Pointer))
                        if (element != null && element == text) return entry.Role;
            }
        }
        return null;
    }
}
