#!/usr/bin/env python3
"""The conversion rules, each as a pure function. Run: python3 tests/test_rules.py"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import gen3core  # noqa: E402
from gen3core import Tables, u16  # noqa: E402
import vanilla_frlg  # noqa: E402
import frlgplus  # noqa: E402
import fixtures  # noqa: E402
from vanilla_read import read_vanilla  # noqa: E402
import rules  # noqa: E402
from rules import (build_key_flags, convert_bag, convert_daycare, convert_mons,  # noqa: E402
                   remap_ids, zeroed_regions)

DATA = os.path.join(HERE, "..", "scripts", "data")

TV = Tables(layout=vanilla_frlg.LAYOUT)
TF = Tables(layout=frlgplus.LAYOUT)
V, F = TV.layout, TF.layout
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def item(name):
    return next(k for k, r in TF.items.items() if r["name"] == name)


def src(**pockets):
    """Build a read_vanilla-shaped pockets dict from {pocket: [(name, qty)]}."""
    out = {k: [] for k in ("items", "key_items", "poke_balls", "tmhm", "berries")}
    for pocket, entries in pockets.items():
        for slot, (nm, qty) in enumerate(entries):
            iid = item(nm)
            out[pocket].append({"slot": slot, "id": iid, "qty": qty, "name": nm,
                                "frlgplus_pocket": TF.item_pocket(iid)})
    return out


def pocket_items(writes, key, count):
    """Decode an FRLG+ ItemSlot pocket out of the produced bytes."""
    buf = writes[key]
    return [(u16(buf, 4 * i), u16(buf, 4 * i + 2)) for i in range(count)
            if u16(buf, 4 * i) != 0]


def test_the_items_pocket_fans_out_into_three():
    """Vanilla keeps medicine, held items and general items in one pocket.

    FRLG+ has three, so each item must land in the one its own data names.
    """
    writes, losses = convert_bag(
        src(items=[("Potion", 5), ("Escape Rope", 1), ("Leftovers", 1)]), F, TF)
    check(losses == [], f"nothing should be lost: {losses}")
    general = pocket_items(writes, "sb1_bag_items", F["bag_items_count"])
    medicine = pocket_items(writes, "sb1_bag_medicine", F["bag_medicine_count"])
    held = pocket_items(writes, "sb1_bag_held_items", F["bag_held_items_count"])
    check(general == [(item("Escape Rope"), 1)], f"general pocket {general}")
    check(medicine == [(item("Potion"), 5)], f"medicine pocket {medicine}")
    check(held == [(item("Leftovers"), 1)], f"held pocket {held}")


def test_quantities_are_plaintext_until_encrypted_with_the_key():
    """read_vanilla hands the rules DECRYPTED quantities, so convert_bag returns
    plaintext and encrypt_quantities must apply the key before anything is written.
    Writing plaintext into the save makes FRLG+ read every stack as qty ^ key."""
    writes, _ = convert_bag(src(items=[("Potion", 999)]), F, TF)
    medicine = pocket_items(writes, "sb1_bag_medicine", F["bag_medicine_count"])
    check(medicine == [(item("Potion"), 999)], f"convert_bag should stay plaintext: {medicine}")
    key16 = 0xC0DE
    enc = rules.encrypt_quantities(writes, F, key16)
    medicine = pocket_items(enc, "sb1_bag_medicine", F["bag_medicine_count"])
    check(medicine == [(item("Potion"), 999 ^ key16)], f"not encrypted: {medicine}")
    check(enc["sb1_bag_items"] == writes["sb1_bag_items"],
          "an empty pocket must stay all zero")
    check(rules.encrypt_quantities(enc, F, key16)["sb1_bag_medicine"]
          == writes["sb1_bag_medicine"], "XOR twice should round-trip")


def test_tms_become_one_bit_each():
    """DeserializeTmHmItemSlots reads bit i as ITEM_TM01 + i (src/item.c:65)."""
    writes, losses = convert_bag(src(tmhm=[("TM01", 1), ("TM05", 1)]), F, TF)
    bits = writes["sb1_bag_tmhm_bits"]
    check(len(bits) == F["bag_tmhm_bytes"], f"bitfield is {len(bits)} bytes")
    for n, want in ((0, True), (4, True), (1, False), (57, False)):
        got = bool((bits[n // 8] >> (n % 8)) & 1)
        check(got == want, f"TM bit {n}: expected {want}, got {got}")
    check(losses == [], f"one of each TM loses nothing: {losses}")


def test_the_tm_bit_comes_from_the_table_not_from_id_arithmetic():
    """Defends the lookup in tables.tmhm against a regression to id - item_tm01.

    In today's item data the two agree for every entry, so no real TM can tell
    them apart. This swaps two table entries on a wrapper, so the table position
    and the arithmetic position of TM01 and TM02 disagree; only a rule that reads
    the table puts the bits where the table says. A real HM is checked as well.
    """
    class Swapped:
        def __init__(self, real):
            self._real = real
            self.tmhm = list(real.tmhm)
            self.tmhm[0], self.tmhm[1] = self.tmhm[1], self.tmhm[0]

        def __getattr__(self, name):
            return getattr(self._real, name)

    writes, _ = convert_bag(src(tmhm=[("TM01", 1)]), F, Swapped(TF))
    bits = writes["sb1_bag_tmhm_bits"]
    check(not bits[0] & 1 and (bits[0] >> 1) & 1,
          f"TM01 should sit at its table position 1, bits {bits[0]:08b}")
    hm = len(TF.tmhm) - 1
    writes, _ = convert_bag(src(tmhm=[(TF.item_name(TF.tmhm[hm][0]), 1)]), F, TF)
    bits = writes["sb1_bag_tmhm_bits"]
    check((bits[hm // 8] >> (hm % 8)) & 1, f"last HM bit {hm} not set")


def test_a_duplicate_tm_is_reported_as_the_one_unavoidable_loss():
    """The bitfield has no quantity, so a second copy of a TM cannot be carried."""
    writes, losses = convert_bag(src(tmhm=[("TM01", 3)]), F, TF)
    bits = writes["sb1_bag_tmhm_bits"]
    check(bits[0] & 1, "TM01's bit should still be set")
    kinds = [l["kind"] for l in losses]
    check(kinds == ["tm_quantity"], f"expected one tm_quantity loss, got {losses}")
    check("TM01" in losses[0]["item"], f"the loss should name the TM: {losses[0]}")


def test_key_items_become_one_byte_indices():
    """FRLG+ stores an index into its own table, not an item id."""
    writes, losses = convert_bag(src(key_items=[("Bicycle", 1)]), F, TF)
    buf = writes["sb1_bag_key_items"]
    check(len(buf) == F["bag_key_items_count"], f"key item pocket is {len(buf)} bytes")
    stored = [b for b in buf if b]
    want = next(s for s, iid in TF.key_item_indices.items() if iid == item("Bicycle"))
    check(stored == [want], f"expected stored index {want}, got {stored}")
    check(losses == [], f"a mappable key item loses nothing: {losses}")


def test_berries_move_to_their_new_home():
    """FRLG+ relocated the berry pouch into what is vanilla's unused_348C."""
    writes, _ = convert_bag(src(berries=[("Oran Berry", 7)]), F, TF)
    check(F["sb1_bag_berries"] != V["sb1_bag_berries"],
          "the berry pouch should have moved between the two games")
    check("sb1_bag_berries" in writes, "the rule must write the FRLG+ berry key")
    berries = pocket_items(writes, "sb1_bag_berries", F["bag_berries_count"])
    check(berries == [(item("Oran Berry"), 7)], f"berries {berries}")


def test_poke_balls_carry_across_unchanged():
    writes, _ = convert_bag(src(poke_balls=[("Poké Ball", 10)]), F, TF)
    balls = pocket_items(writes, "sb1_bag_poke_balls", F["bag_poke_balls_count"])
    check(balls == [(item("Poké Ball"), 10)], f"balls {balls}")


def test_an_overflowing_pocket_is_reported_not_silently_dropped():
    """Review Focus 1. Destination capacities alone do not rule out overflow:
    counting distinct FRLG+ items, items is 48 into 47 slots and key_items is 58
    into 36. What does is that every vanilla source pocket is smaller than the
    destinations it feeds (42 Items slots fan into 47/40/52, 30 KeyItems feed 36),
    medicine being the tight path at exactly 40 distinct items for 40 slots. That
    argument assumes one vanilla source per destination; if the item data routed
    several sources into one, it would no longer hold. So no legal bag of distinct
    items overflows. A 42-slot Items pocket can still hold the 40 medicines plus
    two more stacks of ones already present (a hand-edited or glitched bag);
    those two must be reported, never dropped silently."""
    medicines = [r["name"] for r in TF.items.values() if r["pocket"] == "medicine"]
    slots = F["bag_medicine_count"]
    check(len(medicines) == slots, f"{len(medicines)} medicines for {slots} slots")
    stacks = [(nm, 1) for nm in medicines] + [(medicines[0], 2), (medicines[1], 2)]
    writes, losses = convert_bag(src(items=stacks), F, TF)
    kept = pocket_items(writes, "sb1_bag_medicine", slots)
    check(len(kept) == slots, f"{len(kept)} kept, expected {slots}")
    full = [l for l in losses if l["kind"] == "pocket_full"]
    check(len(full) == 2, f"expected 2 pocket_full losses, got {losses}")
    check(all("stack of 2" in l["detail"] for l in full), f"quantity missing: {full}")


def test_every_carried_item_lands_in_the_pocket_frlgplus_assigns_it():
    """The invariant the extractor checks on the result, asserted at the source."""
    writes, losses = convert_bag(
        src(items=[("Potion", 1), ("Escape Rope", 1), ("Leftovers", 1)],
            poke_balls=[("Poké Ball", 1)], berries=[("Oran Berry", 1)],
            key_items=[("Bicycle", 1)]), F, TF)
    check(losses == [], f"nothing should be lost: {losses}")
    by_key = {"sb1_bag_items": "items", "sb1_bag_medicine": "medicine",
              "sb1_bag_held_items": "held_items", "sb1_bag_poke_balls": "poke_balls",
              "sb1_bag_berries": "berry_pouch"}
    for key, pocket in by_key.items():
        cnt = F[key.replace("sb1_bag_", "bag_") + "_count"]
        for iid, _qty in pocket_items(writes, key, cnt):
            check(TF.item_pocket(iid) == pocket,
                  f"{TF.item_name(iid)} written to {pocket} but belongs to "
                  f"{TF.item_pocket(iid)}")


def raw_entry(iid, qty, pocket):
    """A pockets-dict entry whose destination is stated rather than looked up."""
    return {"slot": 0, "id": iid, "qty": qty, "name": f"item {iid}",
            "frlgplus_pocket": pocket}


def lost_kinds(losses):
    return [l["kind"] for l in losses]


def test_an_item_with_no_key_item_index_is_reported():
    """A key-item destination that FRLG+'s index table lacks is not carried."""
    mapped = set(TF.key_item_indices.values())
    stray = next(i for i in TF.items if i not in mapped)
    pockets = src()
    pockets["key_items"].append(raw_entry(stray, 1, "key_items"))
    writes, losses = convert_bag(pockets, F, TF)
    check(lost_kinds(losses) == ["unmapped_key_item"], f"got {losses}")
    check(not any(writes["sb1_bag_key_items"]), "nothing should be written")


def test_key_items_past_the_pocket_are_reported_as_full():
    ids = list(TF.key_item_indices.values())
    cap = F["bag_key_items_count"]
    check(len(ids) > cap, f"need more than {cap} mappable key items")
    pockets = src()
    pockets["key_items"] = [raw_entry(i, 1, "key_items") for i in ids[:cap + 1]]
    writes, losses = convert_bag(pockets, F, TF)
    check(lost_kinds(losses) == ["pocket_full"], f"got {losses}")
    check(all(writes["sb1_bag_key_items"]), "the pocket should be completely filled")


def test_a_tm_case_item_that_is_not_a_tm_is_reported_as_unknown():
    pockets = src()
    pockets["tmhm"].append(raw_entry(item("Potion"), 1, "tm_case"))
    writes, losses = convert_bag(pockets, F, TF)
    check(lost_kinds(losses) == ["unknown_item"], f"got {losses}")
    check(not any(writes["sb1_bag_tmhm_bits"]), "no TM bit should be set")


def test_an_item_with_no_frlgplus_pocket_is_reported_as_unknown():
    unknown_id = max(TF.items) + 1000
    check(TF.item_pocket(unknown_id) == "unknown", "premise: absent id is unknown")
    pockets = src()
    pockets["items"].append(raw_entry(unknown_id, 4, TF.item_pocket(unknown_id)))
    _, losses = convert_bag(pockets, F, TF)
    check(lost_kinds(losses) == ["unknown_item"], f"got {losses}")


# ---------------------------------------------------------------- the crypto itself
def g_padding(mon):
    """The boxHP/boxStatus/forme halfword as FRLG+ reads it, out of real bytes."""
    return frlgplus.decode_box_padding(
        gen3core.u16(gen3core.decrypt_substructures(mon)[0]["G"], 10))


def test_rewrite_substructures_round_trips_in_every_substructure_order():
    """pid % 24 picks one of 24 storage orders. A rewrite that assumed one order
    would corrupt the other 23, and the game would show a Bad Egg. So walk all of
    them: every other field must come back identical, and the stored checksum must
    verify against the new plaintext."""
    for order_index in range(24):
        pid = 0x10000000 + order_index          # pid % 24 == order_index
        before = fixtures.mon("Pikachu", 25, pid=pid, met=88, party=False)
        subs_before, sum_before = gen3core.decrypt_substructures(before)
        if sum_before != u16(before, 0x1C):
            check(False, f"order {order_index}: the fixture itself does not verify")
            continue
        after = gen3core.rewrite_substructures(before, g_halfword=0xBEEF, met_location=0x42)
        subs_after, sum_after = gen3core.decrypt_substructures(after)
        check(sum_after == u16(after, 0x1C),
              f"order {order_index}: the stored checksum does not verify after the rewrite")
        check(before[:0x1C] == after[:0x1C],
              f"order {order_index}: the unencrypted header changed")
        check(before[0x1E:32] == after[0x1E:32],
              f"order {order_index}: the bytes after the checksum changed")
        check(len(after) == len(before), f"order {order_index}: length changed")
        check(u16(subs_after["G"], 10) == 0xBEEF,
              f"order {order_index}: substructure G's last halfword is "
              f"{u16(subs_after['G'], 10):#x}")
        check(subs_after["M"][1] == 0x42,
              f"order {order_index}: metLocation is {subs_after['M'][1]:#x}")
        check(subs_after["G"][:10] == subs_before["G"][:10],
              f"order {order_index}: the rest of G changed")
        check(subs_after["A"] == subs_before["A"], f"order {order_index}: A changed")
        check(subs_after["E"] == subs_before["E"], f"order {order_index}: E changed")
        check(subs_after["M"][:1] == subs_before["M"][:1]
              and subs_after["M"][2:] == subs_before["M"][2:],
              f"order {order_index}: the rest of M changed")


def test_rewrite_substructures_with_nothing_to_change_returns_the_same_bytes():
    before = fixtures.mon("Gyarados", 40, party=False)
    check(gen3core.rewrite_substructures(before) == before,
          "a no-op rewrite must be byte-identical, not merely equivalent")


def test_rewrite_substructures_keeps_a_party_mon_s_tail():
    """A party Pokémon is the same 80 bytes plus a 20-byte tail. Re-encrypting must
    not disturb the tail, or the level and stats the game reads would change."""
    before = fixtures.mon("Bulbasaur", 5)
    check(len(before) == 100, f"premise: a party mon is 100 bytes, got {len(before)}")
    after = gen3core.rewrite_substructures(before, g_halfword=0x1234)
    check(len(after) == 100, f"the rewrite returned {len(after)} bytes")
    check(after[80:] == before[80:], "the party tail changed")


def test_rewrite_substructures_refuses_a_pokemon_whose_checksum_does_not_verify():
    """Editing one would turn detectable corruption into valid-looking corruption:
    the rewrite would compute a checksum over the garbage and the game would then
    accept it. So it raises instead."""
    bad = fixtures.mon("Charmander", 5, bad_checksum=True, party=False)
    check(gen3core.decrypt_substructures(bad)[1] != u16(bad, 0x1C),
          "premise: the fixture's stored checksum should not verify")
    for kwargs in ({"g_halfword": 1}, {"met_location": 1}, {}):
        try:
            gen3core.rewrite_substructures(bad, **kwargs)
            check(False, f"rewrite_substructures({kwargs}) should have raised")
        except ValueError as e:
            check("checksum" in str(e).lower(), f"the refusal should say why: {e}")


def test_write_box_padding_is_the_inverse_of_decode_box_padding():
    before = fixtures.mon("Pikachu", 25, party=False)
    after = frlgplus.write_box_padding(before, box_hp=301, box_status=9, forme=2)
    got = g_padding(after)
    check(got == {"box_hp": 301, "box_status": 9, "forme": 2}, f"read back {got}")
    try:
        frlgplus.write_box_padding(before, box_hp=0x400)
        check(False, "an out-of-range boxHP should be refused, not truncated")
    except ValueError:
        pass


# ---------------------------------------------------------------- the six rules
def test_the_daycare_moves_four_bytes_and_its_offspring_field_widens():
    """offspringPersonality is u16 in vanilla and u32 in FRLG+, which is the whole
    reason the struct sits at 0x2F7C instead of the 0x2F80 the comment claims."""
    raw = fixtures.build_vanilla_save(daycare_step=137)
    v = read_vanilla(raw, TV)
    writes = convert_daycare(v["sb1"], V, F)
    check(V["sb1_daycare"] == 0x2F80 and F["sb1_daycare"] == 0x2F7C,
          f"vanilla {V['sb1_daycare']:#x}, FRLG+ {F['sb1_daycare']:#x}")
    check(V["daycare_offspring_width"] == 2 and F["daycare_offspring_width"] == 4,
          "offspring widths should be 2 and 4")
    step = writes["sb1_daycare_step_counter"]
    check(step == bytes([137]), f"step counter should carry: {step!r}")
    mons = writes["sb1_daycare"]
    check(len(mons) == F["daycare_mon_size"] * F["daycare_mon_count"],
          f"the mons block is {len(mons)} bytes")
    check(len(writes["sb1_daycare_offspring"]) == F["daycare_offspring_width"],
          f"offspring is {len(writes['sb1_daycare_offspring'])} bytes, "
          f"expected {F['daycare_offspring_width']}")


def test_the_daycare_carries_both_stored_pokemon_and_the_widened_offspring():
    """The two DaycareMon structures are byte-identical between the games, so they
    must come across verbatim; only offspringPersonality is re-packed wider."""
    boxed = fixtures.mon("Bulbasaur", 5, party=False)
    sb1 = bytearray(V["sb1_size"])
    span = V["daycare_mon_size"]
    sb1[V["sb1_daycare"]:V["sb1_daycare"] + 80] = boxed
    sb1[V["sb1_daycare"] + span:V["sb1_daycare"] + span + 80] = boxed
    struct.pack_into("<H", sb1, V["sb1_daycare_offspring"], 0xABCD)
    sb1[V["sb1_daycare_step_counter"]] = 42
    writes = convert_daycare(bytes(sb1), V, F)
    mons = writes["sb1_daycare"]
    check(mons[:80] == boxed, "the first stored Pokemon did not carry verbatim")
    check(mons[span:span + 80] == boxed, "the second stored Pokemon did not carry")
    check(writes["sb1_daycare_offspring"] == struct.pack("<I", 0xABCD),
          f"offspring should widen to u32: {writes['sb1_daycare_offspring']!r}")
    check(writes["sb1_daycare_step_counter"] == bytes([42]), "the step counter changed")


def test_every_occupied_slot_gets_its_box_hp_written():
    """Spec 3.3 item 3 and the owner's requirement 1: a boxed Pokemon left at
    boxHP 0 withdraws fainted once No Free Heals or Nuzlocke is on."""
    boxes = [[None] * TV.layout["in_box_count"] for _ in range(TV.layout["total_boxes"])]
    boxes[0][0] = fixtures.mon("Pikachu", 25, party=False)
    boxes[3][11] = fixtures.mon("Gyarados", 40, party=False)
    raw = fixtures.build_vanilla_save(boxes=boxes, party=[fixtures.mon("Bulbasaur", 5)])
    v = read_vanilla(raw, TV)
    out, skipped, _ = convert_mons(v, F, TF, source_version="fr")
    check(skipped == [], f"nothing should be skipped here: {skipped}")
    for where, mon80 in (("box 1 slot 1", out["boxes"][0][0]),
                         ("box 4 slot 12", out["boxes"][3][11])):
        padding = g_padding(mon80)
        check(padding["box_hp"] > 0, f"{where}: boxHP is still 0")
        check(padding["box_status"] == 0, f"{where}: boxStatus {padding['box_status']}")
    check(out["boxes"][0][1] is None, "an empty slot must stay empty")
    check(g_padding(out["party"][0])["box_hp"] > 0, "the party slot got no boxHP")


def test_box_hp_equals_the_computed_max_hp():
    raw = fixtures.build_vanilla_save(
        boxes=[[fixtures.mon("Pikachu", 25, party=False)] + [None] * 29]
              + [[None] * 30 for _ in range(13)])
    v = read_vanilla(raw, TV)
    out, _, _ = convert_mons(v, F, TF, source_version="fr")
    want = v["boxes"][0][0]["stats"]["HP"]
    got = g_padding(out["boxes"][0][0])["box_hp"]
    check(got == want, f"boxHP {got} should equal max HP {want}")


def test_an_egg_and_a_bad_checksum_slot_are_skipped_not_guessed():
    """Review Focus 2. There are no stats on an egg, and a failing checksum means
    the numbers are garbage; writing either would turn garbage into valid garbage."""
    boxes = [[fixtures.mon("Bulbasaur", 5, is_egg=True, party=False),
              fixtures.mon("Charmander", 5, bad_checksum=True, party=False)]
             + [None] * 28] + [[None] * 30 for _ in range(13)]
    raw = fixtures.build_vanilla_save(boxes=boxes)
    v = read_vanilla(raw, TV)
    out, skipped, _ = convert_mons(v, F, TF, source_version="fr")
    reasons = " ".join(skipped).lower()
    check(len(skipped) == 2, f"both slots should be skipped, got {skipped}")
    check("egg" in reasons, f"the egg's skip should say why: {skipped}")
    check("checksum" in reasons, f"the bad slot's skip should say why: {skipped}")
    check(out["boxes"][0][0] == boxes[0][0], "the egg's bytes must be untouched")
    check(out["boxes"][0][1] == boxes[0][1], "the bad slot's bytes must be untouched")


def test_a_skipped_slot_does_not_shift_the_pokemon_after_it():
    """party and party_raw are indexed by PHYSICAL slot, so a skip has to leave the
    original bytes at its own index. Compacting here would move everything after it
    down a slot and the game would read a shifted party."""
    party = [fixtures.mon("Bulbasaur", 5, bad_checksum=True),
             fixtures.mon("Pikachu", 25),
             fixtures.mon("Gyarados", 40, is_egg=True),
             fixtures.mon("Charmander", 5)]
    raw = fixtures.build_vanilla_save(party=party)
    v = read_vanilla(raw, TV)
    out, skipped, _ = convert_mons(v, F, TF, source_version="fr")
    check(len(out["party"]) == len(party),
          f"party should stay {len(party)} long, got {len(out['party'])}")
    check(len(skipped) == 2, f"two slots should be skipped: {skipped}")
    check(out["party"][0] == party[0], "the bad-checksum slot must keep its bytes")
    check(out["party"][2] == party[2], "the egg must keep its bytes")
    for i in (1, 3):
        check(out["party"][i] != party[i], f"slot {i} should have been rewritten")
        check(out["party"][i][:8] == party[i][:8],
              f"slot {i} is no longer the same Pokemon (pid/otid moved)")
        check(g_padding(out["party"][i])["box_hp"] > 0, f"slot {i} got no boxHP")


def test_deoxys_forme_follows_the_source_version():
    """FR shows Deoxys in its Attack forme, LG in Defense; FRLG+ stores it."""
    for version, want in (("fr", 1), ("lg", 2)):
        raw = fixtures.build_vanilla_save(
            boxes=[[fixtures.mon("Deoxys", 30, party=False)] + [None] * 29]
                  + [[None] * 30 for _ in range(13)])
        v = read_vanilla(raw, TV)
        out, _, decisions = convert_mons(v, F, TF, source_version=version)
        got = g_padding(out["boxes"][0][0])["forme"]
        check(got == want, f"{version}: Deoxys forme {got}, expected {want}")
        check(any("Deoxys" in d for d in decisions),
              f"{version}: the forme choice should be reported: {decisions}")
        check(any(gen3core.DEOXYS_FORMES[want] in d for d in decisions),
              f"{version}: the forme should be named, not numbered: {decisions}")


def test_a_non_deoxys_species_gets_forme_zero():
    raw = fixtures.build_vanilla_save(
        boxes=[[fixtures.mon("Pikachu", 10, party=False)] + [None] * 29]
              + [[None] * 30 for _ in range(13)])
    v = read_vanilla(raw, TV)
    out, _, _ = convert_mons(v, F, TF, source_version="fr")
    got = g_padding(out["boxes"][0][0])["forme"]
    check(got == 0, f"forme should be 0, got {got}")


def test_a_pokemon_s_met_location_is_remapped_in_the_same_pass():
    """One decrypt per Pokemon has to do both jobs, so check the met location moved
    as well as the padding halfword, on the same bytes."""
    remaps = vanilla_frlg.remaps(DATA)
    old, new = next((v, f) for v, f in remaps["mapsec"].items() if v != f)
    raw = fixtures.build_vanilla_save(
        boxes=[[fixtures.mon("Pikachu", 10, met=old, party=False)] + [None] * 29]
              + [[None] * 30 for _ in range(13)])
    v = read_vanilla(raw, TV)
    out, skipped, _ = convert_mons(v, F, TF, source_version="fr")
    check(skipped == [], f"nothing should be skipped: {skipped}")
    mon80 = out["boxes"][0][0]
    m = gen3core.decrypt_substructures(mon80)[0]["M"]
    check(m[1] == new, f"metLocation {old} should become {new}, got {m[1]}")
    check(g_padding(mon80)["box_hp"] > 0, "the same pass should also have set boxHP")


def test_box_hp_is_clamped_to_the_width_of_the_field():
    """boxHP is 10 bits, so 1023 is the most that can be stored; one more would
    overflow into boxStatus and the Pokemon would come out of the box poisoned.

    No legal Pokemon gets near it — the game's own cap is 714, and Blissey at level
    100 with max HP EVs is exactly that — so this drives the clamp with a stat that
    could only come from a hand-edited or glitched save, the same way the bag's
    overflow test does. Without the clamp, encode_box_padding refuses the value and
    the whole conversion raises instead of carrying the Pokemon.
    """
    check(rules.BOX_HP_FIELD_MAX == 0x3FF,
          f"the field is 10 bits, so the clamp is 0x3FF, not "
          f"{rules.BOX_HP_FIELD_MAX:#x}")
    raw = fixtures.build_vanilla_save(
        boxes=[[fixtures.mon("Pikachu", 25, party=False)] + [None] * 29]
              + [[None] * 30 for _ in range(13)])
    v = read_vanilla(raw, TV)
    real_hp = v["boxes"][0][0]["stats"]["HP"]
    check(real_hp <= rules.BOX_HP_FIELD_MAX,
          f"premise: a real Pokemon's max HP ({real_hp}) is inside the field")
    v["boxes"][0][0]["stats"]["HP"] = 5000
    out, skipped, _ = convert_mons(v, F, TF, source_version="fr")
    check(skipped == [], f"an over-cap stat is clamped, not skipped: {skipped}")
    padding = g_padding(out["boxes"][0][0])
    check(padding["box_hp"] == rules.BOX_HP_FIELD_MAX,
          f"boxHP should clamp to {rules.BOX_HP_FIELD_MAX}, got {padding['box_hp']}")
    check(padding["box_status"] == 0,
          f"the clamp must not spill into boxStatus, got {padding['box_status']}")
    check(padding["forme"] == 0, f"nor into forme, got {padding['forme']}")


def test_cerulean_cave_is_remapped_and_pallet_town_is_not():
    """The finding this whole remap exists for: FRLG+ inserts three Safari Zone
    maps into gMapGroup_Dungeons, so a save made in Cerulean Cave would otherwise
    load into a Safari Zone map."""
    remaps = vanilla_frlg.remaps(DATA)
    rows = {name: (a, b) for a, b, name in
            vanilla_frlg.remap_rows(DATA, "remap_maps.txt")}
    (vg, vn), (fg, fn) = rows["CeruleanCave_1F"]
    raw = fixtures.build_vanilla_save(map_group=vg, map_num=vn)
    v = read_vanilla(raw, TV)
    writes, notes = remap_ids(v["sb1"], V, F, remaps)
    loc = writes["sb1_location"]
    check((loc[0], loc[1]) == (fg, fn),
          f"location should become {(fg, fn)}, got {(loc[0], loc[1])}")
    check((vg, vn) != (fg, fn), "premise: Cerulean Cave 1F should actually move")
    (pg, pn), (pfg, pfn) = rows["PalletTown"]
    raw2 = fixtures.build_vanilla_save(map_group=pg, map_num=pn)
    v2 = read_vanilla(raw2, TV)
    w2, _ = remap_ids(v2["sb1"], V, F, remaps)
    check((w2["sb1_location"][0], w2["sb1_location"][1]) == (pfg, pfn),
          "Pallet Town should not move")


def test_every_stored_warp_and_the_quest_log_are_remapped():
    """All five warps and the quest log's own map triples, not just `location`: a
    missed one sends the player's escape rope or heal point to the wrong map."""
    remaps = vanilla_frlg.remaps(DATA)
    rows = {name: (a, b) for a, b, name in
            vanilla_frlg.remap_rows(DATA, "remap_maps.txt")}
    (vg, vn), (fg, fn) = rows["CeruleanCave_1F"]
    v = read_vanilla(fixtures.build_vanilla_save(map_group=vg, map_num=vn), TV)
    src = bytearray(v["sb1"])
    stride = V["quest_log_scene_size"]
    for i in range(V["quest_log_scene_count"]):
        o = V["sb1_quest_log"] + i * stride
        src[o + 1], src[o + 2] = vg, vn
    writes, _ = remap_ids(bytes(src), V, F, remaps)
    for key in ("sb1_location", "sb1_continue_game_warp", "sb1_dynamic_warp",
                "sb1_last_heal_location", "sb1_escape_warp"):
        got = (writes[key][0], writes[key][1])
        check(got == (fg, fn), f"{key} should be {(fg, fn)}, got {got}")
        check(len(writes[key]) == 8, f"{key} should write a whole 8-byte warp")
        # struct WarpData's other five bytes are warpId and the s16 x, y: where the
        # player is standing INSIDE the map. Only the map moves; zeroing these would
        # drop the player on the map's warp 0 at (0, 0).
        tail = struct.unpack_from("<bxhh", writes[key], 2)
        check(tail == (7, 13, 21),
              f"{key}: warpId/x/y should carry through as (7, 13, 21), got {tail}")
        check(writes[key][2:] == src[V[key] + 2:V[key] + 8],
              f"{key}: the bytes after mapGroup/mapNum were not carried verbatim")
    scenes = writes["sb1_quest_log"]
    check(len(scenes) == stride * V["quest_log_scene_count"],
          f"the quest log write is {len(scenes)} bytes")
    for i in range(V["quest_log_scene_count"]):
        o = i * stride
        check((scenes[o + 1], scenes[o + 2]) == (fg, fn),
              f"quest log scene {i} is ({scenes[o + 1]}, {scenes[o + 2]})")


def test_a_zeroed_quest_log_scene_is_left_alone():
    """(0, 0) means "this scene is unused", but it is also a real map id —
    BattleColosseum_2P, which happens to be identity-mapped today. The guard is what
    makes an unused scene stay unused regardless; without it, the scene's map triple
    would be whatever BattleColosseum_2P's row says.

    So this doctors that one row to a non-identity value. The guard must still leave
    the scene alone; dropping it writes a map into an empty scene.
    """
    remaps = dict(vanilla_frlg.remaps(DATA))
    check(remaps["maps"][(0, 0)] == (0, 0),
          "premise: BattleColosseum_2P is identity-mapped today, which is why the "
          "guard needs a doctored row to be testable at all")
    remaps["maps"] = dict(remaps["maps"])
    remaps["maps"][(0, 0)] = (9, 99)
    raw = fixtures.build_vanilla_save()
    v = read_vanilla(raw, TV)
    writes, _ = remap_ids(v["sb1"], V, F, remaps)
    check(set(writes["sb1_quest_log"]) == {0},
          "an all-zero quest log must come out all zero, not carry (9, 99)")


def test_the_map_layout_id_is_remapped_as_a_halfword():
    remaps = vanilla_frlg.remaps(DATA)
    old, new = next((v, f) for v, f in remaps["layouts"].items() if v != f)
    raw = fixtures.build_vanilla_save(layout_id=old)
    v = read_vanilla(raw, TV)
    writes, _ = remap_ids(v["sb1"], V, F, remaps)
    check(writes["sb1_map_layout_id"] == struct.pack("<H", new),
          f"layout {old} should become {new}, got "
          f"{u16(writes['sb1_map_layout_id'], 0)}")


def test_an_id_with_no_remap_row_passes_through_untouched():
    """Review Focus 4. metLocation 0xFD-0xFF are the METLOC specials, the 18 empty
    vanilla layout slots have no row, and MAPSEC_COUNT is dropped on both sides."""
    remaps = vanilla_frlg.remaps(DATA)
    for special in (0xFD, 0xFE, 0xFF):
        check(special not in remaps["mapsec"],
              f"{special:#x} must not have a remap row")
    raw = fixtures.build_vanilla_save(
        boxes=[[fixtures.mon("Mr. Mime", 20, met=0xFE, party=False)] + [None] * 29]
              + [[None] * 30 for _ in range(13)])
    v = read_vanilla(raw, TV)
    out, skipped, _ = convert_mons(v, F, TF, source_version="fr")
    check(skipped == [], f"an in-game-trade Pokemon is not a skip: {skipped}")
    m = gen3core.decrypt_substructures(out["boxes"][0][0])[0]["M"]
    check(m[1] == 0xFE, f"METLOC_IN_GAME_TRADE should pass through, got {m[1]:#x}")
    # And a layout id with no row must not raise.
    raw2 = fixtures.build_vanilla_save(layout_id=22)   # inside vanilla's empty run
    v2 = read_vanilla(raw2, TV)
    w2, notes = remap_ids(v2["sb1"], V, F, remaps)
    check(any("layout" in n.lower() for n in notes),
          f"an unmapped layout id should be reported: {notes}")
    check(w2["sb1_map_layout_id"] == struct.pack("<H", 22),
          "an unmapped layout id must pass through unchanged")


def test_a_map_with_no_remap_row_is_reported_and_passed_through():
    remaps = vanilla_frlg.remaps(DATA)
    missing = next(((g, n) for g in range(44) for n in range(256)
                    if (g, n) not in remaps["maps"]), None)
    check(missing is not None, "premise: some (group, num) has no row")
    raw = fixtures.build_vanilla_save(map_group=missing[0], map_num=missing[1])
    v = read_vanilla(raw, TV)
    writes, notes = remap_ids(v["sb1"], V, F, remaps)
    got = (writes["sb1_location"][0], writes["sb1_location"][1])
    check(got == missing, f"an unmapped map should pass through, got {got}")
    check(any("no remap row" in n for n in notes),
          f"it should be reported: {notes}")


def test_object_event_drift_on_the_saved_map_is_reported():
    """Spec 7: saved object-event state belongs to the map the player stands on.
    The skill cannot fix a changed object list, only warn."""
    remaps = vanilla_frlg.remaps(DATA)
    counts = remaps["object_counts"]
    check(counts, "premise: remap_objcount.txt should list at least one map")
    by_name = remaps["maps_by_name"]
    name = next((n for n in counts if n in by_name), None)
    check(name is not None, f"a changed map should be findable by name: {list(counts)[:3]}")
    (vg, vn), _ = by_name[name]
    raw = fixtures.build_vanilla_save(map_group=vg, map_num=vn)
    v = read_vanilla(raw, TV)
    _writes, notes = remap_ids(v["sb1"], V, F, remaps)
    check(any(name in n and "object events" in n for n in notes),
          f"the drift on {name} should be reported: {notes}")
    # And a map whose object list did not change must not raise the warning.
    clean = next(n for n, (a, _b) in by_name.items() if n not in counts and a != (vg, vn))
    (cg, cn), _ = by_name[clean]
    v2 = read_vanilla(fixtures.build_vanilla_save(map_group=cg, map_num=cn), TV)
    _w2, notes2 = remap_ids(v2["sb1"], V, F, remaps)
    check(not any("object events" in n for n in notes2),
          f"{clean} should raise no object warning: {notes2}")


def test_key_flags_set_normal_exp_and_the_source_version():
    """A zero-filled key-flag word means expMod == 0, which battle_script_commands.c
    reads as no EXP ever. new_game.c:157 sets it to 2."""
    fr, lg = build_key_flags("fr"), build_key_flags("lg")
    for name, w in (("fr", fr), ("lg", lg)):
        check((w >> 8) & 3 == 2, f"{name}: expMod is {(w >> 8) & 3}, must be 2")
        check(w & 3 == 0, f"{name}: difficulty should be normal")
        check((w >> 3) & 1 == 0, f"{name}: nuzlocke should be off")
        check((w >> 4) & 3 == 0, f"{name}: ivCalcMode should be actual")
        check((w >> 7) & 1 == 0, f"{name}: noPMC should be off")
    check((fr >> 2) & 1 == 0, "FireRed is version bit 0")
    check((lg >> 2) & 1 == 1, "LeafGreen is version bit 1")


def test_key_flags_refuse_a_version_that_is_not_fr_or_lg():
    for bad in ("ruby", "", None, "FR"):
        try:
            build_key_flags(bad)
            check(False, f"build_key_flags({bad!r}) should have raised")
        except ValueError:
            pass


def test_key_flags_read_back_the_way_the_extractor_reads_them():
    """The one end-to-end check that the bit order is right: hand the word to the
    profile's own Key System parser and see Normal / 1x / actual come back."""
    sb1 = bytearray(F["sb1_size"])
    struct.pack_into("<H", sb1, F["sb1_key_flags"], build_key_flags("lg"))
    ks = frlgplus._parse_key_system(bytes(sb1), F)
    check(ks["difficulty"] == "Normal", f"difficulty {ks['difficulty']}")
    check(ks["exp_modifier"] == "1x", f"exp_modifier {ks['exp_modifier']}")
    check(ks["iv_calculation"] == "actual" and ks["ev_calculation"] == "actual",
          f"calc modes {ks['iv_calculation']} / {ks['ev_calculation']}")
    check(ks["nuzlocke"] is False, f"nuzlocke {ks['nuzlocke']}")
    check(ks["no_free_heals"] is False, f"no_free_heals {ks['no_free_heals']}")
    check(ks["version"] == "LeafGreen", f"version {ks['version']}")


def test_the_carved_out_regions_are_zeroed():
    """FRLG+ reads these from space vanilla leaves as filler, so whatever a vanilla
    save happens to hold there must not be inherited as data.

    Every length is pinned against the generated layout rather than against another
    hand-written number: masterTrainerFlags' length is emitted outright
    (master_trainer_flags_bytes), and the two that rules.py still spells as literals
    are checked against the distance to the field that follows them, so a size that
    drifted out of step with the FRLG+ source fails here instead of zeroing the
    wrong span.
    """
    z = zeroed_regions(F)
    sizes = {
        # nuzlockeDupeFlags[52] sits between unused_348C and the relocated berry pouch.
        "sb1_nuzlocke_dupe_flags": F["sb1_bag_berries"] - F["sb1_nuzlocke_dupe_flags"],
        # filler_EasyChatPairs[36] runs from its offset to the Day Care.
        "sb1_filler_easy_chat": F["sb1_daycare"] - F["sb1_filler_easy_chat"],
        # masterTrainerFlags[20] is generated: unused_3A94 + 44 to registeredTexts.
        "sb1_master_trainer_flags": F["master_trainer_flags_bytes"],
        # u8 masterTrainerTitle and u16 lastViewedPokedexEntry.
        "sb1_master_trainer_title": 1,
        "sb1_last_viewed_pokedex_entry": F["sb1_key_flags"] - F["sb1_last_viewed_pokedex_entry"],
    }
    check(sizes["sb1_nuzlocke_dupe_flags"] == 52,
          f"nuzlockeDupeFlags should be 52 bytes, the layout says "
          f"{sizes['sb1_nuzlocke_dupe_flags']}")
    check(sizes["sb1_filler_easy_chat"] == 36,
          f"filler_EasyChatPairs should be 36 bytes, the layout says "
          f"{sizes['sb1_filler_easy_chat']}")
    check(sizes["sb1_master_trainer_flags"] == 20,
          f"masterTrainerFlags should be 20 bytes, the layout says "
          f"{sizes['sb1_master_trainer_flags']}")
    check(sizes["sb1_master_trainer_flags"] * 8 > frlgplus.MASTER_TRAINER_GRANDMASTER,
          f"{sizes['sb1_master_trainer_flags']} bytes cannot hold a bit for "
          f"Grandmaster ({frlgplus.MASTER_TRAINER_GRANDMASTER})")
    for key, size in sizes.items():
        check(key in z, f"{key} should be zeroed")
        if key in z:
            check(len(z[key]) == size, f"{key} is {len(z[key])} bytes, expected {size}")
            check(set(z[key]) == {0}, f"{key} should be all zero")
    leftover = F["sb1_item_block_end"] - (F["sb1_bag_held_items"]
                                          + 4 * F["bag_held_items_count"])
    check(z["__leftover_item_slots"] == bytes(leftover),
          f"the leftover slot region should be {leftover} zero bytes")


def test_no_zeroed_region_overlaps_another_or_runs_past_saveblock1():
    """These are written by offset, so an overlap or an overrun would silently
    clobber a neighbouring field."""
    z = zeroed_regions(F)
    spans = sorted((F[k], F[k] + len(v)) for k, v in z.items() if not k.startswith("__"))
    for (lo, hi), (nlo, _nhi) in zip(spans, spans[1:]):
        check(hi <= nlo, f"region ending {hi:#x} overlaps the one starting {nlo:#x}")
    for lo, hi in spans:
        check(hi <= F["sb1_size"], f"a region runs to {hi:#x}, past sb1 {F['sb1_size']:#x}")
    check(F["sb1_filler_easy_chat"] + len(z["sb1_filler_easy_chat"]) == F["sb1_daycare"],
          "the easy-chat filler must stop exactly where the Day Care starts")


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
