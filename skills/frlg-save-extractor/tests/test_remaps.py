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
        rows = vanilla_frlg.remap_rows(DATA_DIR, f"remap_{kind}.txt")
        check(len(R[kind]) == len(rows),
              f"{kind} remap has rows sharing a vanilla value, so the dict collapsed them")
        check(len(targets) == len(set(targets)),
              f"{kind} remap sends two vanilla values to the same FRLG+ value")


def test_mapsec_kanto_is_stable_and_none_moves():
    """Kanto and Sevii sections are unchanged; MAPSEC_NONE is not."""
    rows = {name: (v, f) for v, f, name in vanilla_frlg.remap_rows(DATA_DIR, "remap_mapsec.txt")}
    v, f = rows["MAPSEC_PALLET_TOWN"]
    check(v == f, f"MAPSEC_PALLET_TOWN should not move, got {v:#x} -> {f:#x}")
    v, f = rows["MAPSEC_NONE"]
    check((v, f) == (0xC5, 0xD5), f"MAPSEC_NONE should be 0xC5 -> 0xD5, got {v:#x} -> {f:#x}")


def test_generator_writes_beside_the_vanilla_layout():
    """Run from shared/tools or from the vendored tools/, output lands in the skill's data dir."""
    check(os.path.realpath(generate_remaps.OUT_DIR) == os.path.realpath(os.path.join(DATA_DIR, "vanilla")),
          f"generator output dir {generate_remaps.OUT_DIR} is not {DATA_DIR}/vanilla")


def test_vanilla_mapsec_refuses_a_missing_or_duplicated_json():
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        try:
            generate_remaps.vanilla_mapsec(tmp)
            check(False, "a missing region_map_sections.json was not refused")
        except SystemExit as e:
            check("region_map_sections.json" in str(e), f"refusal does not name the file: {e}")
        d = os.path.join(tmp, "src", "data", "region_map")
        os.makedirs(d)
        with open(os.path.join(d, "region_map_sections.json"), "w") as f:
            json.dump({"map_sections": [{"id": "MAPSEC_A"}, {"id": "MAPSEC_B"}, {"id": "MAPSEC_A"}]}, f)
        try:
            generate_remaps.vanilla_mapsec(tmp)
            check(False, "a duplicate section id was not refused")
        except SystemExit as e:
            check("MAPSEC_A" in str(e), f"refusal does not name the duplicate: {e}")


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

def test_layout_ids_count_empty_slots():
    """Vanilla layouts.json has empty {} slots, and they consume layout IDs.

    LAYOUT_BATTLE_COLOSSEUM_2P is 47 in vanilla because the 11 empty entries before
    it count; numbering that skipped empties would give 36 here, which is its FRLG+ id.
    """
    rows = {name: (v, f) for v, f, name in vanilla_frlg.remap_rows(DATA_DIR, "remap_layouts.txt")}
    check(rows.get("LAYOUT_BATTLE_COLOSSEUM_2P") == (47, 36),
          f"LAYOUT_BATTLE_COLOSSEUM_2P should be 47 -> 36, got {rows.get('LAYOUT_BATTLE_COLOSSEUM_2P')}")
    # A second pin far down the list, past the last of the 18 empties: all 18 have
    # been counted by here, so 342 - 324 == 18. A late placeholder shifts this.
    check(rows.get("LAYOUT_BIRTH_ISLAND_EXTERIOR") == (342, 324),
          f"LAYOUT_BIRTH_ISLAND_EXTERIOR should be 342 -> 324, got {rows.get('LAYOUT_BIRTH_ISLAND_EXTERIOR')}")
    # One assertion that tells the two rules apart: with empties skipped the highest
    # vanilla id could not exceed the number of named layouts.
    highest = max(v for v, _ in rows.values())
    check(highest > len(rows),
          f"highest vanilla layout id {highest} does not exceed the {len(rows)} named layouts")


def test_object_count_changes_are_generated_and_name_their_maps():
    """Spec 7's warning needs this table; a silently empty one disables it."""
    changes = vanilla_frlg.object_count_changes(DATA_DIR)
    check(isinstance(changes, dict), "object_count_changes should return a dict")
    for name, (a, b) in changes.items():
        check(a != b, f"{name} is listed but its counts match ({a} == {b})")
        check(a >= 0 and b >= 0, f"{name} has a negative count")


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
