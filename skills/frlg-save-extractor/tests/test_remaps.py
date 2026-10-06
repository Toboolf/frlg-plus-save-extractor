#!/usr/bin/env python3
"""The vanilla -> FRLG+ ID remaps. Run: python3 tests/test_remaps.py

FRLG+ inserts three Safari Zone maps into gMapGroup_Dungeons, which shifts
Cerulean Cave and every Pokemon League room. A conversion that skipped this
would drop a player who saved in Cerulean Cave into a Safari Zone map, and
nothing would say so until the save was loaded.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import vanilla_frlg  # noqa: E402
import generate_remaps  # noqa: E402
from gen3core import DATA_DIR  # noqa: E402

FAILS = []
R = vanilla_frlg.remaps(DATA_DIR)


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def test_every_remap_loaded():
    for k in ("maps", "layouts", "mapsec"):
        check(k in R and R[k], f"remap {k} is empty")


def test_cerulean_cave_moves():
    """The regression test for the renumbering finding."""
    rows = {name: (v, f) for v, f, name in vanilla_frlg.remap_rows(DATA_DIR, "remap_maps.txt")}
    v, f = rows["CeruleanCave_1F"]
    check(v != f, "CeruleanCave_1F should change map number between the trees")
    check(R["maps"][v] == f, f"CeruleanCave_1F must remap {v} -> {f}")


def test_pokemon_league_rooms_move():
    rows = {name: (v, f) for v, f, name in vanilla_frlg.remap_rows(DATA_DIR, "remap_maps.txt")}
    for room in ("PokemonLeague_LoreleisRoom", "PokemonLeague_BrunosRoom",
                 "PokemonLeague_AgathasRoom", "PokemonLeague_LancesRoom",
                 "PokemonLeague_ChampionsRoom"):
        check(room in rows, f"{room} missing from the map remap")
        if room in rows:
            v, f = rows[room]
            check(v != f, f"{room} should change map number")


def test_pallet_town_does_not_move():
    rows = {name: (v, f) for v, f, name in vanilla_frlg.remap_rows(DATA_DIR, "remap_maps.txt")}
    v, f = rows["PalletTown"]
    check(v == f, f"PalletTown should not move, got {v} -> {f}")


def test_remap_is_a_function_not_a_collision():
    for kind in ("maps", "layouts", "mapsec"):
        targets = list(R[kind].values())
        check(len(targets) == len(set(targets)),
              f"{kind} remap sends two vanilla values to the same FRLG+ value")


def test_mapsec_kanto_is_stable_and_none_moves():
    """Kanto and Sevii sections are unchanged; MAPSEC_NONE is not."""
    rows = {name: (v, f) for v, f, name in vanilla_frlg.remap_rows(DATA_DIR, "remap_mapsec.txt")}
    v, f = rows["MAPSEC_PALLET_TOWN"]
    check(v == f, f"MAPSEC_PALLET_TOWN should not move, got {v:#x} -> {f:#x}")
    v, f = rows["MAPSEC_NONE"]
    check(v != f, f"MAPSEC_NONE should move, got {v:#x} -> {f:#x}")


def test_generator_writes_beside_the_vanilla_layout():
    """Run from shared/tools or from the vendored tools/, output lands in the skill's data dir."""
    check(os.path.realpath(generate_remaps.OUT_DIR) == os.path.realpath(os.path.join(DATA_DIR, "vanilla")),
          f"generator output dir {generate_remaps.OUT_DIR} is not {DATA_DIR}/vanilla")


def test_matches_vanilla_accepts_only_agreeing_revisions():
    names = ["MAPSEC_A", "MAPSEC_B"]
    ok = {"MAPSEC_A": 0, "MAPSEC_B": 1, "MAPSEC_NONE": 2, "MAPSEC_COUNT": 3}
    check(generate_remaps.matches_vanilla(ok, names), "an agreeing revision was refused")
    renamed = {"MAPSEC_A": 0, "MAPSEC_B_OLD": 1, "MAPSEC_NONE": 2}
    check(not generate_remaps.matches_vanilla(renamed, names), "a renamed section was accepted")
    shifted = {"MAPSEC_A": 1, "MAPSEC_B": 0}
    check(not generate_remaps.matches_vanilla(shifted, names), "a shifted value was accepted")
    extra = {"MAPSEC_A": 0, "MAPSEC_B": 1, "MAPSEC_HOENN": 2}
    check(not generate_remaps.matches_vanilla(extra, names), "an extra section was accepted")


def test_pair_up_refuses_a_vanilla_only_name():
    try:
        generate_remaps.pair_up("maps", {"A": (0, 0), "Gone": (0, 1)}, {"A": (0, 0)},
                                lambda gn: [gn[0], gn[1]])
        check(False, "pair_up dropped a vanilla-only name silently")
    except SystemExit as e:
        check("Gone" in str(e), f"refusal does not name the missing map: {e}")


def test_pair_up_counts_moved_rows():
    rows = generate_remaps.pair_up("maps", {"A": (0, 0), "B": (0, 1)}, {"A": (0, 0), "B": (0, 2)},
                                   lambda gn: [gn[0], gn[1]])
    check(rows == [[0, 0, 0, 0, "A"], [0, 1, 0, 2, "B"]], f"unexpected rows {rows}")


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
