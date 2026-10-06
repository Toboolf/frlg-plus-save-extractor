#!/usr/bin/env python3
"""The vanilla layout table says what a vanilla save looks like.

Run: python3 tests/test_vanilla_layout.py

These offsets are the ones the migrator will read a vanilla save with, so they
are pinned here against the values the vanilla bag layout forces: four-byte
ItemSlots, pockets in vanilla's order, and nothing where FRLG+ put its bitfield.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from gen3core import Tables  # noqa: E402
import vanilla_frlg  # noqa: E402
import frlgplus  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


V = Tables(layout=vanilla_frlg.LAYOUT).layout
F = Tables(layout=frlgplus.LAYOUT).layout


def test_vanilla_pocket_offsets():
    """Vanilla: Items[42] 0x310, KeyItems[30] 0x3B8, Balls[13] 0x430, TMHM[58] 0x464,
    Berries[43] 0x54C, ending exactly at seen1 (0x5F8)."""
    expect = {"sb1_bag_items": 0x310, "bag_items_count": 42,
              "sb1_bag_key_items": 0x3B8, "bag_key_items_count": 30,
              "sb1_bag_poke_balls": 0x430, "bag_poke_balls_count": 13,
              "sb1_bag_tmhm": 0x464, "bag_tmhm_count": 58,
              "sb1_bag_berries": 0x54C, "bag_berries_count": 43}
    for k, want in expect.items():
        check(V.get(k) == want, f"vanilla {k}: want {want:#x} got {V.get(k)}")
    end = V["sb1_bag_berries"] + 4 * V["bag_berries_count"]
    check(end == 0x5F8, f"vanilla bag should end at 0x5F8 (seen1), ends at {end:#x}")


def test_vanilla_daycare_is_four_bytes_later_than_frlgplus():
    """FRLG+ replaced dewfordTrends[5] (40 bytes) with a 36-byte filler, moving the
    Day Care from vanilla's 0x2F80 to 0x2F7C."""
    check(V["sb1_daycare"] == 0x2F80, f"vanilla daycare at {V.get('sb1_daycare')}")
    check(F["sb1_daycare"] == 0x2F7C, f"FRLG+ daycare at {F.get('sb1_daycare')}")


def test_daycare_offspring_width_pins_the_geometry():
    """offspringPersonality is u16 in vanilla, u32 in FRLG+; that width is what
    places the step counter and the end of the struct."""
    check(V.get("daycare_offspring_width") == 2, f"vanilla width {V.get('daycare_offspring_width')}")
    check(F.get("daycare_offspring_width") == 4, f"FRLG+ width {F.get('daycare_offspring_width')}")
    check(V["sb1_daycare_step_counter"] == 0x309A, f"vanilla step counter {V['sb1_daycare_step_counter']:#x}")
    check(F["sb1_daycare_step_counter"] == 0x3098, f"FRLG+ step counter {F['sb1_daycare_step_counter']:#x}")
    for name, L in (("vanilla", V), ("FRLG+", F)):
        check(L["sb1_daycare_step_counter"] == L["sb1_daycare_offspring"] + L["daycare_offspring_width"],
              f"{name} step counter is not offspring + width")
    # struct end (offspring + width + u8 step counter, padded to 4) is giftRibbons in vanilla
    end = V["sb1_daycare_step_counter"] + 1
    end += -end % 4
    check(end == 0x309C, f"vanilla Day Care ends at {end:#x}, want giftRibbons 0x309C")


def test_items_count_differs():
    """FRLG+ appends two items to vanilla's 375, so this one key really differs."""
    check(V["items_count"] == 375, f"vanilla items_count {V.get('items_count')}")
    check(F["items_count"] == 377, f"FRLG+ items_count {F.get('items_count')}")


def test_shared_offsets_really_are_shared():
    """Every key both layouts define, except the bag, the daycare (with its offspring width) and items_count,
    must agree.

    Written as "whatever both tables have" rather than a hand-listed set, so a
    new layout key is covered the day it is generated. items_count is skipped
    here because it is asserted to differ in test_items_count_differs.
    """
    differs = {"sb1_daycare", "sb1_daycare_offspring", "sb1_daycare_step_counter",
               "items_count", "daycare_offspring_width"}
    for k in sorted(set(V) & set(F)):
        if k.startswith("sb1_bag") or k.startswith("bag_") or k in differs:
            continue
        check(V[k] == F[k], f"{k} should match: vanilla {V[k]} vs FRLG+ {F[k]}")


def test_vanilla_has_no_frlgplus_only_keys():
    """The FRLG+ pockets and features that do not exist in vanilla, by their real names."""
    for k in ("sb1_bag_tmhm_bits", "bag_tmhm_bytes", "sb1_bag_medicine",
              "sb1_bag_held_items", "sb1_master_trainer_title",
              "sb1_master_trainer_flags"):
        check(k not in V, f"vanilla layout should not define {k}")


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
