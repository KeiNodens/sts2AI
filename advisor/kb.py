"""card_kb.json — カード/パワー効果シグネチャの知識ベース。

スキーマ(version 1):
{
  "version": 1,
  "cards": {
    "<CARD_ID>": {
      "effects": [{"op": "damage", "n": 6, "tgt": "enemy"}, ...],
      "unknown": ["SomeVar"],          // 任意: 未解釈機構の件名
      "prov": "static|trace|observed", // 出所
      "conf": 0.9,                     // 確度(統合の優先順位に使う)
      "mod": "ModId",                  // 任意: 所属 mod
      "mod_ver": "1.2.3"               // 任意: 解析時の mod バージョン
    }
  },
  "powers": { "<POWER_ID>": {...同型...} },
  "mod_versions": {"ModId": "1.2.3"}   // 解析時点の mod バージョン帳
}

mod が更新されたら mod_versions と照合して「その mod 分だけ stale」にし、
再解析対象として返す(全体の無効化はしない)。
"""
from __future__ import annotations

import json
from pathlib import Path

_BUNDLED_RULES = Path(__file__).resolve().parent / "var_rules.json"


def _load_bundled_var_rules() -> dict:
    if _BUNDLED_RULES.exists():
        try:
            return json.loads(_BUNDLED_RULES.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


class CardKB:
    def __init__(self, data: dict | None = None, path: Path | None = None):
        data = data or {}
        self.version = data.get("version", 1)
        self.cards: dict[str, dict] = data.get("cards", {})
        self.powers: dict[str, dict] = data.get("powers", {})
        self.mod_versions: dict[str, str] = data.get("mod_versions", {})
        self.var_rules: dict[str, dict] = data.get("var_rules", {})
        self.path = path
        # キュレーション済みルール(静的解析由来)を常時マージ。
        # card_kb.json 側の var_rules があればそれを優先(新しい観測)。
        bundled = _load_bundled_var_rules()
        if bundled:
            merged = dict(bundled)
            merged.update(self.var_rules)
            self.var_rules = merged

    @classmethod
    def load(cls, path: str | Path) -> "CardKB":
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        return cls(json.loads(p.read_text(encoding="utf-8")), path=p)

    def save(self, path: str | Path | None = None) -> None:
        p = Path(path or self.path)
        payload = {
            "version": self.version,
            "cards": self.cards,
            "powers": self.powers,
            "mod_versions": self.mod_versions,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    def lookup(self, card_id: str | None) -> dict | None:
        if card_id is None:
            return None
        return self.cards.get(card_id)

    def lookup_power(self, power_id: str | None) -> dict | None:
        if power_id is None:
            return None
        return self.powers.get(power_id)

    def lookup_var_rule(self, card: dict, var_name: str) -> dict | None:
        """カードID prefix / pool に合致するルールセットから var の規則を引く。"""
        cid = card.get("id") or ""
        pool = card.get("pool") or ""
        for prefix, ruleset in self.var_rules.items():
            if prefix.startswith("_"):
                continue
            if cid.startswith(prefix) or (pool and pool == prefix):
                rule = ruleset.get(var_name)
                if rule:
                    return rule
        return None

    def record(self, card_id: str, effects: list, prov: str, conf: float,
               mod: str | None = None, mod_ver: str | None = None,
               unknown: list[str] | None = None) -> None:
        """観測/トレース由来の署名を追記する(より高確度のみ上書き)。"""
        cur = self.cards.get(card_id)
        if cur is not None and cur.get("conf", 0) > conf:
            return
        entry = {"effects": effects, "prov": prov, "conf": conf}
        if unknown:
            entry["unknown"] = unknown
        if mod:
            entry["mod"] = mod
        if mod_ver:
            entry["mod_ver"] = mod_ver
        self.cards[card_id] = entry

    def stale_mods(self, current_versions: dict[str, str]) -> list[str]:
        """KB 記録時とバージョンが変わった mod id の一覧(要再解析)。"""
        return [
            mod for mod, ver in self.mod_versions.items()
            if mod in current_versions and current_versions[mod] != ver
        ]
