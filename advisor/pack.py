"""decision pack ビルダ — STS2_MCP の生 state を LLM 向けの安全な構造に畳む。

LLM に出すのは build_decision_pack() の戻り値だけ。description/title/label/
body/name などの自由文は絶対に pack へコピーしない(BLOCKED_KEYS)。
カード・パワー・敵は id / entity_id で識別する。
"""
from __future__ import annotations

import re

from .sig import merge_sig

# pack に絶対に含めないキー(自由文・表示名)。再帰検査にも使う。
BLOCKED_KEYS = frozenset({
    "description", "title", "label", "body", "name", "keywords",
    "relic_description", "potion_description", "card_description",
    "instructions_title", "instructions_description", "hover",
    "raw_description", "text", "flavor",
})

_INT = re.compile(r"\d+")
_MULT = re.compile(r"(\d+)\s*[x×]\s*(\d+)")

_ATTACK_INTENTS = {"Attack", "SingleAttack", "MultiAttack", "AttackDebuff",
                   "AttackBuff", "DeathBlow"}
_BLOCK_INTENTS = {"Defend", "Block"}


def _num(v, default=0):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    return default


def _power_map(status_list) -> dict:
    """status[{id, amount, type}] → {POWER_ID: amount} + 型注記。"""
    out: dict[str, dict] = {}
    for p in status_list or []:
        pid = p.get("id")
        if not pid:
            continue
        out[pid] = {"n": _num(p.get("amount")), "t": p.get("type")}
    return out


def _parse_intent(intent: dict) -> dict:
    """intent{type,label} → {"k": type, "dmg": n, "hits": n} に畳む。"""
    itype = intent.get("type") or "Unknown"
    out: dict = {"k": itype}
    label = intent.get("label") or ""
    m = _MULT.search(label)
    if m:
        out["dmg"], out["hits"] = int(m.group(1)), int(m.group(2))
    else:
        nums = [int(x) for x in _INT.findall(label)]
        if itype in _ATTACK_INTENTS:
            if nums:
                out["dmg"], out["hits"] = nums[0], 1
        elif nums:
            out["n"] = nums[0]
    return out


def _enemy_pack(e: dict) -> dict:
    intents = [_parse_intent(i) for i in e.get("intents") or []]
    return {
        "e": e.get("entity_id"),
        "hp": _num(e.get("hp")),
        "max_hp": _num(e.get("max_hp")),
        "blk": _num(e.get("block")),
        "int": intents,
        "pw": _power_map(e.get("status")),
    }


def _hand_card(card: dict, kb=None) -> dict:
    sig = merge_sig(card, kb)
    entry = {
        "i": card.get("index"),
        "id": card.get("id"),
        "t": card.get("type"),
        "c": card.get("cost"),
        "star": card.get("star_cost"),
        "up": card.get("is_upgraded"),
        "play": card.get("can_play", True),
        "tgt": card.get("target_type"),
        "fx": sig["fx"],
        "prov": sig["prov"],
    }
    if sig.get("conf", 1) < 0.8:
        entry["conf"] = sig["conf"]
    if sig.get("kw"):
        entry["kw"] = sig["kw"]
    if sig.get("un"):
        entry["un"] = sig["un"]
    reason = card.get("unplayable_reason")
    if reason and not entry["play"]:
        entry["why"] = reason
    return entry


def _choice_card(card: dict, i: int, kb=None) -> dict:
    """カード選択画面の選択肢を id+sig に畳む(name/description は捨てる)。"""
    sig = merge_sig(card, kb)
    return {
        "i": card.get("index", i),
        "id": card.get("id"),
        "t": card.get("type"),
        "c": card.get("cost"),
        "star": card.get("star_cost"),
        "r": card.get("rarity"),
        "up": card.get("is_upgraded"),
        "fx": sig["fx"],
        "kw": sig.get("kw") or [],
        "un": sig.get("un") or [],
        "conf": sig.get("conf"),
        "prov": sig["prov"],
    }


def _combat_pack(state: dict, kb) -> dict:
    battle = state.get("battle") or {}
    player = state.get("player") or {}
    enemies = [_enemy_pack(e) for e in battle.get("enemies") or []]
    hand = [_hand_card(c, kb) for c in player.get("hand") or []]
    pack = {
        "round": _num(battle.get("round")),
        "phase": "play" if battle.get("is_play_phase") else battle.get("turn"),
        "all_ready": battle.get("all_players_ready"),
        "en": _num(player.get("energy")),
        "en_max": _num(player.get("max_energy")),
        "stars": player.get("stars"),
        "hp": _num(player.get("hp")),
        "blk": _num(player.get("block")),
        "pw": _power_map(player.get("status")),
        "piles": {
            "draw": _num(player.get("draw_pile_count")),
            "disc": _num(player.get("discard_pile_count")),
            "ex": _num(player.get("exhaust_pile_count")),
        },
        "enemies": enemies,
        "hand": hand,
    }
    team = []
    for i, p in enumerate(state.get("players") or []):
        team.append({
            "slot": i, "me": p.get("is_local"), "alive": p.get("is_alive"),
            "hp": _num(p.get("hp")), "blk": _num(p.get("block", 0)),
            "rdy": p.get("is_ready_to_end_turn"),
        })
    pack["team"] = team
    return pack


def _options_pack(state: dict, kb) -> dict:
    """非戦闘画面: 選択肢を {i, フラグ} に畳む。選択肢テキストは捨てる。"""
    t = state.get("state_type")
    out: dict = {"options": []}
    if t == "map":
        mp = state.get("map") or {}
        # 実際のmap state: 移動可能なのは next_options(index, col, row, type, leads_to)
        for n in mp.get("next_options") or mp.get("nodes") or []:
            if "next_options" not in mp and not (n.get("is_clickable") or n.get("clickable")):
                continue
            out["options"].append({
                "act": "choose_map_node",
                "i": n.get("index", len(out["options"])),
                "pos": [n.get("col"), n.get("row")],
                "node": n.get("type") or n.get("room_type"),
            })
        out["votes"] = mp.get("votes")
        out["all_voted"] = mp.get("all_voted")
        cur = mp.get("current_position")
        if cur:
            out["at"] = [cur.get("col"), cur.get("row")]
    elif t == "event":
        ev = state.get("event") or {}
        for o in ev.get("options") or []:
            out["options"].append({
                "act": "choose_event_option",
                "i": o.get("index"),
                "proceed": o.get("is_proceed"),
                "locked": o.get("is_locked"),
                "chosen": o.get("was_chosen"),
            })
        out["shared"] = ev.get("is_shared")
    elif t == "rewards":
        sub = state.get("rewards") or {}
        for r in sub.get("items") or sub.get("rewards") or []:
            out["options"].append({
                "act": "claim_reward",
                "i": r.get("index", len(out["options"])),
                "kind": r.get("type") or r.get("kind"),
            })
    elif t in ("card_reward", "card_select", "bundle_select", "hand_select"):
        key = {"card_reward": "card_reward", "card_select": "card_select",
               "bundle_select": "bundle_select", "hand_select": "hand_select"}[t]
        sub = state.get(key) or {}
        cards = sub.get("cards") or sub.get("options") or sub.get("choices") or []
        for j, c in enumerate(cards):
            if isinstance(c, dict) and ("vars" in c or "id" in c):
                opt = _choice_card(c, j, kb)
                opt["act"] = "select_card"
                out["options"].append(opt)
        for flag in ("can_skip", "can_confirm", "can_cancel", "prompt"):
            if flag in sub:
                out[flag] = sub[flag]
    elif t == "relic_select":
        sub = state.get("relic_select") or {}
        for j, r in enumerate(sub.get("relics") or []):
            out["options"].append({"act": "select_relic", "i": j,
                                   "id": r.get("id") or r.get("relic_id")})
        if sub.get("can_skip"):
            out["can_skip"] = True
    elif t == "rest_site":
        for o in (state.get("rest_site") or {}).get("options") or []:
            out["options"].append({
                "act": "choose_rest_option", "i": o.get("index"),
                "locked": o.get("is_locked") or o.get("locked"),
            })
    elif t == "treasure":
        out["options"].append({"act": "claim_treasure_relic", "i": 0})
    elif t == "shop":
        sub = state.get("shop") or {}
        for it in sub.get("items") or []:
            out["options"].append({
                "act": "shop_purchase", "i": it.get("index"),
                "kind": it.get("item_type"), "price": _num(it.get("price")),
                "can": it.get("can_afford"), "id": it.get("id") or it.get("card_id"),
            })
        out["gold"] = _num((state.get("player") or {}).get("gold"))
    return out


def build_decision_pack(state: dict, kb=None) -> dict:
    """mp_state() の戻り値 → LLM 向け decision pack。

    kb: CardKB(任意)。カード署名の統合に使う。
    """
    if not isinstance(state, dict):
        return {"t": "error", "msg": "state is not a dict"}
    if state.get("status") == "error":
        return {"t": "error", "http": state.get("http")}

    t = state.get("state_type") or "unknown"
    pack: dict = {"t": t}
    if state.get("room_type"):
        pack["room"] = state["room_type"]
    run = state.get("run")
    if run:
        pack["run"] = {"act": run.get("act"), "floor": run.get("floor")}

    if "battle" in state or t in ("monster", "elite", "boss"):
        pack["combat"] = _combat_pack(state, kb)

    pack.update(_options_pack(state, kb))

    team = state.get("players")
    if team and "combat" not in pack:
        pack["team"] = [
            {"slot": i, "me": p.get("is_local"), "hp": _num(p.get("hp")),
             "alive": p.get("is_alive")}
            for i, p in enumerate(team)
        ]
    me = state.get("player")
    if me and "combat" not in pack:
        pack["me"] = {
            "hp": _num(me.get("hp")), "max_hp": _num(me.get("max_hp")),
            "gold": _num(me.get("gold")),
            "deck": _num(me.get("deck_count", len(me.get("deck") or [])) or None),
        }

    from .eval import eval_combat, eval_options
    if "combat" in pack:
        pack["advice"] = eval_combat(pack["combat"])
    else:
        adv = eval_options(pack)
        if adv:
            pack["advice"] = adv
    return pack


def assert_llm_safe(obj, path: str = "$") -> list[str]:
    """pack 内に BLOCKED_KEYS が混入していないか再帰検査。違反パスの一覧を返す。"""
    bad: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in BLOCKED_KEYS:
                bad.append(f"{path}.{k}")
            bad += assert_llm_safe(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += assert_llm_safe(v, f"{path}[{i}]")
    return bad
