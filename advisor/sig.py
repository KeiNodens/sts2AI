"""カードの vars(DynamicVarSet シリアライズ) → 効果シグネチャへの変換。

ブリッジは各カードについて DynamicVar を {b,p,c,t} で返す:
  b = BaseValue, p = PreviewValue, c = 戦闘中の計算値(CalculatedVar.Calculate)
  t = var の実装型名(DynamicVar 以外のときのみ)

シグネチャは {"op": ..., "n": 数値, "tgt": ...} の列 + kw フラグ。
provenance タグ:
  vars    = 実行時 DynamicVars から導出(数値は確実、条件分岐までは不明)
  static  = card_kb.json の静的解析由来
  trace   = アクショントレース由来
  observed= 状態差分の観測由来
"""

# DynamicVarSet の既知キー → (op, 適用先ヒント)
# 適用先はカードの target_type で最終決定する(power付与は対象側へ、など)。
VAR_OPS = {
    "Damage":           ("damage", "target"),
    "CalculatedDamage": ("damage", "target"),
    "ExtraDamage":      ("damage", "target"),
    "OstyDamage":       ("damage", "target"),
    "Block":            ("block", "self"),
    "CalculatedBlock":  ("block", "self"),
    "Cards":            ("draw", "self"),
    "Energy":           ("gain_energy", "self"),
    "Stars":            ("gain_stars", "self"),
    "Heal":             ("heal", "self"),
    "HpLoss":           ("lose_hp", "self"),
    "MaxHp":            ("gain_max_hp", "self"),
    "Gold":             ("gain_gold", "self"),
    "Repeat":           ("repeat", None),
    "Summon":           ("summon", "self"),
    "Forge":            ("forge", "self"),
    "Strength":         ("apply_power", "self"),
    "Dexterity":        ("apply_power", "self"),
}

# 数値の集計にしか使わない記帳用 var(効果ではない)
_IGNORE_VARS = {"CalculationBase", "CalculationExtra"}

# 対象が敵側になる target_type(apply_power の向き判定に使う)
_ENEMY_TARGETS = {"AnyEnemy", "AnyCreature", "RandomEnemy"}
_ALL_TARGETS = {"AllEnemies", "AllCreatures"}
_ALLY_TARGETS = {"AnyAlly", "AllAllies", "AnyPlayer", "Osty"}

# mod 独自 var の名前トークン → 推定 op。「mod 独自機構の大半は
# FooDamage/BarLoss/SpiritThreshold のような英語名 var」という実測に基づく
# ヒューリスティック。確定的でない分 conf は下げるが、値が見えるだけで
# まったく読めないよりずっとマシ。
# 閾値・発動条件などの記帳用 var 名(値が大きくても効果ではない)。
# 部分一致だと "SpiritThreshold" が "spirit" に先に当たってしまうので、
# 名前テーブルより先に接尾辞チェックする。
_CONDITION_SUFFIXES = (
    "threshold", "requirement", "count", "counter", "cooldown",
    "perturn", "percombat", "perplay", "cap", "maximum", "minimum",
    "cost", "price", "unlock",
)

# mod 独自の「別リソースへのダメージ」プール。HP ダメージではないので
# リーサル計算に混ぜると危険(スタミナ0即死は別トラック)。
# 名前が damage 系 + これらの語を含む → op=mod_damage + pool。
_MOD_DAMAGE_POOLS = (
    "stamina", "spirit", "soul", "essence", "sanity", "poise",
    "posture", "pleasure", "lust", "will", "morale", "blood",
)

_VAR_NAME_HINTS = [
    # (部分一致, op, 既定tgt)。先勝ち。
    (("damage", "dmg"), "damage", "enemy"),
    (("block", "shield", "guard", "armor", "barrier"), "block", "self"),
    (("draw",), "draw", "self"),
    (("energy", "mana"), "gain_energy", "self"),
    (("hploss", "lifeloss", "selfdamage", "bloodloss"), "lose_hp", "self"),
    (("strength", "dexterity", "ritual", "plating"), "apply_power", "self"),
    (("vulnerable", "weak", "poison", "doom", "mark"), "apply_power", "enemy"),
    (("summon", "spawn"), "summon", "self"),
    (("upgrade", "forge", "enchant"), "forge", "self"),
    (("heal", "regen"), "heal", "self"),
    (("gold",), "gain_gold", "self"),
    (("loss", "lose", "drain"), "lose_resource", "enemy_if_targeted"),
    (("stamina", "spirit", "charge", "soul", "essence", "orb"), "gain_resource", "self"),
]


def _rule_fx(name: str, rule: dict, card: dict) -> dict:
    """var_rules.json のルール → effect シグネチャ。"""
    fx: dict = {"op": rule.get("op", "mod_var"), "guess": True, "rule": True}
    n = None
    tgt = rule.get("tgt")
    if tgt is None:
        op = fx["op"]
        if op in ("damage", "mod_damage", "lose_resource"):
            tgt = _effect_target(card)
        elif op == "apply_power":
            tgt = _effect_target(card)
        elif op not in ("condition",):
            tgt = "self"
    if tgt is not None:
        fx["tgt"] = tgt
    for k in ("pool", "cond", "ref", "note", "of"):
        if rule.get(k) is not None:
            fx[k] = rule[k]
    if rule.get("power"):
        fx["power"] = rule["power"]
    if rule.get("sign"):
        fx["sign"] = rule["sign"]
    return fx


def _guess_mod_var(name: str, card: dict) -> dict:
    """未知 var 名から効果を推定する。一致しなければ op=mod_var。

    優先順位: var_rules(静的解析キュレーション) → 接尾辞 condition →
    damage 系(リソース語を含むなら mod_damage) → 名前ヒント表。
    """
    low = name.lower()
    # 閾値/記帳用 var は効果ではない — 値をそのまま加点しないよう先に判定
    if any(low.endswith(s) or low.rstrip("0123456789_").endswith(s)
           for s in _CONDITION_SUFFIXES):
        return {"op": "condition", "tgt": None, "guess": True}
    # 「ダメージ」だがリソース名を含む → HP ではなく独自ゲージへのダメージ
    if any(t in low for t in ("damage", "dmg")):
        for pool in _MOD_DAMAGE_POOLS:
            if pool in low:
                return {"op": "mod_damage", "pool": pool,
                        "tgt": _effect_target(card), "guess": True}
        return {"op": "damage", "tgt": _effect_target(card), "guess": True}
    for toks, op, tgt in _VAR_NAME_HINTS[1:]:  # damage は上で処理済み
        if any(tok in low for tok in toks):
            if tgt == "enemy_if_targeted":
                tgt = "enemy" if (card.get("target_type") or "") in (
                    _ENEMY_TARGETS | _ALL_TARGETS) else "self"
            elif tgt == "enemy":
                tgt = _effect_target(card) if _effect_target(card) != "self" else "enemy"
            return {"op": op, "tgt": tgt, "guess": True}
    return {"op": "mod_var", "guess": True}


def _num(v: object) -> float | int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    return None


def _var_value(vd: dict) -> float | int | None:
    """var エントリから代表値を選ぶ。c > p(非0) > b の優先順位。"""
    c = _num(vd.get("c"))
    if c is not None:
        return c
    p = _num(vd.get("p"))
    if p not in (None, 0):
        return p
    return _num(vd.get("b"))


def _effect_target(card: dict) -> str:
    """カードの target_type → 効果の対象(enemy/all_enemies/ally/self)。"""
    tt = card.get("target_type") or ""
    if tt in _ALL_TARGETS:
        return "all_enemies"
    if tt in _ENEMY_TARGETS:
        return "enemy"
    if tt in _ALLY_TARGETS:
        return "ally"
    return "self"


def derive_sig(card: dict, rule_for=None) -> dict:
    """state 内のカード dict から効果シグネチャを導出する。

    rule_for: var 名 → var_rules エントリ(または None)を返す callable。
    CardKB.lookup_var_rule に渡せる。

    戻り値:
      {"fx": [op...], "kw": [keyword...], "un": [未解釈 var 名...],
       "prov": "vars", "conf": 0.8}
    """
    effects: list[dict] = []
    unknown: list[str] = []
    target_type = card.get("target_type") or ""

    for name, vd in (card.get("vars") or {}).items():
        if not isinstance(vd, dict):
            continue
        n = _var_value(vd)
        vtype = vd.get("t") or "DynamicVar"

        if name in _IGNORE_VARS:
            continue
        if vtype.startswith("PowerVar"):
            # PowerVar<Poison> 等: キー名がパワーID。付与先はカードの対象側。
            effects.append({
                "op": "apply_power", "power": name, "n": n,
                "tgt": _effect_target(card),
            })
            continue
        op = VAR_OPS.get(name)
        if op is None:
            # mod 独自 var: まずキュレーション済みルール、なければ名前推定。
            # どちらでも値だけは見える形で残す。
            rule = rule_for(name) if rule_for else None
            guess = _rule_fx(name, rule, card) if rule else _guess_mod_var(name, card)
            guess["var"] = name
            if rule and rule.get("sign"):
                guess["n"] = (n or 0) * rule["sign"]
            else:
                guess["n"] = n
            effects.append(guess)
            unknown.append(name)
            continue
        op_name, tgt_hint = op
        fx = {"op": op_name, "n": n}
        if tgt_hint == "target":
            fx["tgt"] = _effect_target(card)
        elif tgt_hint:
            fx["tgt"] = tgt_hint
        effects.append(fx)

    kw = list(card.get("kw") or [])
    # 未解釈 var: ルール解決は軽い減点、名前推定は中程度、まったく不明は減点
    conf = 0.8
    if unknown:
        hard = sum(1 for f in effects
                   if f.get("op") == "mod_var" and f.get("var") in unknown)
        ruled = sum(1 for f in effects
                    if f.get("rule") and f.get("var") in unknown)
        soft = len(unknown) - hard - ruled
        conf -= hard * 0.12 + soft * 0.05 + ruled * 0.02
    if not effects and (card.get("type") not in (None, "Status", "Curse")):
        conf -= 0.2
    return {"fx": effects, "kw": kw, "un": unknown,
            "prov": "vars", "conf": round(max(conf, 0.1), 2)}


def merge_sig(card: dict, kb=None) -> dict:
    """vars 導出と KB 由来を確度で統合する。

    kb: CardKB(カードエントリ + var_rules ルックアップを提供)。
    KB エントリが存在して確度が同等以上なら KB の効果を採用し、vars 由来は
    数値の裏付けとして残す。KB が無ければ vars 導出(ルール付き)を使う。
    """
    rule_for = None
    kb_entry = None
    if kb is not None:
        kb_entry = kb.lookup(card.get("id"))
        rule_for = lambda name: kb.lookup_var_rule(card, name)
    derived = derive_sig(card, rule_for)
    if kb_entry is None:
        return derived
    kb_conf = kb_entry.get("conf", 0.0)
    if kb_conf >= derived["conf"]:
        return {
            "fx": kb_entry.get("effects", []),
            "kw": derived["kw"],
            "un": derived["un"] + kb_entry.get("unknown", []),
            "prov": kb_entry.get("prov", "static"),
            "conf": kb_conf,
            "vars_fx": derived["fx"],
        }
    derived["kb_hint"] = kb_entry.get("effects")
    return derived
