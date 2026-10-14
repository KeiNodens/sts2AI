"""L1 アドバイザ — decision pack 上の純粋計算。

- incoming      このターンに来る合計ダメージ(intent の dmg×hits 合算)
- max_dmg       エネルギー内で出せる理論最大ダメージ(グリーディ近似)
- lethal        そのターンに倒し切れる敵の entity_id
- block_gap     incoming - 現在ブロック(防御の必要性)
- plan          推奨アクション列([{act, i, id, tgt}...]。i は現在の手札index)
- notes         未解釈 var や X コスト等の注記
"""
from __future__ import annotations

from .pack import _ATTACK_INTENTS

_ENERGY_OPS = {"gain_energy"}
_DRAW_OPS = {"draw"}
_DMG_OPS = {"damage"}
_BLK_OPS = {"block"}
_SETUP_OPS = {"apply_power", "summon", "forge", "gain_max_hp", "gain_stars",
              "power_multiply", "multiply_block"}


def _cost(card: dict, energy: int) -> int:
    """カードのエネルギーコスト。X は残エネルギー全投入。"""
    c = card.get("c")
    if c is None:
        return 0
    s = str(c)
    if s.upper() == "X":
        return max(energy, 0)
    try:
        return int(s)
    except ValueError:
        return 99  # 解釈不能コストは実質プレイ不可扱い


def _fx_sum(card: dict, ops: set[str]) -> float:
    """指定 op の合計量。repeat はダメ/ブロック/ドロー系の乗数になる。"""
    repeat = 1
    vals = 0.0
    for fx in card.get("fx") or []:
        if fx.get("op") == "repeat":
            repeat += int(fx.get("n") or 0)
    for fx in card.get("fx") or []:
        if fx.get("op") in ops:
            vals += (fx.get("n") or 0) * repeat
    return vals


def _is_attack(card: dict) -> bool:
    return _fx_sum(card, _DMG_OPS) > 0 or card.get("t") == "Attack"


def _aoe(card: dict) -> bool:
    return (card.get("tgt") or "") in {"AllEnemies", "AllCreatures"}


def _repeat_mult(card: dict) -> int:
    r = 1
    for fx in card.get("fx") or []:
        if fx.get("op") == "repeat":
            r += int(fx.get("n") or 0)
    return r


def _sim_hit(e: dict, dmg: float) -> None:
    absorbed = min(e["blk"], dmg)
    e["blk"] -= absorbed
    e["hp"] -= dmg - absorbed


def _weakest(enems: dict) -> str | None:
    alive = [(k, v) for k, v in enems.items() if v["hp"] > 0]
    if not alive:
        return None
    return min(alive, key=lambda kv: kv[1]["hp"] + kv[1]["blk"])[0]


def _new_sim(energy0: int, enemies: list) -> dict:
    return {
        "en": energy0, "blk": 0.0, "strn": 0.0, "dex": 0.0,
        "pool_dmg": {}, "played": [],
        "enems": {e["e"]: {"hp": e.get("hp") or 0, "blk": e.get("blk") or 0,
                           "vuln": 0, "weak": False} for e in enemies},
    }


def _clone_sim(sim: dict) -> dict:
    return {
        "en": sim["en"], "blk": sim["blk"], "strn": sim["strn"],
        "dex": sim["dex"], "pool_dmg": dict(sim["pool_dmg"]),
        "played": list(sim["played"]),
        "enems": {k: dict(v) for k, v in sim["enems"].items()},
    }


def _sim_apply(sim: dict, c: dict) -> bool:
    """sim にカード c を 1 枚適用する。エネルギー不足なら False。"""
    enems = sim["enems"]
    cost = _cost(c, sim["en"])
    if cost > sim["en"]:
        return False
    sim["en"] -= cost
    sim["played"].append(c)
    # 条件付きボーナスは発動不確実なので 0.6 割引で期待値計上
    bonus_dmg = sum((f.get("n") or 0) * 0.6 for f in c.get("fx") or []
                    if f.get("op") == "conditional_bonus"
                    and f.get("of") == "damage")
    bonus_blk = sum((f.get("n") or 0) * 0.6 for f in c.get("fx") or []
                    if f.get("op") == "conditional_bonus"
                    and f.get("of") == "block")
    for _ in range(_repeat_mult(c)):
        for fx in c.get("fx") or []:
            op, n, tgt = fx.get("op"), fx.get("n") or 0, fx.get("tgt")
            if op == "damage":
                n += bonus_dmg
            elif op == "block":
                n += bonus_blk
            if op == "mod_damage":
                # 独自ゲージ(スタミナ等)へのダメージ — HP リーサルに混ぜない。
                pool = fx.get("pool") or "mod"
                sim["pool_dmg"][pool] = sim["pool_dmg"].get(pool, 0.0) + n
            elif op == "damage":
                # dyn="block"(Body Slam型)はダメ=現ブロックで概算
                dmg = (sim["blk"] if fx.get("dyn") == "block" else n) \
                    + sim["strn"]
                if tgt == "all_enemies":
                    for e in enems.values():
                        _sim_hit(e, dmg * (1.5 if e["vuln"] else 1.0))
                else:
                    w = _weakest(enems)
                    if w is not None:
                        _sim_hit(enems[w],
                                 dmg * (1.5 if enems[w]["vuln"] else 1.0))
            elif op == "block":
                sim["blk"] += n + sim["dex"]
            elif op == "gain_energy":
                sim["en"] += n
            elif op == "apply_power":
                pw = _power_base_name(fx.get("power") or "")
                if tgt in ("enemy", "all_enemies"):
                    tgts = list(enems.values()) if tgt == "all_enemies" \
                        else ([enems[w]] if (w := _weakest(enems)) else [])
                    for e in tgts:
                        if pw == "Vulnerable":
                            e["vuln"] += n
                        elif pw == "Weak":
                            e["weak"] = True
                else:
                    if pw == "Strength":
                        sim["strn"] += n
                    elif pw == "Dexterity":
                        sim["dex"] += n
            elif op == "power_multiply":
                pw = _power_base_name(fx.get("power") or "")
                if pw == "Strength":
                    sim["strn"] *= max(1, n)
                elif pw == "Dexterity":
                    sim["dex"] *= max(1, n)
            elif op == "multiply_block":
                sim["blk"] *= max(1, n)
    return True


def _simulate(order: list, energy0: int, enemies: list) -> dict:
    """順序付き手札をざっくり実行し、結果状態を返す。"""
    sim = _new_sim(energy0, enemies)
    for c in order:
        _sim_apply(sim, c)
    sim["en_left"] = sim["en"]
    return sim


def _dealt(sim: dict, enemies: list) -> float:
    hp0 = {e["e"]: (e.get("hp") or 0) for e in enemies}
    return sum(max(0.0, hp0[k] - v["hp"]) for k, v in sim["enems"].items())


def _partial_score(sim: dict, enemies: list) -> float:
    """ビーム探索の途中ノード評価 — incoming ペナルティは終端でのみ掛ける。
    未使用エネルギーは選択肢の価値として加点する。"""
    lethal = sum(1 for v in sim["enems"].values() if v["hp"] <= 0)
    return (_dealt(sim, enemies) + lethal * 25.0
            + sum(sim.get("pool_dmg", {}).values()) * 0.5
            + sim["blk"] * 0.8 + sim["en"] * 1.5)


def _node_key(sim: dict, used: frozenset) -> tuple:
    return (tuple(sorted(used)), round(sim["en"], 1), round(sim["blk"], 1),
            round(sim["strn"], 2), round(sim["dex"], 2),
            tuple(sorted((k, round(v["hp"], 1), round(v["blk"], 1))
                         for k, v in sim["enems"].items())))


def _rollout(hand: list, energy0: int, enemies: list,
             incoming: float, block0: float, width: int = 12) -> tuple:
    """L3 ビーム探索 — プレイ順+部分集合を全探索し、_sim_score 最大の終端を返す。
    途中で打ち切る(プレイしない)選択も終端として常に含める。"""
    root = _new_sim(energy0, enemies)
    root["en_left"] = energy0
    best = root
    best_score = _sim_score(root, enemies, incoming, block0)
    frontier = [(root, frozenset())]
    while frontier:
        cands = []
        for sim, used in frontier:
            for i, c in enumerate(hand):
                if i in used:
                    continue
                if _cost(c, sim["en"]) > sim["en"]:
                    continue
                s2 = _clone_sim(sim)
                _sim_apply(s2, c)
                cands.append((_partial_score(s2, enemies), s2, used | {i}))
        if not cands:
            break
        seen: dict[tuple, tuple] = {}
        for sc, s2, u in cands:
            k = _node_key(s2, u)
            if k not in seen or seen[k][0] < sc:
                seen[k] = (sc, s2, u)
        top = sorted(seen.values(), key=lambda x: -x[0])[:width]
        frontier = []
        for sc, s2, u in top:
            s2["en_left"] = s2["en"]
            fin = _sim_score(s2, enemies, incoming, block0)
            if fin > best_score:
                best, best_score = s2, fin
            frontier.append((s2, u))
    return best, best_score


def _sim_score(sim: dict, enemies: list, incoming: float, block0: float) -> float:
    """シミュレーション結果のスコア。リーサル最重視、被弾は重めのペナルティ。"""
    hp0 = {e["e"]: (e.get("hp") or 0) for e in enemies}
    dealt = sum(max(0.0, hp0[k] - v["hp"]) for k, v in sim["enems"].items())
    lethal = sum(1 for v in sim["enems"].values() if v["hp"] <= 0)
    # 独自ゲージダメージは別勝利筋の可能性 — 確実でないので半分で加点
    dealt += sum(sim.get("pool_dmg", {}).values()) * 0.5
    weak_all = any(v["weak"] for v in sim["enems"].values())
    incoming_adj = incoming * (0.75 if weak_all else 1.0)
    blk_total = block0 + sim["blk"]
    unblocked = max(0.0, incoming_adj - blk_total)
    blocked_value = min(blk_total, incoming_adj) * 0.8
    return dealt + lethal * 25.0 + blocked_value - unblocked * 1.3


def _role(c: dict) -> str:
    ops = {f.get("op") for f in c.get("fx") or []}
    if ops & _ENERGY_OPS:
        return "energy"
    if ops & (_DMG_OPS | {"mod_damage"}):
        return "attack"
    if ops & _BLK_OPS:
        return "defense"
    if ops & _SETUP_OPS or ops & _DRAW_OPS:
        return "setup"
    return "misc"


_ROLE_ORDER = {"energy": 0, "setup": 1, "attack": 2, "defense": 3, "misc": 4}

# 試す順序候補。それぞれ違う局面を得意とする(先バフ/殴り優先/防御優先/軽い順)
_ORDERINGS = [
    ("setup_first",  lambda c: (_ROLE_ORDER.get(_role(c), 9), -_fx_sum(c, _DMG_OPS))),
    ("aggro",        lambda c: (0 if _role(c) == "energy" else
                                1 if _role(c) == "attack" else 2,
                                -_fx_sum(c, _DMG_OPS))),
    ("turtle",       lambda c: (0 if _role(c) == "energy" else
                                1 if _role(c) == "defense" else
                                2 if _role(c) == "attack" else 3,
                                -_fx_sum(c, _BLK_OPS))),
    ("cheap_first",  lambda c: (_cost(c, 99), -_fx_sum(c, _DMG_OPS))),
]


def eval_combat(pack_combat: dict) -> dict:
    energy = pack_combat.get("en") or 0
    hand = [c for c in pack_combat.get("hand") or [] if c.get("play")]
    enemies = [e for e in pack_combat.get("enemies") or []]
    block0 = pack_combat.get("blk") or 0

    incoming = 0.0
    for e in enemies:
        for it in e.get("int") or []:
            if it.get("k") in _ATTACK_INTENTS:
                incoming += (it.get("dmg") or 0) * (it.get("hits") or 1)

    block_gap = max(0.0, incoming - block0)

    # L2: 順序候補を全てシミュレートして最善を採用
    best = None
    best_name = None
    for name, keyfn in _ORDERINGS:
        sim = _simulate(sorted(hand, key=keyfn), energy, enemies)
        score = _sim_score(sim, enemies, incoming, block0)
        if best is None or score > best[1]:
            best, best_name = (sim, score), name

    # L3: ビーム探索で順列+部分集合を網羅(固定順序では拾えない最適を探す)
    roll_sim, roll_score = _rollout(hand, energy, enemies, incoming, block0)
    if best is None or roll_score > best[1]:
        best, best_name = (roll_sim, roll_score), "rollout"

    sim = best[0]
    enems = sim["enems"]
    lethal = [k for k, v in enems.items() if v["hp"] <= 0]
    hp0 = {e["e"]: e.get("hp") or 0 for e in enemies}
    max_dmg = sum(max(0.0, hp0[k] - v["hp"]) for k, v in enems.items())

    plan = []
    # 単体攻撃は「現在最弱の敵」に集中(ドライバは実行時に同じ id で解決)
    focus = min(enemies, key=lambda e: (e.get("hp") or 0) + (e.get("blk") or 0),
                default={})
    for c in sim["played"]:
        tgt = None
        if str(c.get("tgt") or "") in ("AnyEnemy", "AnyCreature", "RandomEnemy",
                                       "enemy", "random_enemy"):
            tgt = focus.get("e")
        plan.append({"act": "play_card", "i": c.get("i"),
                     "id": c.get("id"), "tgt": tgt})
    plan.append({"act": "end_turn"})

    notes: list[str] = []
    for c in pack_combat.get("hand") or []:
        if not c.get("play"):
            notes.append(f"unplayable i={c.get('i')} {c.get('why') or ''}".strip())
        if c.get("un"):
            notes.append(f"unparsed vars i={c.get('i')}: {','.join(c['un'])}")
        if str(c.get("c") or "").upper() == "X":
            notes.append(f"X-cost i={c.get('i')} resolves at X={energy}")

    return {
        "incoming": incoming,
        "block_gap": block_gap,
        "max_dmg": max_dmg,
        "lethal": lethal,
        "plan": plan,
        "ordering": best_name,
        "en_left": sim["en_left"],
        "notes": notes,
    }


# --- カード報酬/選択の L1 スコアリング -------------------------------------

# op ごとの 1 点あたり価値(コストペナルティと別)
_OP_VALUE = {
    "damage": 1.0, "block": 0.85, "draw": 2.5, "gain_energy": 3.0,
    "heal": 0.8, "lose_hp": -1.0, "gain_max_hp": 1.5, "gain_gold": 0.1,
    "gain_stars": 1.0, "summon": 3.0, "repeat": 2.0, "forge": 1.0,
    # mod_var 推定系: gain_resource(独自リソース獲得)は中程度、
    # lose_resource は対象が敵なら弱化として加点、自傷なら減点、
    # condition は閾値メタデータで効果ではないので 0
    "gain_resource": 1.5, "condition": 0.0, "mod_var": 0.4,
    # mod 独自ゲージへのダメージ(スタミナ等)。別勝利筋だが確実でないので
    # 通常 damage の 0.7 倍程度。HP リーサルには計上しない。
    "mod_damage": 0.7,
    # var_rules/trace 由来の拡張 op
    "spend_resource": -1.0,   # リソース消費はコスト
    "add_card": -0.6,         # デッキ/山札にカード追加(希釈として微減)
    "discard": -0.3,          # 捨てる効果(文脈次第でプラスにもなるが保守的に)
    "channel": 1.5, "evoke": 0.5,   # オーブ系
    "power_delta": 1.0,       # パワー増減(不特定)
    "exhaust_card": -0.2,
    "conditional_bonus": None,  # of に応じて別計算
}
# apply_power はパワーIDごとに価値が違う
_POWER_VALUE = {
    "Strength": 3.0, "Dexterity": 3.0, "Vulnerable": 2.5, "Weak": 2.5,
    "Poison": 2.0,
}
# 敵に付くと「敵が強くなる」パワー(=付与量が負なら我々に有利なデバフ)
_ENEMY_BUFF_POWERS = {"Strength", "Dexterity", "Ritual", "Plating", "Regen"}


def _power_base_name(power_id: str) -> str:
    """STRENGTH_POWER → Strength のように正規化して _POWER_VALUE を引けるようにする。"""
    p = power_id
    for suf in ("_POWER", "Power"):
        if p.endswith(suf):
            p = p[: -len(suf)]
    # STRENGTH → Strength(大文字IDをタイトルケースに)
    return p.title() if p.isupper() else p
_KW_VALUE = {
    "Innate": 1.0, "Retain": 0.5, "Sly": 1.0,
    "Exhaust": -0.5, "Ethereal": -0.5,
}
_RARITY_BONUS = {"Rare": 1.0, "Uncommon": 0.3}
_SKIP_THRESHOLD = 4.0   # このスコア未満しか無ければ skip 推奨(デッキ希釈)


def score_card(o: dict) -> float:
    """選択肢カードの L1 スコア。大きいほど取る価値あり。

    シグネチャの各効果に重みを掛けて足し、コスト・kw・レアリティで補正する。
    カード同士のシナジーは見ない(L2 以降の仕事)。
    """
    if o.get("t") in ("Status", "Curse"):
        return -5.0
    if "Unplayable" in (o.get("kw") or []):
        return -50.0
    score = 0.0
    for fx in o.get("fx") or []:
        op = fx.get("op")
        n = fx.get("n") or 0
        if op == "apply_power":
            pw = _power_base_name(fx.get("power") or "")
            val = _POWER_VALUE.get(pw, 1.5)
            # 敵へのバフ系パワーは負の付与(敵弱化)こそ有利
            if fx.get("tgt") == "enemy" and (
                    pw in _ENEMY_BUFF_POWERS or fx.get("debuff") is False):
                score -= n * val
            elif fx.get("debuff") is True and fx.get("tgt") != "enemy":
                score -= n * val * 0.5  # 自デバフは弱い減点
            else:
                score += n * val
        elif op == "lose_resource" or op == "lose_block":
            v = n * (1.2 if fx.get("tgt") == "enemy" else -0.8)
            score += v
        elif op == "conditional_bonus":
            # 条件付き加算 — of 元の効果価値 × 0.6(条件が常時成立しない前提)
            base = _OP_VALUE.get(fx.get("of") or "", 0.4)
            score += base * n * 0.6
        elif op == "spend_resource":
            score += n * _OP_VALUE["spend_resource"]
        elif op == "power_multiply":
            # パワー倍加(例: Limit Break)。平均所持を仮定して追加スタック分を近似
            pw = _power_base_name(fx.get("power") or "")
            val = _POWER_VALUE.get(pw, 1.5)
            score += max(0, n - 1) * val * 2.0
        elif op == "multiply_block":
            score += max(0, n - 1) * 6.0  # 平均ブロック~6を倍加として近似
        elif op == "damage" and fx.get("dyn"):
            # Body Slam型(ダメ=現ブロック等): 静的 n が無いので平均値で近似
            score += 8.0 if fx["dyn"] == "block" else 5.0
        else:
            v = (_OP_VALUE.get(op) or 0.0) * (n or 0)
            if fx.get("tgt") == "all_enemies":
                v *= 1.4
            if fx.get("guess") and not fx.get("rule"):
                v *= 0.85  # 名前推定は不確実性割引(ルール由来は推定より確実)
            if fx.get("cond"):
                v *= 0.7    # 条件付き効果は割引
            score += v
    # コスト負担(X は 1.5 換算)
    c = str(o.get("c") or "0")
    cost = 1.5 if c.upper() == "X" else (int(c) if c.isdigit() else 0)
    score -= cost * 1.5
    if o.get("star"):
        score -= 1.5
    score += sum(_KW_VALUE.get(k, 0.0) for k in (o.get("kw") or []))
    score += _RARITY_BONUS.get(o.get("r") or "", 0.0)
    # 未解釈 var が多いほど読めないカードとして減点
    score -= len(o.get("un") or []) * 0.4
    return round(score, 2)


def eval_options(pack: dict) -> dict:
    """非戦闘画面の L1 助言。確実なもの(報酬回収など)だけ提案する。"""
    opts = pack.get("options") or []
    advice: dict = {"options": len(opts)}
    t = pack.get("t")
    if t == "rewards":
        # 報酬は全回収(高indexから — 選択でindexがずれるため)
        advice["plan"] = [
            {"act": "claim_reward", "i": o["i"]}
            for o in sorted(opts, key=lambda o: o.get("i") or 0, reverse=True)
        ]
    elif t == "map":
        hp = (pack.get("me") or {}).get("hp")
        pref = None
        for o in opts:
            node = str(o.get("node") or "").lower()
            if "rest" in node and hp is not None:
                pref = o
        advice["pick"] = (pref or opts[0]).get("i") if opts else None
    elif t == "event":
        proceed = [o for o in opts if o.get("proceed")]
        advice["pick"] = proceed[0]["i"] if proceed else (opts[0]["i"] if opts else None)
        advice["note"] = "event option text not surfaced (privacy); LLM decides" \
            if not proceed else None
    elif t in ("card_reward", "card_select", "bundle_select", "hand_select"):
        scored = [(o, score_card(o)) for o in opts if "fx" in o]
        advice["scores"] = [{"i": o.get("i"), "id": o.get("id"), "s": s}
                            for o, s in scored]
        best = max(scored, key=lambda x: x[1], default=None)
        if best and best[1] >= _SKIP_THRESHOLD:
            advice["pick"] = best[0].get("i")
        else:
            advice["pick"] = None
            advice["skip"] = True
            advice["note"] = (f"best score {best[1] if best else 'n/a'} "
                              f"< {_SKIP_THRESHOLD}: lean deck is better")
    return advice
