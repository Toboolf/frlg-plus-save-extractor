"""Vanilla FireRed / LeafGreen profile.

Only what differs from FRLG+ lives here. Item IDs, species IDs, move IDs, flag
and var numbers and game-stat indices are identical between the two trees (see
the spec's evidence section), so this profile reuses the FRLG+ name tables and
supplies only its own save layout.

The last halfword of Pokémon substructure 0 is genuinely padding in vanilla, so
decode_box_padding reports nothing: a zero there means "never written", not
"fainted".
"""
from __future__ import annotations

import os

NAME = "vanilla FR/LG"
LAYOUT = "vanilla/save_layout.txt"
MAX_SPECIES = 439          # NUM_SPECIES (412) plus the 27 Unown letter slots, same as FRLG+:
                           # vanilla defines SPECIES_UNOWN_B..QMARK as NUM_SPECIES + 1..27 too


def decode_box_padding(packed):          # noqa: ARG001 - padding carries nothing
    return {}


def boxed_hp_fields(padding, calc_modes):  # noqa: ARG001
    return {"hp_current": None, "status": "not recorded", "box_hp_recorded": False,
            "status_source": "this game leaves that halfword as padding"}


def remap_rows(data_dir, filename):
    """(vanilla value, FRLG+ value, name) per row, with tuples for the two-column maps."""
    path = os.path.join(data_dir, "vanilla", filename)
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("|")
            if filename == "remap_maps.txt":
                out.append(((int(parts[0]), int(parts[1])),
                            (int(parts[2]), int(parts[3])), parts[4]))
            else:
                out.append((int(parts[0], 0), int(parts[1], 0), parts[2]))
    return out


def remaps(data_dir):
    """Everything a vanilla ID has to be translated through, keyed by what it is."""
    return {
        "maps": {v: f for v, f, _ in remap_rows(data_dir, "remap_maps.txt")},
        "layouts": {v: f for v, f, _ in remap_rows(data_dir, "remap_layouts.txt")},
        "mapsec": {v: f for v, f, _ in remap_rows(data_dir, "remap_mapsec.txt")},
    }


def object_count_changes(data_dir):
    """{map name: (vanilla count, FRLG+ count)} for maps whose object events moved."""
    return {name: (a, b) for a, b, name in
            remap_rows(data_dir, "remap_objcount.txt")}
