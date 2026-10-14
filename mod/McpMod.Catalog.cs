// GET /api/v1/catalog?kind=cards|powers|relics
// 全登録モデルの静的カタログ。card_kb.json 生成(ローカル解析)用で、
// LLM には出さない — description も含めて返してよい内部向けデータ。
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Singleton;

namespace STS2_MCP;

public static partial class McpMod
{
    private static void HandleGetCatalog(HttpListenerRequest request, HttpListenerResponse response)
    {
        try
        {
            var kind = (request.QueryString["kind"] ?? "cards").ToLowerInvariant();
            var dataTask = RunOnMainThread(() => BuildCatalog(kind));
            SendJson(response, dataTask.GetAwaiter().GetResult());
        }
        catch (Exception ex)
        {
            SendError(response, 500, $"Failed to build catalog: {ex.Message}");
        }
    }

    private static object BuildCatalog(string kind)
    {
        return kind switch
        {
            "powers" => new Dictionary<string, object?>
                { ["powers"] = SafeCatalogEntries(ModelDb.AllPowers, BuildPowerEntry) },
            "relics" => new Dictionary<string, object?>
                { ["relics"] = SafeCatalogEntries(ModelDb.AllRelics, BuildRelicEntry) },
            _ => new Dictionary<string, object?>
                { ["cards"] = SafeCatalogEntries(ModelDb.AllCards, BuildCardCatalogEntry) },
        };
    }

    // 個別エントリの失敗がカタログ全体を潰さないよう foreach + try/catch
    private static List<Dictionary<string, object?>> SafeCatalogEntries<T>(
        IEnumerable<T> models, Func<T, Dictionary<string, object?>> build)
    {
        var list = new List<Dictionary<string, object?>>();
        foreach (var m in models)
        {
            try { list.Add(build(m)); }
            catch { /* skip broken entry */ }
        }
        return list;
    }

    private static Dictionary<string, object?> BuildCardCatalogEntry(CardModel card)
    {
        var entry = BuildCardInfo(card, MegaCrit.Sts2.Core.Entities.Cards.PileType.Deck);
        entry["pool"] = SafeGetText(() => card.Pool?.Id.Entry) ?? SafeGetText(() => card.Pool?.Title);
        entry["target_type"] = card.TargetType.ToString();
        entry["max_upgrade"] = card.MaxUpgradeLevel;
        return entry;
    }

    private static Dictionary<string, object?> BuildPowerEntry(PowerModel power)
    {
        MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVarSet? set = null;
        try { set = power.DynamicVars; } catch { }
        return new Dictionary<string, object?>
        {
            ["id"] = power.Id.Entry,
            ["name"] = SafeGetText(() => power.Title),
            ["type"] = power.Type.ToString(),
            ["stack"] = power.StackType.ToString(),
            ["description"] = SafeGetText(() => power.Description),
            ["vars"] = SerializeVarSet(set, null, null),
        };
    }

    private static Dictionary<string, object?> BuildRelicEntry(RelicModel relic)
    {
        MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVarSet? set = null;
        try { set = relic.DynamicVars; } catch { }
        return new Dictionary<string, object?>
        {
            ["id"] = relic.Id.Entry,
            ["name"] = SafeGetText(() => relic.Title),
            ["rarity"] = relic.Rarity.ToString(),
            ["description"] = SafeGetText(() => relic.DynamicDescription),
            ["vars"] = SerializeVarSet(set, null, null),
        };
    }
}
