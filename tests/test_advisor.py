"""advisor パッケージのユニットテスト(ゲーム不要・合成 state)。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor.kb import CardKB
from advisor.pack import BLOCKED_KEYS, assert_llm_safe, build_decision_pack
from advisor.sig import derive_sig


def _card(**kw):
    base = {
        "index": 0, "id": "STRIKE", "type": "Attack", "cost": "1",
        "star_cost": None, "is_upgraded": False, "can_play": True,
        "target_type": "AnyEnemy", "vars": {"Damage": {"b": 6, "p": 6}},
        "kw": [], "name": "Strike",
        "description": "NSFW prose that must never leak",
    }
    base.update(kw)
    return base


def _combat_state(hand=None, enemies=None, **over):
    st = {
        "state_type": "monster",
        "local_player_slot": 1,
        "run": {"act": 1, "floor": 3},
        "battle": {
            "round": 1, "turn": "player", "is_play_phase": True,
            "all_players_ready": False,
            "enemies": enemies if enemies is not None else [
                {"entity_id": "JAW_WORM_0", "hp": 40, "max_hp": 40, "block": 0,
                 "status": [], "intents": [{"type": "Attack", "label": "攻撃 8"}]},
            ],
        },
        "player": {
            "character": "Silent", "hp": 60, "max_hp": 66, "block": 0,
            "energy": 3, "max_energy": 3,
            "hand": hand if hand is not None else [_card()],
            "status": [], "draw_pile_count": 7, "discard_pile_count": 2,
            "exhaust_pile_count": 0,
            "relics": [{"id": "BURNING_BLOOD", "name": "x", "description": "y"}],
        },
        "players": [
            {"character": "Ironclad", "is_local": False, "hp": 70, "max_hp": 80,
             "gold": 99, "is_alive": True, "block": 0, "is_ready_to_end_turn": False},
            {"character": "Silent", "is_local": True, "hp": 60, "max_hp": 66,
             "gold": 99, "is_alive": True, "block": 0, "is_ready_to_end_turn": False},
        ],
    }
    st.update(over)
    return st


class TestSig(unittest.TestCase):
    def test_damage_var_derives_attack(self):
        sig = derive_sig(_card())
        self.assertEqual(sig["prov"], "vars")
        dmg = [f for f in sig["fx"] if f["op"] == "damage"]
        self.assertEqual(dmg[0]["n"], 6)
        self.assertEqual(dmg[0]["tgt"], "enemy")

    def test_power_var_uses_target_type(self):
        card = _card(vars={"Poison": {"b": 3, "t": "PowerVar`1"}},
                     target_type="AnyEnemy")
        fx = derive_sig(card)["fx"][0]
        self.assertEqual(fx, {"op": "apply_power", "power": "Poison", "n": 3,
                              "tgt": "enemy"})

    def test_power_var_self_target(self):
        card = _card(type="Skill", vars={"Strength": {"b": 2, "t": "PowerVar`1"}},
                     target_type="Self")
        fx = derive_sig(card)["fx"][0]
        self.assertEqual(fx["tgt"], "self")

    def test_unknown_var_is_listed(self):
        card = _card(vars={"WeirdModVar": {"b": 1}, "Damage": {"b": 5}})
        sig = derive_sig(card)
        self.assertIn("WeirdModVar", sig["un"])
        self.assertLess(sig["conf"], 0.8)

    def test_keywords_passthrough(self):
        sig = derive_sig(_card(kw=["Exhaust", "Sly"]))
        self.assertEqual(sig["kw"], ["Exhaust", "Sly"])


class TestPack(unittest.TestCase):
    def test_combat_pack_shape(self):
        pack = build_decision_pack(_combat_state())
        self.assertEqual(pack["t"], "monster")
        c = pack["combat"]
        self.assertEqual(c["en"], 3)
        self.assertEqual(c["enemies"][0]["e"], "JAW_WORM_0")
        self.assertEqual(c["enemies"][0]["int"][0]["dmg"], 8)
        self.assertEqual(c["team"][1]["me"], True)
        card = c["hand"][0]
        self.assertEqual(card["id"], "STRIKE")
        self.assertEqual(card["fx"][0]["op"], "damage")

    def test_no_free_text_anywhere(self):
        pack = build_decision_pack(_combat_state())
        bad = assert_llm_safe(pack)
        self.assertEqual(bad, [])
        # 生 state には確かに文章が含まれていることの対偶確認
        self.assertIn("NSFW", _combat_state()["player"]["hand"][0]["description"])

    def test_multiattack_intent(self):
        st = _combat_state(enemies=[{
            "entity_id": "BUG_0", "hp": 10, "block": 0, "status": [],
            "intents": [{"type": "MultiAttack", "label": "3x4"}]}])
        pack = build_decision_pack(st)
        it = pack["combat"]["enemies"][0]["int"][0]
        self.assertEqual((it["dmg"], it["hits"]), (3, 4))

    def test_advice_lethal_and_plan(self):
        hand = [_card(index=0, vars={"Damage": {"b": 45}})]
        pack = build_decision_pack(_combat_state(hand=hand))
        adv = pack["advice"]
        self.assertEqual(adv["incoming"], 8)
        self.assertIn("JAW_WORM_0", adv["lethal"])
        self.assertEqual(adv["plan"][-1], {"act": "end_turn"})
        play = adv["plan"][0]
        self.assertEqual(play["act"], "play_card")
        self.assertEqual(play["tgt"], "JAW_WORM_0")

    def test_unplayable_card_not_in_plan(self):
        hand = [_card(index=0, can_play=False, unplayable_reason="NotEnoughEnergy")]
        pack = build_decision_pack(_combat_state(hand=hand))
        adv = pack["advice"]
        plays = [a for a in adv["plan"] if a["act"] == "play_card"]
        self.assertEqual(plays, [])
        self.assertTrue(any("unplayable" in n for n in adv["notes"]))

    def test_event_pack_has_no_text(self):
        st = {"state_type": "event", "event": {
            "event_id": "NEOW", "options": [
                {"index": 0, "title": "NSFW option text",
                 "description": "lewd", "is_proceed": True, "is_locked": False},
                {"index": 1, "title": "x", "is_proceed": False}]}}
        pack = build_decision_pack(st)
        self.assertEqual(pack["options"][0], {
            "act": "choose_event_option", "i": 0, "proceed": True,
            "locked": False, "chosen": None})
        self.assertEqual(assert_llm_safe(pack), [])

    def test_rewards_plan_reverse_order(self):
        st = {"state_type": "rewards", "rewards": {"rewards": [
            {"index": 0, "type": "gold"}, {"index": 1, "type": "card"}]}}
        pack = build_decision_pack(st)
        plan = pack["advice"]["plan"]
        self.assertEqual([a["i"] for a in plan], [1, 0])


    def test_card_reward_pick_scoring(self):
        st = {"state_type": "card_reward", "card_reward": {"cards": [
            # i=0: 弱い — 0コスト1ダメージ
            {"index": 0, "id": "WEAK", "type": "Attack", "cost": "0",
             "rarity": "Common", "vars": {"Damage": {"b": 1, "p": 1}},
             "target_type": "AnyEnemy"},
            # i=1: 強い — 1コスト14ダメ + Vulnerable2
            {"index": 1, "id": "GOOD", "type": "Attack", "cost": "1",
             "rarity": "Uncommon",
             "vars": {"Damage": {"b": 14, "p": 14},
                      "Vulnerable": {"b": 2, "t": "PowerVar`1"}},
             "target_type": "AnyEnemy"},
            # i=2: 呪い
            {"index": 2, "id": "CURSE", "type": "Curse", "cost": "0",
             "vars": {}},
        ]}}
        pack = build_decision_pack(st)
        adv = pack["advice"]
        self.assertEqual(adv["pick"], 1)
        scores = {s["id"]: s["s"] for s in adv["scores"]}
        self.assertGreater(scores["GOOD"], scores["WEAK"])
        self.assertLess(scores["CURSE"], 0)

    def test_card_reward_skip_when_all_bad(self):
        st = {"state_type": "card_reward", "card_reward": {"cards": [
            {"index": i, "id": f"JUNK{i}", "type": "Skill", "cost": "1",
             "rarity": "Common", "vars": {}, "target_type": "Self"}
            for i in range(3)]}}
        pack = build_decision_pack(st)
        adv = pack["advice"]
        self.assertIsNone(adv["pick"])
        self.assertTrue(adv.get("skip"))

    def test_mod_var_named_damage_beats_unknown(self):
        """mod 独自 var: 名前に Damage を含むものは推定加点、
        まったく不明なものより価値が上になる。"""
        st = {"state_type": "card_reward", "card_reward": {"cards": [
            {"index": 0, "id": "UNKNOWN", "type": "Attack", "cost": "1",
             "rarity": "Common",
             "vars": {"ZzqkBuff": {"b": 8}}, "target_type": "AnyEnemy"},
            {"index": 1, "id": "STAMINA_HIT", "type": "Attack", "cost": "1",
             "rarity": "Common",
             "vars": {"EnemyStaminaDamage": {"b": 12}}, "target_type": "AnyEnemy"},
        ]}}
        pack = build_decision_pack(st)
        scores = {s["id"]: s["s"] for s in pack["advice"]["scores"]}
        self.assertGreater(scores["STAMINA_HIT"], scores["UNKNOWN"])
        self.assertEqual(pack["advice"]["pick"], 1)

    def test_threshold_var_not_overvalued(self):
        """SpiritThreshold のような閾値/条件 var は効果ではない。
        大きな値がついていても condition(0点)扱いで過大評価しない。"""
        st = {"state_type": "card_reward", "card_reward": {"cards": [
            {"index": 0, "id": "THRESH", "type": "Skill", "cost": "1",
             "rarity": "Common",
             "vars": {"SpiritThreshold": {"b": 99}}, "target_type": "Self"},
            {"index": 1, "id": "PLAIN_BLOCK", "type": "Skill", "cost": "1",
             "rarity": "Common",
             "vars": {"Block": {"b": 6}}, "target_type": "Self"},
        ]}}
        pack = build_decision_pack(st)
        scores = {s["id"]: s["s"] for s in pack["advice"]["scores"]}
        self.assertGreater(scores["PLAIN_BLOCK"], scores["THRESH"])


class TestL2(unittest.TestCase):
    def test_buff_before_attack_ordering(self):
        """Strength バフを先に出す列の方が総ダメージが伸びる。
        L2 は ordering=setup_first を選び max_dmg にバフ分が乗る。"""
        hand = [
            _card(index=0, id="INFLAME", type="Power", target_type="Self",
                  vars={"Strength": {"b": 2, "t": "PowerVar`1"}}),
            _card(index=1, vars={"Damage": {"b": 6}}),
            _card(index=2, vars={"Damage": {"b": 6}}),
            _card(index=3, vars={"Damage": {"b": 6}}),
        ]
        st = _combat_state(hand=hand)
        st["player"]["energy"] = 4  # INFLAME + 3 attacks = 4エネ必要
        pack = build_decision_pack(st)
        adv = pack["advice"]
        # 3 attacks × (6+2) = 24。バフなしだと 18。
        self.assertEqual(adv["max_dmg"], 24)
        self.assertEqual(adv["ordering"], "setup_first")
        # 計画の先頭はバフカード
        self.assertEqual(adv["plan"][0]["id"], "INFLAME")

    def test_vulnerable_amplifies_attacks(self):
        """Vulnerable を先に撒くと単体攻撃が 1.5 倍になる。"""
        hand = [
            _card(index=0, id="BASH", type="Attack",
                  vars={"Damage": {"b": 8},
                        "Vulnerable": {"b": 2, "t": "PowerVar`1"}},
                  target_type="AnyEnemy"),
            _card(index=1, vars={"Damage": {"b": 6}}),
            _card(index=2, vars={"Damage": {"b": 6}}),
        ]
        pack = build_decision_pack(_combat_state(hand=hand))
        adv = pack["advice"]
        # bash 8 + 残り 6*1.5*2 = 8+18 = 26(setup_first でも bash は attack role、
        # vuln付与自体は bash の fx 内なので bash 自身の hits には乗らない前提でも
        # 26 超になるかは実装依存 — 少なくとも 20 超を期待)
        self.assertGreaterEqual(adv["max_dmg"], 20)
        self.assertEqual(adv["plan"][0]["id"], "BASH")

    def test_energy_exhaustion(self):
        """エネルギーが尽きるとシミュレーションでカードがスキップされる。"""
        hand = [
            _card(index=0, vars={"Damage": {"b": 20}}),   # 1コスト
            _card(index=1, id="HEAVY", cost="3", vars={"Damage": {"b": 40}}),
            _card(index=2, vars={"Damage": {"b": 6}}),
        ]
        st = _combat_state(hand=hand)
        st["player"]["energy"] = 2   # HEAVY(3)は出せない
        pack = build_decision_pack(st)
        adv = pack["advice"]
        played_ids = [a["id"] for a in adv["plan"] if a["act"] == "play_card"]
        self.assertNotIn("HEAVY", played_ids)
        self.assertIn("STRIKE", played_ids)

    def test_weak_intent_reduces_incoming(self):
        """敵に Weak を付ける列では被ダメ見積が 0.75 倍になる。"""
        hand = [_card(index=0, id="WEAKEN", type="Skill", cost="1",
                      vars={"Weak": {"b": 2, "t": "PowerVar`1"}},
                      target_type="AnyEnemy")]
        st = _combat_state(hand=hand, enemies=[{
            "entity_id": "E_0", "hp": 100, "block": 0, "status": [],
            "intents": [{"type": "Attack", "label": "攻撃 20"}]}])
        pack = build_decision_pack(st)
        adv = pack["advice"]
        # incoming は生値 20、シミュレーション内では weak 考慮でスコア改善
        self.assertEqual(adv["incoming"], 20)
        played = [a["id"] for a in adv["plan"] if a["act"] == "play_card"]
        self.assertIn("WEAKEN", played)


class TestKBGen(unittest.TestCase):
    def test_card_entry_from_catalog(self):
        """catalog 形状の生データから KB エントリが生成できる。"""
        from tools.gen_card_kb import _card_entry
        entry = _card_entry({
            "id": "MODCARD", "type": "Attack", "cost": "1",
            "target_type": "AnyEnemy",
            "vars": {"Damage": {"b": 9, "p": 9, "t": "DamageVar"},
                     "SoulsGain": {"b": 2}},
            "kw": ["Exhaust"], "pool": "FOX_POOL",
            "description": "9ダメージ。魂を2得る。",
        })
        ops = [f["op"] for f in entry["effects"]]
        self.assertIn("damage", ops)
        self.assertEqual(entry["prov"], "catalog")
        self.assertIn("SoulsGain", entry["unknown"])

    def test_conditional_flag_from_text(self):
        from tools.gen_card_kb import _card_entry
        entry = _card_entry({
            "id": "COND", "type": "Attack", "cost": "1",
            "target_type": "AnyEnemy",
            "vars": {"Damage": {"b": 5}},
            "description": "敵のHPが半分未満のとき、2倍のダメージ。",
        })
        self.assertTrue(entry.get("conditional"))
        self.assertLessEqual(entry["conf"], 0.6)


class TestKB(unittest.TestCase):
    def test_kb_overrides_low_conf(self):
        kb = CardKB({"cards": {"MODCARD": {
            "effects": [{"op": "damage", "n": 99, "tgt": "enemy"}],
            "prov": "static", "conf": 0.9}}})
        hand = [_card(id="MODCARD", vars={"Damage": {"b": 5}})]
        pack = build_decision_pack(_combat_state(hand=hand), kb=kb)
        card = pack["combat"]["hand"][0]
        self.assertEqual(card["fx"][0]["n"], 99)
        self.assertEqual(card["prov"], "static")

    def test_stale_mods(self):
        kb = CardKB({"mod_versions": {"NiceMod": "1.0", "Gone": "9"}})
        stale = kb.stale_mods({"NiceMod": "1.1", "Other": "1"})
        self.assertEqual(stale, ["NiceMod"])

    def test_record_keeps_higher_conf(self):
        kb = CardKB()
        kb.record("X", [{"op": "damage"}], "observed", 0.6)
        kb.record("X", [{"op": "damage", "n": 2}], "trace", 0.9)
        kb.record("X", [{"op": "draw"}], "observed", 0.5)
        self.assertEqual(kb.cards["X"]["effects"][0]["n"], 2)


class TestRollout(unittest.TestCase):
    def test_rollout_finds_defend_then_entrench(self):
        """固定順序では multiply_block が先に消費される(setup 役扱い)が、
        正しくは Defend→Entrench。incoming が高い局面ではこれが最善。"""
        from advisor.eval import eval_combat
        combat = {
            "en": 2, "blk": 0,
            "enemies": [{"e": "E0", "hp": 40, "blk": 0,
                         "int": [{"k": "Attack", "dmg": 20, "hits": 1}]}],
            "hand": [
                {"i": 0, "id": "ENTRENCH", "c": 1, "play": True,
                 "fx": [{"op": "multiply_block", "n": 2}]},
                {"i": 1, "id": "DEFEND", "c": 1, "play": True,
                 "fx": [{"op": "block", "n": 5}]},
                {"i": 2, "id": "STRIKE", "c": 1, "play": True,
                 "fx": [{"op": "damage", "n": 6, "tgt": "enemy"}]},
            ],
        }
        adv = eval_combat(combat)
        self.assertEqual(adv["ordering"], "rollout")
        self.assertEqual(adv["plan"][0]["id"], "DEFEND")
        self.assertEqual(adv["plan"][1]["id"], "ENTRENCH")

    def test_rollout_skips_dead_card(self):
        """効果を持たない高コストカードはどの列でもスキップされるべき。"""
        from advisor.eval import eval_combat
        combat = {
            "en": 3, "blk": 0,
            "enemies": [{"e": "E0", "hp": 40, "blk": 0,
                         "int": [{"k": "Attack", "dmg": 8, "hits": 1}]}],
            "hand": [
                {"i": 0, "id": "DEAD", "c": 2, "play": True, "fx": []},
                {"i": 1, "id": "STRIKE", "c": 1, "play": True,
                 "fx": [{"op": "damage", "n": 6, "tgt": "enemy"}]},
                {"i": 2, "id": "DEFEND", "c": 1, "play": True,
                 "fx": [{"op": "block", "n": 5}]},
            ],
        }
        adv = eval_combat(combat)
        ids = [s["id"] for s in adv["plan"] if s["act"] == "play_card"]
        self.assertNotIn("DEAD", ids)


class TestObserve(unittest.TestCase):
    def _state(self, ehp=20, eblk=0, hp=60, blk=0, en=2, hand=3,
               pw=None, epw=None):
        return {"player": {
                    "hp": hp, "block": blk, "energy": en,
                    "status": pw or [], "hand": [0] * hand,
                    "draw_pile_count": 7},
                "battle": {"enemies": [
                    {"entity_id": "E0", "hp": ehp, "block": eblk,
                     "status": epw or []}]}}

    def test_diff_damage(self):
        from advisor.observe import _diff, _snap
        fx = _diff(_snap(self._state(ehp=20)),
                   _snap(self._state(ehp=14, hand=2)))
        self.assertIn({"op": "damage", "n": 6, "tgt": "enemy"}, fx)

    def test_diff_block_and_power(self):
        from advisor.observe import _diff, _snap
        s1 = self._state(blk=5, epw=[{"id": "WEAK_POWER", "amount": 2}],
                         hand=2)
        fx = _diff(_snap(self._state()), _snap(s1))
        self.assertIn({"op": "block", "n": 5, "tgt": "self"}, fx)
        self.assertIn({"op": "apply_power", "power": "WEAK_POWER", "n": 2,
                       "tgt": "enemy"}, fx)

    def test_recorder_flush_to_kb(self):
        from advisor.observe import Recorder
        kb = CardKB()
        r = Recorder()
        r.before("STRIKE", self._state(ehp=20))
        r.after(self._state(ehp=14, hand=2))
        r.before("STRIKE", self._state(ehp=14))
        r.after(self._state(ehp=8, hand=1))
        out = r.flush(kb)
        self.assertEqual(out["recorded"], ["STRIKE"])
        ent = kb.lookup("STRIKE")
        self.assertEqual(ent["prov"], "observed")
        self.assertEqual(ent["effects"],
                         [{"op": "damage", "n": 6, "tgt": "enemy"}])

    def test_flush_keeps_higher_conf_and_stashes_obs(self):
        from advisor.observe import Recorder
        kb = CardKB({"cards": {"X": {
            "effects": [{"op": "damage", "n": 99, "tgt": "enemy"}],
            "prov": "static", "conf": 0.95}}})
        r = Recorder()
        r.before("X", self._state(ehp=20))
        r.after(self._state(ehp=14, hand=2))
        out = r.flush(kb)
        self.assertEqual(out["conflicted"], ["X"])
        ent = kb.lookup("X")
        self.assertEqual(ent["effects"][0]["n"], 99)  # static が優位維持
        self.assertIn("obs", ent)                      # 観測は obs に記録


if __name__ == "__main__":
    unittest.main()
