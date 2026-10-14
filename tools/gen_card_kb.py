"""card_kb.json 静的ジェネレータ — /api/v1/catalog から全カードの署名を生成。

使い方:
    python tools/gen_card_kb.py [--port 15526] [--out card_kb.json]

前提: StS2 が起動していて STS2_MCP が応答すること(catalog はメニュー画面でも取れる)。
生成物はローカル参照のみ — description は KB の条件推定に使うが LLM には出ない。

provenance "catalog": DynamicVarSet の値は正しいが、条件付き効果
(「X未満のとき」「このターン」等)や非 var 効果(変身、廃棄、手札操作)は
拾えない。条件系っぽい var 名を持つカードには conditional フラグを立てる。
"""
from __future__ import annotations

import argparse
import os
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from advisor.sig import derive_sig  # noqa: E402

# 条件付き効果を匂わせる var 名 / 説明文パターン(条件は vars からは読めない)
_CONDITIONAL_VAR = re.compile(
    r"threshold|requirement|perturn|per_attack|when|if_|condition|unlock|upto", re.I)
_CONDITIONAL_TEXT = re.compile(
    r"とき|場合|未満|以上|以下|たび|毎に|ごとに|if |when |per |each |"
    r"whenever|until |once per|first time", re.I)


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _mods_fingerprint(mods_dir: str | None = None) -> str:
    """mods/ フォルダ全体の (name, size, mtime) ハッシュ。
    どれか1本でも更新があれば変わる → KB 再生成のトリガーに使う。
    mod 単位の無効化は指紋を mod 側でも分けられるよう
    将来 catalog に mod id が来たら差し替える。"""
    if not mods_dir:
        return "untracked"
    mods = Path(mods_dir)
    if not mods.is_dir():
        raise ValueError("The selected mods directory does not exist")
    h = hashlib.sha256()
    for p in sorted(mods.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def _card_entry(card: dict) -> dict:
    """catalog のカード → KB エントリ。vars→sig + 条件フラグ。"""
    sig = derive_sig({
        "vars": card.get("vars"),
        "kw": card.get("kw"),
        "type": card.get("type"),
        "target_type": card.get("target_type") or "",
    })
    entry: dict = {
        "effects": sig["fx"],
        "prov": "catalog",
        "conf": sig["conf"],
        "type": card.get("type"),
        "cost": card.get("cost"),
    }
    if sig["un"]:
        entry["unknown"] = sig["un"]
    if card.get("pool"):
        entry["pool"] = card["pool"]
    # 条件っぽさ: var 名か説明文に条件表現があれば flag
    cond = any(_CONDITIONAL_VAR.search(u) for u in sig["un"])
    desc = card.get("description") or ""
    if not cond and _CONDITIONAL_TEXT.search(desc):
        cond = True
    if cond:
        entry["conditional"] = True
        entry["conf"] = min(entry["conf"], 0.6)
    return entry


def _power_entry(power: dict) -> dict:
    return {
        "type": power.get("type"),
        "stack": power.get("stack"),
        "vars": power.get("vars"),
        "prov": "catalog",
        "conf": 0.6,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=15526)
    ap.add_argument("--out", default=os.environ.get("STS2_KB_PATH", "card_kb.json"))
    ap.add_argument("--merge", action="store_true",
                    help="既存 KB と merge(高 conf の方を残す)")
    ap.add_argument("--mods-dir", default=None, help="Optional local mods directory for change tracking")
    args = ap.parse_args()

    base = f"http://localhost:{args.port}"
    cards = _get(f"{base}/api/v1/catalog?kind=cards").get("cards", [])
    powers = _get(f"{base}/api/v1/catalog?kind=powers").get("powers", [])
    print(f"catalog: {len(cards)} cards, {len(powers)} powers")

    kb_cards: dict[str, dict] = {}
    cond = 0
    for c in cards:
        cid = c.get("id")
        if not cid:
            continue
        e = _card_entry(c)
        cond += bool(e.get("conditional"))
        kb_cards[cid] = e
    kb_powers = {p["id"]: _power_entry(p) for p in powers if p.get("id")}

    out = Path(args.out)
    if args.merge and out.exists():
        old = json.loads(out.read_text(encoding="utf-8"))
        for cid, e in (old.get("cards") or {}).items():
            cur = kb_cards.get(cid)
            if cur is None or e.get("conf", 0) > cur.get("conf", 0):
                kb_cards[cid] = e
        kb_powers.update({k: v for k, v in (old.get("powers") or {}).items()
                          if k not in kb_powers})

    fp = _mods_fingerprint(args.mods_dir)
    payload = {"version": 1, "cards": kb_cards, "powers": kb_powers,
                   "mod_versions": {"_catalog": fp}}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"wrote {out} — {len(kb_cards)} cards ({cond} conditional-flagged), "
          f"{len(kb_powers)} powers, fingerprint={fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
