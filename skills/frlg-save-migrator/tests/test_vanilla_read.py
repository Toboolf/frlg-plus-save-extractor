#!/usr/bin/env python3
"""The vanilla reader, on synthetic saves. Run: python3 tests/test_vanilla_read.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

from gen3core import Tables  # noqa: E402
import vanilla_frlg  # noqa: E402
from vanilla_read import read_vanilla  # noqa: E402
import fixtures  # noqa: E402

T = Tables(layout=vanilla_frlg.LAYOUT)
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def test_reads_a_synthetic_vanilla_save():
    raw = fixtures.build_vanilla_save(money=123456, coins=42)
    v = read_vanilla(raw, T)
    check(v["slot"]["valid"], f"slot should be valid: {v['slot']['problems']}")
    check(v["money"] == 123456, f"money {v['money']}")
    check(v["coins"] == 42, f"coins {v['coins']}")
    check(len(v["sb1"]) >= T.layout["sb1_size"], "SaveBlock1 too short")
    check(len(v["boxes"]) == T.layout["total_boxes"], f"{len(v['boxes'])} boxes")
    check(v["player"]["name"] == "ASH", f"name {v['player']['name']!r}")
    check((v["player"]["tid"], v["player"]["sid"]) == (fixtures.TID, fixtures.SID),
          f"ids {v['player']['tid']}/{v['player']['sid']}")


def test_the_cross_check_that_proves_the_key_and_the_offset():
    """GAME_STAT_SAVED_GAME must equal the sector counter: it can only match when
    both the game-stats offset and the encryption key offset are right."""
    raw = fixtures.build_vanilla_save(save_counter=7)
    v = read_vanilla(raw, T)
    check(v["saved_game_stat"] == 7, f"stat {v['saved_game_stat']} vs counter 7")
    check(v["slot"]["counter"] == 7, f"counter {v['slot']['counter']}")


def test_the_newer_slot_wins_and_a_missing_slot_is_tolerated():
    both = read_vanilla(fixtures.build_vanilla_save(save_counter=9), T)
    check(both["slot"]["counter"] == 9 and both["slot"]["index"] == 0,
          f"slot {both['slot']['index']} counter {both['slot']['counter']}")
    one = read_vanilla(fixtures.build_vanilla_save(save_counter=9, one_slot_only=True), T)
    check(one["slot"]["valid"] and one["slot"]["counter"] == 9,
          f"one-slot save: {one['slot']['problems']}")


def test_pockets_carry_the_frlgplus_pocket_each_item_belongs_to():
    """This is what Task 3's re-pocketing keys on, so the reader must supply it."""
    potion = next(k for k, r in T.items.items() if r["name"] == "Potion")
    raw = fixtures.build_vanilla_save(items=[(potion, 3)])
    v = read_vanilla(raw, T)
    got = [i for i in v["pockets"]["items"] if i["id"] == potion]
    check(len(got) == 1, f"expected the Potion in the items pocket, got {v['pockets']['items']}")
    if got:
        check(got[0]["qty"] == 3, f"quantity {got[0]['qty']}")
        check(got[0]["frlgplus_pocket"] == "medicine",
              f"a Potion belongs to FRLG+'s medicine pocket, got {got[0]['frlgplus_pocket']!r}")


def test_a_party_pokemon_decodes_with_the_vanilla_profile():
    raw = fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)])
    v = read_vanilla(raw, T)
    check(len(v["party"]) == 1, f"{len(v['party'])} party mons")
    if v["party"]:
        m = v["party"][0]
        check(m["species"] == "Bulbasaur", f"species {m['species']}")
        check(m["level"] == 5, f"level {m['level']}")
        check(m["hp_current"] == m["stats"]["HP"],
              f"party HP is stored whole: {m['hp_current']} vs {m['stats']['HP']}")
        check(v["party_raw"] == [fixtures.mon("Bulbasaur", 5)],
              "party_raw must be the exact 100 bytes written")


def test_a_hole_in_the_party_keeps_the_raw_slices_on_their_physical_slots():
    """A slot inside partyCount that decodes empty must not shift the Pokémon after it:
    a writer indexes sb1_party + 100*i over these lists."""
    a = fixtures.mon("Bulbasaur", 5, pid=0x01010101)
    b = fixtures.mon("Pidgey", 6, pid=0x02020202)
    c = fixtures.mon("Rattata", 7, pid=0x03030303)
    raw = fixtures.build_vanilla_save(party=[a, bytes(100), b, c])
    v = read_vanilla(raw, T)
    check(len(v["party"]) == 4 and len(v["party_raw"]) == 4,
          f"lengths {len(v['party'])}/{len(v['party_raw'])}, expected 4/4")
    check(v["party"][1] is None, "the hole must decode to None, in place")
    check([m and m["species"] for m in v["party"]] == ["Bulbasaur", None, "Pidgey", "Rattata"],
          f"party order {[m and m['species'] for m in v['party']]}")
    check(v["party_raw"] == [a, bytes(100), b, c], "party_raw is off its physical slots")
    off = T.layout["sb1_party"]
    check(all(v["party_raw"][i] == v["sb1"][off + 100 * i:off + 100 * (i + 1)] for i in range(4)),
          "party_raw[i] is not the bytes at sb1_party + 100*i")


def test_an_incomplete_slot_is_refused_not_zero_filled():
    raw = bytearray(fixtures.build_vanilla_save(one_slot_only=True))
    raw[0xFF8:0xFFC] = bytes(4)            # break one sector's signature in the only slot
    try:
        read_vanilla(bytes(raw), T)
        check(False, "a slot missing a section was read instead of refused")
    except ValueError as e:
        check("missing sections" in str(e), f"refusal does not name the gap: {e}")


def test_boxes_and_raw_slices_line_up():
    mons = [fixtures.mon("Pidgey", 3, party=False, pid=0x01020304),
            fixtures.mon("Rattata", 4, party=False, pid=0x0A0B0C0D)]
    raw = fixtures.build_vanilla_save(boxes=[[mons[0]], [None, mons[1]]])
    v = read_vanilla(raw, T)
    check(v["boxes"][0][0] and v["boxes"][0][0]["species"] == "Pidgey", "box 1 slot 1")
    check(v["boxes"][1][1] and v["boxes"][1][1]["species"] == "Rattata", "box 2 slot 2")
    check(v["boxes"][1][0] is None, "an empty slot must be None")
    check(v["boxes"][0][0] and v["boxes"][0][0]["status"] == "not recorded"
          and v["boxes"][0][0]["hp_current"] is None,
          "a boxed vanilla Pokémon has no recorded HP or status")
    check(len(v["boxes_raw"]) == 14 and all(len(b) == 30 for b in v["boxes_raw"]), "boxes_raw shape")
    check(v["boxes_raw"][0][0] == mons[0] and v["boxes_raw"][1][1] == mons[1], "boxes_raw bytes")
    check(all(len(r) == 80 for b in v["boxes_raw"] for r in b), "every raw slice is 80 bytes")


def test_remaps_ride_along():
    v = read_vanilla(fixtures.build_vanilla_save(), T)
    check(set(v["remaps"]) >= {"maps", "layouts", "mapsec"} and v["remaps"]["maps"],
          f"remaps keys {sorted(v['remaps'])}")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    for f in FAILS:
        print(f"FAIL: {f}")
    print(f"\n{len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
