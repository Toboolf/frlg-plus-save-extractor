#!/usr/bin/env python3
"""The vanilla layout table says what a vanilla save looks like.

Run: python3 tests/test_vanilla_layout.py

These offsets are the ones the migrator will read a vanilla save with, so they
are pinned here against the values the vanilla bag layout forces: four-byte
ItemSlots, pockets in vanilla's order, and nothing where FRLG+ put its bitfield.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from gen3core import Tables  # noqa: E402
import generate_tables  # noqa: E402
from cparse import Consts  # noqa: E402
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


# ---- unit tests: no pokefirered checkout needed, so they always run -------------
REAL_ANNOTATIONS = {"bagPocket_Items": 0x310, "bagPocket_KeyItems": 0x3B8,
                    "bagPocket_PokeBalls": 0x430, "bagPocket_TMHM": 0x464,
                    "bagPocket_Berries": 0x54C, "seen1": 0x5F8}
REAL_COUNTS = {"items": 42, "key_items": 30, "poke_balls": 13, "tmhm": 58, "berries": 43}


def test_unit_vanilla_bag_walk_happy_path():
    got = generate_tables._walk_vanilla_bag(REAL_ANNOTATIONS.__getitem__, REAL_COUNTS, 0x310)
    want = {"sb1_bag_items": 0x310, "bag_items_count": 42,
            "sb1_bag_key_items": 0x3B8, "bag_key_items_count": 30,
            "sb1_bag_poke_balls": 0x430, "bag_poke_balls_count": 13,
            "sb1_bag_tmhm": 0x464, "bag_tmhm_count": 58,
            "sb1_bag_berries": 0x54C, "bag_berries_count": 43}
    check(got == want, f"walk fragment differs: {got}")


def test_unit_vanilla_bag_walk_refuses_swapped_counts():
    """Items and KeyItems swapped: the total is unchanged, so seen1 still matches,
    but key_items lands at 0x388 instead of its annotated 0x3B8."""
    swapped = dict(REAL_COUNTS, items=30, key_items=42)
    try:
        generate_tables._walk_vanilla_bag(REAL_ANNOTATIONS.__getitem__, swapped, 0x310)
    except SystemExit as e:
        msg = str(e)
        print(f"  swapped-counts refusal: {msg}")
        check("key_items" in msg and "0x388" in msg and "0x3b8" in msg,
              f"refusal should name the pocket and both offsets: {msg}")
    else:
        check(False, "swapped counts should have been refused")


def test_unit_vanilla_bag_walk_refuses_a_wrong_total():
    """Every pocket matches its annotation but the walk ends short of seen1."""
    short = dict(REAL_COUNTS, berries=42)
    ann = dict(REAL_ANNOTATIONS)
    try:
        generate_tables._walk_vanilla_bag(ann.__getitem__, short, 0x310)
    except SystemExit as e:
        check("seen1" in str(e), f"should refuse on seen1: {e}")
    else:
        check(False, "a walk that misses seen1 should have been refused")


def test_unit_required_check_names_the_missing_file():
    """A temp tree holding all but items.h is refused by name, not with a KeyError."""
    rel_missing = "include/constants/items.h"
    with tempfile.TemporaryDirectory() as tmp:
        for rel in generate_tables.REQUIRED["vanilla"]:
            if rel == rel_missing:
                continue
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            open(os.path.join(tmp, rel), "w").close()
        argv = sys.argv
        sys.argv = ["generate_tables.py", "--game", "vanilla", "--repo", tmp]
        try:
            generate_tables.main()
        except SystemExit as e:
            msg = str(e)
            print(f"  missing-file refusal: {msg}")
            check(msg.endswith(f"is missing {rel_missing}, which --game vanilla needs"),
                  f"wrong refusal: {msg}")
        except BaseException as e:
            check(False, f"raised {type(e).__name__}: {e}")
        else:
            check(False, "generator ran despite the missing file")
        finally:
            sys.argv = argv


# The generator tests below need a real pret/pokefirered checkout to copy from.
# One is not shipped, so they are skipped (and say so) where it is absent.
VANILLA_SRC = os.path.expanduser(os.environ.get("POKEFIRED_REPO", "~/projects/pokefirered"))
SKIPPED = []


def _copy_vanilla_tree(dst, omit=()):
    """Copy just the headers --game vanilla needs into dst, minus anything in omit."""
    for rel in generate_tables.REQUIRED["vanilla"]:
        if rel in omit:
            continue
        os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
        shutil.copy(os.path.join(VANILLA_SRC, rel), os.path.join(dst, rel))


def _walk(repo):
    consts = Consts()
    for rel in generate_tables.REQUIRED["vanilla"]:
        if not rel.startswith("include/constants/"):
            continue
        consts.load_defines(os.path.join(repo, rel))
    return generate_tables.compute_layout(repo, consts, "vanilla")


def test_vanilla_walk_refuses_swapped_pocket_counts():
    """Swapping two pocket counts keeps the total, so the seen1 landing check still
    passes; only the per-pocket annotation check catches the wrong middle offsets."""
    if not os.path.isfile(os.path.join(VANILLA_SRC, "include/global.h")):
        SKIPPED.append("test_vanilla_walk_refuses_swapped_pocket_counts")
        return
    with tempfile.TemporaryDirectory() as tmp:
        _copy_vanilla_tree(tmp)
        check(_walk(tmp)["sb1_bag_key_items"] == 0x3B8, "undoctored tree should walk cleanly")
        path = os.path.join(tmp, "include/constants/global.h")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        items = int(re.search(r"#define BAG_ITEMS_COUNT\s+(\d+)", text).group(1))
        keys = int(re.search(r"#define BAG_KEYITEMS_COUNT\s+(\d+)", text).group(1))
        text = re.sub(r"(#define BAG_ITEMS_COUNT\s+)\d+", lambda m: m.group(1) + str(keys), text)
        text = re.sub(r"(#define BAG_KEYITEMS_COUNT\s+)\d+", lambda m: m.group(1) + str(items), text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            _walk(tmp)
        except SystemExit as e:
            check("annotated at" in str(e), f"refused, but not on the pocket annotation: {e}")
        else:
            check(False, "swapped pocket counts should have been refused")


def test_vanilla_run_names_a_missing_file():
    """Every header the vanilla layout reads is refused by name, not with a KeyError."""
    if not os.path.isfile(os.path.join(VANILLA_SRC, "include/global.h")):
        SKIPPED.append("test_vanilla_run_names_a_missing_file")
        return
    for rel in generate_tables.REQUIRED["vanilla"]:
        with tempfile.TemporaryDirectory() as tmp:
            _copy_vanilla_tree(tmp, omit=(rel,))
            argv = sys.argv
            sys.argv = ["generate_tables.py", "--game", "vanilla", "--repo", tmp]
            try:
                generate_tables.main()
            except SystemExit as e:
                msg = str(e)
                check(msg.endswith(f"is missing {rel}, which --game vanilla needs"),
                      f"{rel}: wrong refusal: {msg}")
            except BaseException as e:  # a KeyError or traceback is the bug being tested
                check(False, f"{rel}: raised {type(e).__name__}: {e}")
            else:
                check(False, f"{rel}: generator ran despite the missing file")
            finally:
                sys.argv = argv


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    for f in FAILS:
        print(f"FAIL: {f}")
    for n in SKIPPED:
        print(f"SKIPPED (no pokefirered checkout at {VANILLA_SRC}): {n}")
    print(f"\n{len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
