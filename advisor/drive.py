"""decision pack の advice.plan を bridge アクションとして実行するドライバ。

手札 index はプレイごとにずれるため、plan は「カード id の現在の先頭インスタンス」
で再解決する(同名カードは区別不要 — 効果が同じならどれを出しても同じ)。
"""
from __future__ import annotations

import time

from .pack import build_decision_pack


def _find_index(hand: list, card_id: str) -> int | None:
    """残っている手札で id が一致する先頭の index。"""
    for c in hand:
        if c.get("id") == card_id:
            return c.get("index")
    return None


def _resolve_substate(inst, state_fn, mp: bool, sleep: float) -> None:
    """プレイ後に残ったモーダル状態を既定選択で解消する。

    SURVIVOR の「1枚捨てる」等のカードが手札選択 UI (hand_select) を
    開くと、その間に enqueue された PlayCardAction は全て実行されず
    詰まる(API は ok を返すがゲーム側で止まる — 実機確認済)。
    simple_select なら select だけで確定するので、先頭カードを選んで
    confirm を試す(confirm は既に確定済みなら error を返すだけ)。"""
    for _ in range(3):
        st = state_fn() or {}
        sel = st.get("hand_select")
        if st.get("state_type") != "hand_select" and not sel:
            return
        cards = (sel or {}).get("cards") or []
        idx = cards[0].get("index", 0) if cards else 0
        try:
            inst.act("combat_select_card", mp=mp, card_index=idx)
        except Exception:
            pass
        try:
            inst.act("combat_confirm_selection", mp=mp)
        except Exception:
            pass
        time.sleep(sleep)


def play_turn(inst, pack: dict, sleep: float = 1.0, mp: bool = False,
              obs=None) -> list[dict]:
    """combat advice.plan を順に実行する。

    inst: sts2.Inst。mp=True で co-op エンドポイントを使う。
    obs: observe.Recorder — 渡すと各 play_card の前後 state 差分を記録する。
    戻り値: 実行ログ [{"step": ..., "result": ...}]
    """
    state_fn = inst.mp_state if mp else inst.state
    log: list[dict] = []
    for step in (pack.get("advice") or {}).get("plan") or []:
        act = step.get("act")
        if act == "end_turn":
            r = inst.act("end_turn", mp=mp)
            log.append({"step": step, "result": r.get("status")})
            break
        if act != "play_card":
            log.append({"step": step, "result": "skip:unknown act"})
            continue
        cur = state_fn()
        hand = (cur.get("player") or {}).get("hand") or []
        idx = _find_index(hand, step.get("id"))
        if idx is None:
            log.append({"step": step, "result": "card gone"})
            continue
        if obs is not None:
            obs.before(step.get("id"), cur)
        body = {"card_index": idx}
        if step.get("tgt"):
            body["target"] = step["tgt"]
        r = inst.act("play_card", mp=mp, **body)
        log.append({"step": {**step, "resolved_i": idx},
                    "result": r.get("status"), "msg": r.get("message")})
        time.sleep(sleep)
        _resolve_substate(inst, state_fn, mp, sleep)
        if obs is not None:
            obs.after(state_fn())
    return log


def run_turn(inst, kb=None, mp: bool = False, sleep: float = 1.0,
             obs=None) -> dict:
    """状態を取って pack を組み、戦闘なら plan を実行する。戻り値は pack+log。"""
    state = inst.mp_state() if mp else inst.state()
    pack = build_decision_pack(state, kb=kb)
    if "combat" in pack:
        pack["log"] = play_turn(inst, pack, sleep=sleep, mp=mp, obs=obs)
    return pack
