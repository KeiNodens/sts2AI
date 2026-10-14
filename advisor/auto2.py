"""StS2 ラン全自動ドライバ — state をポーリングして画面別に進行する。

StS1 の auto1.py 相当。co-op ラン中は multiplayer エンドポイントに
状態取得・行動を流す(--solo で singleplayer)。戦闘は advisor の
run_turn (pack→eval→plan 実行・hand_select 自動解消付き)。

co-op 特有の「投票制」に対応: map / shared event / end_turn /
treasure bid は「自分が投票済みなら相手を待つ」挙動にして連打しない。

使い方:
    python -m advisor.auto2                 # AI側 (port 15527) を co-op ドライブ
    python -m advisor.auto2 --port 15526    # host 側
    python -m advisor.auto2 --steps 300 --sleep 1.0
    python -m advisor.auto2 --skip-cards    # カード報酬を取らない
    python -m advisor.auto2 --no-observe    # 観測フィードバック無効
    python -m advisor.auto2 --solo          # singleplayer エンドポイント
"""
from __future__ import annotations

import argparse
import os
import json
import time
import urllib.request
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sts2 import Inst  # noqa: E402
from . import drive, observe  # noqa: E402
from .kb import CardKB  # noqa: E402

KB_PATH = Path(os.environ.get("STS2_KB_PATH", "card_kb.json"))

# イベントで「proceed でない選択肢」を選ぶ時の安全キーワード
_SAFE_KEYWORDS = ("hp", "heal", "回復", "進む", "proceed", "leave",
                  "skip", "max hp", "rest")

# マップで優先する部屋タイプ (先頭ほど優先)。Elite は HP 高い時だけ。
_MAP_PREFER = ("unknown", "event", "monster", "treasure",
               "shop", "rest", "elite", "boss")
_ELITE_MIN_HP_PCT = 0.5  # これ以下の HP では Elite を避ける


def _me(state: dict) -> dict:
    for p in state.get("players") or []:
        if p.get("is_local"):
            return p
    return {}


def _voted(votes: list | None) -> bool:
    """votes 配列でローカルプレイヤーが投票済みか。"""
    for v in votes or []:
        if v.get("is_local"):
            return bool(v.get("voted"))
    return False


def _state(inst: Inst, mp: bool) -> dict:
    st = inst.mp_state() if mp else inst.state()
    return st if isinstance(st, dict) else {}


def _act(inst: Inst, action: str, mp: bool, **params) -> dict:
    try:
        return inst.act(action, mp=mp, **params) or {}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _drain_trace(inst: Inst, kb: CardKB) -> int:
    """trace バッファを吸い上げて KB に記録。記録カード数を返す。"""
    try:
        data = inst._req("GET", "/api/v1/trace?drain=1")
    except Exception:
        return 0
    events = data.get("events") or []
    if not events:
        return 0
    try:
        from tools.drain_trace import aggregate, TRACE_CONF
    except Exception:
        return 0
    per_card = aggregate(events)
    for cid, fx in per_card.items():
        kb.record(cid, fx, "trace", TRACE_CONF)
    return len(per_card)


def _map_choice(state: dict, me: dict) -> int:
    """next_options から進む部屋の index を選ぶ。"""
    opts = ((state.get("map") or {}).get("next_options")) or []
    if not opts:
        return 0
    hp_pct = (me.get("hp") or 1) / max(1, me.get("max_hp") or 1)
    prefer = list(_MAP_PREFER)
    if hp_pct < _ELITE_MIN_HP_PCT and "rest" in [str(o.get("type", "")).lower()
                                                 for o in opts]:
        prefer = ["rest"] + [p for p in prefer if p != "rest"]
    for want in prefer:
        for o in opts:
            t = str(o.get("type", "")).lower()
            if want in t:
                if want == "elite" and hp_pct < _ELITE_MIN_HP_PCT:
                    continue
                return o.get("index", 0)
    return opts[0].get("index", 0)


def _event_choice(state: dict) -> int:
    opts = ((state.get("event") or {}).get("options")) or []
    if not opts:
        return 0
    for o in opts:
        if o.get("is_proceed") and not o.get("is_locked"):
            return o.get("index", 0)
    for o in opts:
        if o.get("is_locked"):
            continue
        title = str(o.get("title", "")).lower()
        if any(k in title for k in _SAFE_KEYWORDS):
            return o.get("index", 0)
    for o in opts:
        if not o.get("is_locked"):
            return o.get("index", 0)
    return opts[0].get("index", 0)


def step(inst: Inst, kb: CardKB, obs, mp: bool, sleep: float,
         take_cards: bool, ctx: dict) -> tuple[str, dict]:
    """1 回のポーリング+行動。戻り値は (何をしたか, 生 state)。

    ctx: ステップをまたぐメモ (rewards のカード試行回数など)。"""
    st = _state(inst, mp)
    if not st or st.get("status") == "error":
        return "no-state", st
    t = st.get("state_type") or st.get("menu_screen") or "?"
    me = _me(st)

    # 画面が変わったら選択済みフラグをリセット (同種の画面が再度出るため)
    for flag, screen in (("cs_next_idx", "card_select"),
                         ("bs_done", "bundle_select"),
                         ("rs_done", "relic_select")):
        if t != screen:
            ctx.pop(flag, None)
    if t != "rewards":
        ctx.pop("card_claims", None)

    if t == "game_over" or st.get("game_over"):
        return "GAME_OVER", st

    # ---- 戦闘 ----
    if st.get("battle") or t in ("monster", "elite", "boss"):
        battle = st.get("battle") or {}
        if battle.get("is_play_phase") is False:
            return "combat:enemy-phase-waiting", st
        if me.get("is_alive") is False:
            return "combat:dead-waiting", st
        if me.get("is_ready_to_end_turn"):
            return "combat:voted-waiting", st
        r = drive.run_turn(inst, kb=kb, mp=mp, obs=obs, sleep=sleep)
        n = len((r.get("advice") or {}).get("plan") or [])
        return f"combat:{n}steps", st

    if t == "hand_select":
        _act(inst, "combat_select_card", mp, card_index=0)
        time.sleep(0.3)
        _act(inst, "combat_confirm_selection", mp)
        return "hand_select:resolved", st

    # ---- マップ (投票制) ----
    if t == "map":
        m = st.get("map") or {}
        if _voted(m.get("votes")):
            return "map:voted-waiting", st
        idx = _map_choice(st, me)
        r = _act(inst, "choose_map_node", mp, index=idx)
        return f"map:vote {idx} ({r.get('status')})", st

    # ---- イベント ----
    if t == "event":
        ev = st.get("event") or {}
        if ev.get("in_dialogue"):
            _act(inst, "advance_dialogue", mp)
            return "event:advance", st
        if ev.get("is_shared") and _voted(ev.get("votes")):
            return "event:voted-waiting", st
        idx = _event_choice(st)
        r = _act(inst, "choose_event_option", mp, index=idx)
        return f"event:opt {idx} ({r.get('status')})", st

    if t == "fake_merchant":
        shop = st.get("fake_merchant") or {}
        if (shop.get("shop") or {}).get("can_proceed"):
            _act(inst, "proceed", mp)
            return "fake_merchant:proceed", st
        return "fake_merchant:wait", st

    # ---- 報酬 ----
    if t == "rewards":
        rw = st.get("rewards") or {}
        items = rw.get("items") or []
        # カード以外の報酬を右から優先取得。claim すると index がずれるので
        # 毎回生の items を見る。
        non_card = [it for it in items if str(it.get("type")) != "card"]
        if non_card:
            idx = non_card[-1].get("index", 0)
            r = _act(inst, "claim_reward", mp, index=idx)
            return f"rewards:claim {idx} ({r.get('status')})", st
        if items:
            # 残りはカード報酬のみ。取るなら claim → card_reward 画面、
            # 取らないなら proceed(skip しても残る既知の問題があり、
            # claim→skip ループに入らないよう回数を上限する)。
            if take_cards and ctx.get("card_claims", 0) < 3:
                ctx["card_claims"] = ctx.get("card_claims", 0) + 1
                idx = items[-1].get("index", 0)
                r = _act(inst, "claim_reward", mp, index=idx)
                return f"rewards:claim-card {idx} ({r.get('status')})", st
            if rw.get("can_proceed", True):
                _act(inst, "proceed", mp)
                ctx["card_claims"] = 0
                return "rewards:proceed(skip-cards)", st
        elif rw.get("can_proceed", True):
            _act(inst, "proceed", mp)
            ctx["card_claims"] = 0
            return "rewards:proceed", st
        return "rewards:wait", st

    if t == "card_reward":
        sub = st.get("card_reward") or {}
        if not take_cards and sub.get("can_skip"):
            r = _act(inst, "skip_card_reward", mp)
            return f"card_reward:skip ({r.get('status')})", st
        r = _act(inst, "select_card_reward", mp, card_index=0)
        if r.get("status") != "ok" and sub.get("can_skip"):
            r = _act(inst, "skip_card_reward", mp)
        return f"card_reward:pick ({r.get('status')})", st

    if t == "relic_select":
        # select_relic は1回押せば確定する。画面が残っていた場合だけ
        # skip を試す (連打しない)。
        if not ctx.get("rs_done"):
            r = _act(inst, "select_relic", mp, index=0)
            ctx["rs_done"] = r.get("status") == "ok"
            return f"relic_select:sel ({r.get('status')})", st
        r = _act(inst, "skip_relic_selection", mp)
        return f"relic_select:skip ({r.get('status')})", st

    if t == "card_select":
        sub = st.get("card_select") or {}
        stype = sub.get("screen_type", "")
        cards = sub.get("cards") or []
        # select_card はトグル動作 (押すたびに選択/解除が切替わる)。
        # マルチセレクト ("2枚選べ") があるため、押す index を毎回
        # 1つ進めて同じカードを二度押ししない。confirm が出たら確定。
        if sub.get("can_confirm"):
            r = _act(inst, "confirm_selection", mp)
            return f"card_select:confirm ({r.get('status')})", st
        if (sub.get("can_cancel") or sub.get("can_skip")) and not take_cards:
            r = _act(inst, "cancel_selection", mp)
            return f"card_select:cancel ({r.get('status')})", st
        nxt = ctx.get("cs_next_idx", 0)
        if cards and nxt < len(cards):
            r = _act(inst, "select_card", mp,
                     index=cards[nxt].get("index", nxt))
            ctx["cs_next_idx"] = nxt + 1
            return f"card_select:{stype or 'sel'} {nxt} ({r.get('status')})", st
        if sub.get("can_cancel") or sub.get("can_skip"):
            r = _act(inst, "cancel_selection", mp)
            return f"card_select:cancel ({r.get('status')})", st
        return "card_select:wait", st

    if t == "bundle_select":
        # select_bundle → preview → confirm。confirm が通るまで
        # select は繰り返さない (preview の開き直しを防ぐ)。
        if not ctx.get("bs_done"):
            r = _act(inst, "select_bundle", mp, index=0)
            ctx["bs_done"] = r.get("status") == "ok"
            time.sleep(0.3)
            if ctx["bs_done"]:
                r = _act(inst, "confirm_bundle_selection", mp)
            return f"bundle_select:sel ({r.get('status')})", st
        r = _act(inst, "confirm_bundle_selection", mp)
        if r.get("status") != "ok":
            r = _act(inst, "cancel_bundle_selection", mp)
        return f"bundle_select:confirm ({r.get('status')})", st

    # ---- 部屋 ----
    if t in ("shop",):
        r = _act(inst, "proceed", mp)
        return f"shop:proceed ({r.get('status')})", st

    if t == "rest_site":
        rs = st.get("rest_site") or {}
        opts = rs.get("options") or []
        rest_idx = None
        for o in opts:
            if not o.get("is_enabled", True):
                continue
            label = f"{o.get('id','')} {o.get('name','')}".lower()
            if "rest" in label or "休憩" in label:
                rest_idx = o.get("index")
                break
        if rest_idx is None and opts:
            for o in opts:
                if o.get("is_enabled", True):
                    rest_idx = o.get("index")
                    break
        if rest_idx is not None:
            r = _act(inst, "choose_rest_option", mp, index=rest_idx)
            return f"rest:opt {rest_idx} ({r.get('status')})", st
        if rs.get("can_proceed"):
            _act(inst, "proceed", mp)
            return "rest:proceed", st
        return "rest:wait", st

    if t == "treasure":
        tr = st.get("treasure") or {}
        relics = tr.get("relics") or []
        if relics:
            # 入札も relic fight の解決も同じ claim クリックで進む
            # (同じレリックに集中すると fight になるので、自分の
            # local_player_slot 番の index を選んで分散させる)。
            slot = st.get("local_player_slot") or 0
            idx = min(slot, len(relics) - 1)
            r = _act(inst, "claim_treasure_relic", mp, index=idx)
            return f"treasure:bid {idx} ({r.get('status')})", st
        if tr.get("can_proceed"):
            _act(inst, "proceed", mp)
            return "treasure:proceed", st
        return "treasure:wait", st

    if t == "crystal_sphere":
        r = _act(inst, "crystal_sphere_proceed", mp)
        if r.get("status") != "ok":
            r = _act(inst, "proceed", mp)
        return f"crystal_sphere:proceed ({r.get('status')})", st

    # ---- その他 ----
    if t == "popup" or t == "menu":
        opts = st.get("options") or []
        names = [str(o.get("name", "")).lower() for o in opts]
        for want in ("はい", "yes", "ok", "continue", "confirm"):
            if want in names:
                _act(inst, "menu_select", mp,
                     option=opts[names.index(want)].get("name"))
                return f"{t}:{want}", st
        return f"{t}:wait", st

    if t in ("character_select", "multiplayer_load_lobby",
             "multiplayer", "multiplayer_host", "multiplayer_join"):
        return f"{t}:waiting(setup)", st

    # フォールバック: proceed を試す
    r = _act(inst, "proceed", mp)
    return f"{t}:proceed ({r.get('status')})", st


def run(port: int = 15527, mp: bool = True, steps: int = 300,
        sleep: float = 1.0, verbose: bool = True,
        observe_on: bool = True, take_cards: bool = True,
        kb_path: Path = KB_PATH) -> None:
    inst = Inst(port)
    kb = CardKB.load(kb_path)
    obs = observe.Recorder() if observe_on else None
    ctx: dict = {}
    last_sig, stuck, drained = None, 0, 0
    try:
        for i in range(steps):
            what, st = step(inst, kb, obs, mp, sleep, take_cards, ctx)
            t = st.get("state_type") or "?"
            run_ = st.get("run") or {}
            me = _me(st)
            # 待機系の戻り値は stuck 扱いしない — co-op では相手の投票を
            # 長時間待つのが正常。投票済み/敵フェーズ/セットアップ待ちを含む。
            waiting = ("waiting" in what or what == "no-state"
                       or what.endswith(":wait"))
            sig = (t, run_.get("floor"),
                   json.dumps(st.get("map", {}).get("current_position"),
                              sort_keys=True) if t == "map" else "")
            if verbose:
                hp = me.get("hp")
                print(f"[{i:3d}] f{run_.get('floor')} {t} "
                      f"hp={hp} -> {what}", flush=True)
            if what == "GAME_OVER":
                print("run ended (game over)")
                return
            # 戦闘が進むたびに trace を KB へ反映
            if what.startswith("combat:") and "waiting" not in what:
                drained += _drain_trace(inst, kb)
            if waiting:
                stuck = 0
            elif sig == last_sig:
                stuck += 1
                if stuck >= 20:
                    print("stuck; giving up")
                    return
            else:
                stuck = 0
            last_sig = sig
            time.sleep(sleep)
    finally:
        if obs is not None and obs.samples:
            summary = obs.flush(kb, path=kb_path)
            if verbose:
                print(f"observations flushed: {summary}")
        kb.save(kb_path)
        if verbose and drained:
            print(f"trace recorded: {drained} cards")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=15527)
    ap.add_argument("--solo", action="store_true",
                    help="singleplayer エンドポイントを使う")
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--skip-cards", action="store_true",
                    help="カード報酬を取らない")
    ap.add_argument("--no-observe", action="store_true",
                    help="観測フィードバックを無効化")
    args = ap.parse_args()
    run(port=args.port, mp=not args.solo, steps=args.steps,
        sleep=args.sleep, observe_on=not args.no_observe,
        take_cards=not args.skip_cards)


if __name__ == "__main__":
    main()
