// アクショントレース: カードプレイ時に流れた Cmd を記録して
// 「このカードが実際に何をしたか」の正本を作る。
// GET /api/v1/trace?drain=1 で吸い上げ、Python 側が card_kb.json に
// prov="trace" として統合する。
//
// 帰属: Cmd の CardModel/CardPlay 引数があればそれ、無ければ
// OnPlayWrapper のコンテキスト(直近で実行中のカード)を使う。
// Cmd が非同期で後から実行された場合に誤帰属しうるので conf は
// trace=0.9 に留める(実行値は正確だが帰属は近似)。
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Singleton;

namespace STS2_MCP;

public static partial class McpMod
{
    private const int TraceCapacity = 4000;
    private static readonly List<Dictionary<string, object?>> _traceBuffer = new();
    private static readonly object _traceLock = new();

    // OnPlayWrapper の実行中カード(0=コンテキスト外)
    private static string? _ctxCard;
    private static int _ctxDepth;

    private static void TracePush(string op, object[] args)
    {
        string? card = null;
        string? tgt = null;
        decimal? n = null;
        string? pid = null;
        string? extra = null;

        foreach (var a in args)
        {
            switch (a)
            {
                case null:
                    break;
                case CardModel cm:
                    card ??= SafeGetText(() => cm.Id.Entry);
                    break;
                case CardPlay cp:
                    card ??= SafeGetText(() => cp.Card?.Id.Entry);
                    break;
                case PowerModel pm:
                    pid ??= SafeGetText(() => pm.Id.Entry);
                    break;
                case OrbModel om:
                    extra ??= SafeGetText(() => om.Id.Entry);
                    break;
                case IEnumerable<Creature> many:
                    tgt ??= string.Join(",", many.Select(CreatureTag));
                    break;
                case Creature c:
                    tgt ??= CreatureTag(c);
                    break;
                case Player p:
                    tgt ??= "player:" + SafeGetText(() => p.Character?.Id.Entry);
                    break;
                case DynamicVar dv:
                    n ??= SafeDecimal(() => dv.PreviewValue);
                    break;
                case decimal d:
                    n ??= d;
                    break;
                case int i:
                    n ??= i;
                    break;
            }
        }

        card ??= _ctxCard;
        lock (_traceLock)
        {
            _traceBuffer.Add(new Dictionary<string, object?>
            {
                ["op"] = op,
                ["card"] = card,
                ["n"] = n,
                ["tgt"] = tgt,
                ["pw"] = pid,
                ["x"] = extra,
            });
            if (_traceBuffer.Count > TraceCapacity)
                _traceBuffer.RemoveRange(0, TraceCapacity / 4);
        }
    }

    private static string CreatureTag(Creature c)
    {
        try
        {
            if (c.IsPrimaryEnemy || c.IsSecondaryEnemy)
                return "enemy:" + (SafeGetText(() => c.Monster?.Id.Entry) ?? c.Name ?? "?");
            if (c.IsPet)
                return "pet";
            return "player";
        }
        catch { return "?"; }
    }

    private static decimal? SafeDecimal(Func<decimal> f)
    {
        try { return f(); } catch { return null; }
    }

    private static void HandleGetTrace(HttpListenerResponse response, bool drain)
    {
        List<Dictionary<string, object?>> copy;
        lock (_traceLock)
        {
            copy = new List<Dictionary<string, object?>>(_traceBuffer);
            if (drain)
                _traceBuffer.Clear();
        }
        SendJson(response, new Dictionary<string, object?>
        {
            ["events"] = copy,
            ["ctx_card"] = _ctxCard,
        });
    }

    // --- Harmony patches ---

    // カード効果の入口。コンテキスト(どのカードが今解決中か)を張る。
    [HarmonyPatch(typeof(CardModel), nameof(CardModel.OnPlayWrapper))]
    private static class OnPlayWrapperTracePatch
    {
        static void Prefix(CardModel __instance)
        {
            _ctxDepth++;
            _ctxCard = SafeGetText(() => __instance.Id.Entry);
        }

        static void Finalizer()
        {
            if (--_ctxDepth <= 0)
            {
                _ctxDepth = 0;
                // 非同期 Cmd が遅れて来ることがあるので即クリアせず保持。
                // 次のカード or ターン境界で上書きされる。誤帰属リスクは
                // conf<1.0 で吸収する。
            }
        }
    }

    // Cmd 群をまとめてパッチ。__args から帰属・対象・量を拾う汎用版。
    // 全オーバーロードを対象にする(TargetMethods で列挙)。
    private abstract class CmdTracePatchBase
    {
        protected static IEnumerable<MethodBase> Overloads(Type t, string name)
            => AccessTools.GetDeclaredMethods(t)
                .Where(m => m.Name == name &&
                            !m.IsGenericMethodDefinition &&
                            !m.ContainsGenericParameters);

        protected static void Record(string op, object[] args)
        {
            try { TracePush(op, args); }
            catch { /* トレース失敗でゲームを止めない */ }
        }
    }

    [HarmonyPatch]
    private class DamagePatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "Damage");
        static void Postfix(object[] __args) => Record("damage", __args);
    }

    [HarmonyPatch]
    private class GainBlockPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "GainBlock");
        static void Postfix(object[] __args) => Record("block", __args);
    }

    [HarmonyPatch]
    private class LoseBlockPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "LoseBlock");
        static void Postfix(object[] __args) => Record("lose_block", __args);
    }

    [HarmonyPatch]
    private class HealPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "Heal");
        static void Postfix(object[] __args) => Record("heal", __args);
    }

    [HarmonyPatch]
    private class GainMaxHpPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "GainMaxHp");
        static void Postfix(object[] __args) => Record("gain_max_hp", __args);
    }

    [HarmonyPatch]
    private class LoseMaxHpPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "LoseMaxHp");
        static void Postfix(object[] __args) => Record("lose_max_hp", __args);
    }

    [HarmonyPatch]
    private class KillPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "Kill");
        static void Postfix(object[] __args) => Record("kill", __args);
    }

    [HarmonyPatch]
    private class PowerApplyPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(PowerCmd), "Apply");
        static void Postfix(object[] __args) => Record("apply_power", __args);
    }

    [HarmonyPatch]
    private class PowerModifyPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(PowerCmd), "ModifyAmount");
        static void Postfix(object[] __args) => Record("power_delta", __args);
    }

    [HarmonyPatch]
    private class GainEnergyPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(PlayerCmd), "GainEnergy");
        static void Postfix(object[] __args) => Record("gain_energy", __args);
    }

    [HarmonyPatch]
    private class GainStarsPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(PlayerCmd), "GainStars");
        static void Postfix(object[] __args) => Record("gain_stars", __args);
    }

    [HarmonyPatch]
    private class DrawPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CardPileCmd), "Draw");
        static void Postfix(object[] __args) => Record("draw", __args);
    }

    [HarmonyPatch]
    private class ChannelPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(OrbCmd), "Channel");
        static void Postfix(object[] __args) => Record("channel", __args);
    }

    [HarmonyPatch]
    private class EvokePatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(OrbCmd), "Evoke");
        static void Postfix(object[] __args) => Record("evoke", __args);
    }

    [HarmonyPatch]
    private class SummonPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(OstyCmd), "Summon");
        static void Postfix(object[] __args) => Record("summon", __args);
    }

    [HarmonyPatch]
    private class ForgePatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(ForgeCmd), "Forge");
        static void Postfix(object[] __args) => Record("forge", __args);
    }

    [HarmonyPatch]
    private class ExhaustPatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CardCmd), "Exhaust");
        static void Postfix(object[] __args) => Record("exhaust_card", __args);
    }

    [HarmonyPatch]
    private class EscapePatch : CmdTracePatchBase
    {
        static IEnumerable<MethodBase> TargetMethods() => Overloads(typeof(CreatureCmd), "Escape");
        static void Postfix(object[] __args) => Record("escape", __args);
    }
}
