#!/usr/bin/env python3
"""The conversion rules, each as a pure function. Run: python3 tests/test_rules.py"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

from gen3core import Tables, u16  # noqa: E402
import vanilla_frlg  # noqa: E402
import frlgplus  # noqa: E402
from rules import convert_bag  # noqa: E402

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
        src(items=[("Potion", 5), ("Escape Rope", 1), ("Leftovers", 1)]), V, F, TF)
    check(losses == [], f"nothing should be lost: {losses}")
    general = pocket_items(writes, "sb1_bag_items", F["bag_items_count"])
    medicine = pocket_items(writes, "sb1_bag_medicine", F["bag_medicine_count"])
    held = pocket_items(writes, "sb1_bag_held_items", F["bag_held_items_count"])
    check(general == [(item("Escape Rope"), 1)], f"general pocket {general}")
    check(medicine == [(item("Potion"), 5)], f"medicine pocket {medicine}")
    check(held == [(item("Leftovers"), 1)], f"held pocket {held}")


def test_quantities_survive_because_the_key_does_not_change():
    """SaveBlock2 is never touched, so the XOR key is the same before and after.

    The rule therefore writes quantities already XOR'd, exactly as it found them.
    """
    writes, _ = convert_bag(src(items=[("Potion", 999)]), V, F, TF)
    medicine = pocket_items(writes, "sb1_bag_medicine", F["bag_medicine_count"])
    check(medicine == [(item("Potion"), 999)], f"quantity did not survive: {medicine}")


def test_tms_become_one_bit_each():
    """DeserializeTmHmItemSlots reads bit i as ITEM_TM01 + i (src/item.c:65)."""
    writes, losses = convert_bag(src(tmhm=[("TM01", 1), ("TM05", 1)]), V, F, TF)
    bits = writes["sb1_bag_tmhm_bits"]
    check(len(bits) == F["bag_tmhm_bytes"], f"bitfield is {len(bits)} bytes")
    for n, want in ((0, True), (4, True), (1, False), (57, False)):
        got = bool((bits[n // 8] >> (n % 8)) & 1)
        check(got == want, f"TM bit {n}: expected {want}, got {got}")
    check(losses == [], f"one of each TM loses nothing: {losses}")


def test_a_duplicate_tm_is_reported_as_the_one_unavoidable_loss():
    """The bitfield has no quantity, so a second copy of a TM cannot be carried."""
    writes, losses = convert_bag(src(tmhm=[("TM01", 3)]), V, F, TF)
    bits = writes["sb1_bag_tmhm_bits"]
    check(bits[0] & 1, "TM01's bit should still be set")
    kinds = [l["kind"] for l in losses]
    check(kinds == ["tm_quantity"], f"expected one tm_quantity loss, got {losses}")
    check("TM01" in losses[0]["item"], f"the loss should name the TM: {losses[0]}")


def test_key_items_become_one_byte_indices():
    """FRLG+ stores an index into its own table, not an item id."""
    writes, losses = convert_bag(src(key_items=[("Bicycle", 1)]), V, F, TF)
    buf = writes["sb1_bag_key_items"]
    check(len(buf) == F["bag_key_items_count"], f"key item pocket is {len(buf)} bytes")
    stored = [b for b in buf if b]
    want = next(s for s, iid in TF.key_item_indices.items() if iid == item("Bicycle"))
    check(stored == [want], f"expected stored index {want}, got {stored}")
    check(losses == [], f"a mappable key item loses nothing: {losses}")


def test_berries_move_to_their_new_home():
    """FRLG+ relocated the berry pouch into what is vanilla's unused_348C."""
    writes, _ = convert_bag(src(berries=[("Oran Berry", 7)]), V, F, TF)
    check(F["sb1_bag_berries"] == 0x3570, f"berries should be at 0x3570")
    check(V["sb1_bag_berries"] == 0x54C, f"vanilla berries should be at 0x54C")
    berries = pocket_items(writes, "sb1_bag_berries", F["bag_berries_count"])
    check(berries == [(item("Oran Berry"), 7)], f"berries {berries}")


def test_poke_balls_carry_across_unchanged():
    writes, _ = convert_bag(src(poke_balls=[("Poké Ball", 10)]), V, F, TF)
    balls = pocket_items(writes, "sb1_bag_poke_balls", F["bag_poke_balls_count"])
    check(balls == [(item("Poké Ball"), 10)], f"balls {balls}")


def test_an_overflowing_pocket_is_reported_not_silently_dropped():
    """Review Focus 1. FRLG+'s medicine pocket holds 40 and the item data has exactly
    40 medicines, so no legal bag of distinct items can overflow it. A 42-slot
    vanilla Items pocket can still hold 40 medicines plus two further stacks of
    ones already present (a hand-edited or glitched bag); those two must be
    reported, never dropped silently."""
    medicines = [r["name"] for r in TF.items.values() if r["pocket"] == "medicine"]
    slots = F["bag_medicine_count"]
    check(len(medicines) == slots, f"{len(medicines)} medicines for {slots} slots")
    stacks = [(nm, 1) for nm in medicines] + [(medicines[0], 2), (medicines[1], 2)]
    writes, losses = convert_bag(src(items=stacks), V, F, TF)
    kept = pocket_items(writes, "sb1_bag_medicine", slots)
    check(len(kept) == slots, f"{len(kept)} kept, expected {slots}")
    full = [l for l in losses if l["kind"] == "pocket_full"]
    check(len(full) == 2, f"expected 2 pocket_full losses, got {losses}")


def test_every_carried_item_lands_in_the_pocket_frlgplus_assigns_it():
    """The invariant the extractor checks on the result, asserted at the source."""
    names = ["Potion", "Escape Rope", "Leftovers", "Poké Ball", "Oran Berry", "Bicycle"]
    writes, losses = convert_bag(
        src(items=[("Potion", 1), ("Escape Rope", 1), ("Leftovers", 1)],
            poke_balls=[("Poké Ball", 1)], berries=[("Oran Berry", 1)],
            key_items=[("Bicycle", 1)]), V, F, TF)
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
