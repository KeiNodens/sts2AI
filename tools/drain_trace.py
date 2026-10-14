"""アクショントレース → card_kb.json 統合。

GET /api/v1/trace?drain=1 でブリッジのリングバッファを吸い上げ、
カードごとに Cmd イベントを集計して effect シグネチャを作り、
prov="trace" conf=0.9 で KB に記録する。

    python tools/drain_trace.py [--port 15526] [--kb card_kb.json]

トレース値は実行実績なので正確。帰属だけは近似(非同期 Cmd の
誤帰属がありうる)ため conf は 0.9。同じカードの複数回観測は
中央値でまとめる。
"""
from __future__ import annotations

import argparse
import os
import json
import statistics
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from advisor.kb import CardKB  # noqa: E402

TRACE_CONF = 0.9

# trace op → effect op の対応
_OP_MAP = {
    "damage": "damage",
    "block": "block",
    "lose_block": "lose_block",
    "heal": "heal",
    "gain_max_hp": "gain_max_hp",
    "lose_max_hp": "lose_max_hp",
    # "kill" は除外 — CreatureCmd.Kill はカード効果ではなく
    # エンジンの死亡/勝利判定呼び出しが大半で、効果として書くとノイズになる。
    "apply_power": "apply_power",
    "power_delta": "power_delta",
    "gain_energy": "gain_energy",
    "gain_stars": "gain_stars",
    "draw": "draw",
    "channel": "channel",
    "evoke": "evoke",
    "summon": "summon",
    "forge": "forge",
    "exhaust_card": "exhaust_card",
    "escape": "escape",
}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def aggregate(events: list[dict]) -> dict[str, list[dict]]:
    """card_id → 効果シグネチャの推定リストに集約する。

    同じ (op, tgt, pw) ペアの n は中央値でまとめる。
    """
    per_card: dict[str, dict[tuple, list]] = defaultdict(
        lambda: defaultdict(list))
    for ev in events:
        card = ev.get("card")
        op = ev.get("op")
        if not card or op not in _OP_MAP:
            continue
        key = (op, ev.get("tgt"), ev.get("pw"), ev.get("x"))
        per_card[card][key].append(ev.get("n"))

    out: dict[str, list[dict]] = {}
    for card, groups in per_card.items():
        fx: list[dict] = []
        def _key(item):
            return tuple("" if v is None else str(v) for v in item[0])
        for (op, tgt, pw, extra), vals in sorted(
                groups.items(), key=_key):
            eff: dict = {"op": _OP_MAP[op]}
            if vals:
                nums = [v for v in vals if isinstance(v, (int, float))]
                if nums:
                    eff["n"] = statistics.median(nums)
            if tgt:
                # tgt "enemy:JAW_WORM_0" → 敵側なら enemy
                side = str(tgt).split(":")[0]
                eff["tgt"] = {"enemy": "enemy", "player": "self",
                              "pet": "pet"}.get(side, side)
            if pw:
                eff["power"] = pw
            if extra:
                eff["ref"] = extra
            fx.append(eff)
        out[card] = fx
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=15526)
    ap.add_argument("--kb", default=os.environ.get("STS2_KB_PATH", "card_kb.json"))
    ap.add_argument("--dry", action="store_true",
                    help="KB に書かず集計だけ表示")
    args = ap.parse_args()

    data = _get(f"http://localhost:{args.port}/api/v1/trace?drain=1")
    events = data.get("events") or []
    print(f"trace events: {len(events)}")

    per_card = aggregate(events)
    for cid, fx in sorted(per_card.items()):
        print(f"  {cid}: {json.dumps(fx, ensure_ascii=False)[:200]}")
    if args.dry or not per_card:
        return 0

    kb = CardKB.load(args.kb)
    for cid, fx in per_card.items():
        kb.record(cid, fx, "trace", TRACE_CONF)
    kb.save(args.kb)
    print(f"updated {args.kb} ({len(per_card)} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
