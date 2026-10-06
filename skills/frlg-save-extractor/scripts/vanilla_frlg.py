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

NAME = "vanilla FR/LG"
LAYOUT = "vanilla/save_layout.txt"
MAX_SPECIES = 412          # SPECIES_EGG; vanilla has no extended dex slots


def decode_box_padding(packed):          # noqa: ARG001 - padding carries nothing
    return {}


def boxed_hp_fields(padding, calc_modes):  # noqa: ARG001
    return {"hp_current": None, "status": "not recorded", "box_hp_recorded": False,
            "status_source": "this game leaves that halfword as padding"}
