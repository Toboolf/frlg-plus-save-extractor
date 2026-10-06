#!/usr/bin/env python3
"""The profile seam: what gen3core must not know. Run: python3 tests/test_profiles.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import gen3core  # noqa: E402
import frlgplus  # noqa: E402
import vanilla_frlg  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def test_profile_members():
    for p in (frlgplus, vanilla_frlg):
        for member in ("NAME", "LAYOUT", "MAX_SPECIES", "decode_box_padding", "boxed_hp_fields"):
            check(hasattr(p, member), f"{p.__name__} is missing {member}")


def test_frlgplus_decodes_the_padding():
    packed = (294 & 0x3FF) | (9 << 10) | (1 << 14)      # 294 HP, burned, forme 1
    got = frlgplus.decode_box_padding(packed)
    check(got == {"box_hp": 294, "box_status": 9, "forme": 1}, f"FRLG+ padding decode: {got}")
    check(frlgplus.box_status_name(9, 294) == "burned", "FRLG+ box status 9 should be burned")
    check(frlgplus.box_status_name(0, 0) == "fainted", "status 0 with 0 HP should be fainted")
    check(frlgplus.box_status_name(0, 294) == "healthy", "status 0 with HP should be healthy")
    check(frlgplus.box_status_name(3, 294) == "asleep (3 turns)", "status 3 should be asleep")


def test_frlgplus_boxed_hp_fields_are_pinned():
    on = frlgplus.boxed_hp_fields({"box_hp": 100, "box_status": 9, "forme": 0}, {"no_free_heals": True})
    check(on == {"hp_current": 100, "status": "burned", "box_hp_recorded": True,
                 "status_source": "FRLG+ boxed HP/status, live (No Free Heals is on)"}, f"NFH on: {on}")
    off = frlgplus.boxed_hp_fields({"box_hp": 100, "box_status": 0, "forme": 0}, {"no_free_heals": False})
    check(off == {"hp_current": 100, "status": "healthy", "box_hp_recorded": True,
                  "status_source": "FRLG+ boxed HP, written as max HP on deposit (No Free Heals is off)"},
          f"NFH off, HP recorded: {off}")
    none = frlgplus.boxed_hp_fields({"box_hp": 0, "box_status": 0, "forme": 0}, None)
    check(none["box_hp_recorded"] is False and none["hp_current"] is None
          and none["status"] == "not recorded"
          and none["status_source"] == ("no boxed HP stored for this slot — nothing has written it "
                                        "since this Pokémon was put in the box; a save editor or a "
                                        "different ROM build will leave it at zero"),
          f"NFH off, no HP: {none}")
    check(vanilla_frlg.boxed_hp_fields({}, None)["box_hp_recorded"] is False,
          "vanilla boxed HP must be reported as not recorded")


def test_max_species_tracks_the_generated_layout():
    n = gen3core.Tables(layout=frlgplus.LAYOUT).layout["num_species"]
    check(frlgplus.MAX_SPECIES == n + 27, f"MAX_SPECIES {frlgplus.MAX_SPECIES} != num_species {n} + 27")


def test_decode_pokemon_requires_a_profile():
    try:
        gen3core.decode_pokemon(bytes(80), gen3core.Tables())
    except TypeError:
        return
    FAILS.append("decode_pokemon accepted a call with no profile")


def test_vanilla_treats_it_as_padding():
    check(vanilla_frlg.decode_box_padding(0xFFFF) == {},
          "vanilla must report no boxed HP: that halfword is padding in vanilla")


def test_gen3core_has_no_game_semantics():
    src = open(gen3core.__file__, encoding="utf-8").read()
    for token in ("box_hp", "box_status", "BOX_STATUS", "No Free Heals", "Key System"):
        check(token not in src, f"gen3core.py still mentions {token!r}; it belongs in a profile")


def test_tables_layout_is_selectable():
    t = gen3core.Tables(layout="save_layout.txt")
    check(t.layout["sb1_bag_items"] == 0x310, f"FRLG+ bag items at {t.layout['sb1_bag_items']:#x}")


def test_tables_rejects_a_missing_layout():
    """Review Focus 2: a bad layout name must fail here, naming the file."""
    try:
        gen3core.Tables(layout="nope/save_layout.txt")
    except Exception as e:
        check("nope/save_layout.txt" in str(e),
              f"error should name the missing layout file, got: {e}")
        check(not isinstance(e, KeyError),
              f"should not surface as a bare KeyError, got {type(e).__name__}")
    else:
        FAILS.append("Tables accepted a layout file that does not exist")


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
