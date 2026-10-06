#!/usr/bin/env python3
"""Generate the vanilla -> FRLG+ ID remap tables.

    python3 shared/tools/generate_remaps.py --vanilla ~/projects/pokefirered \
                                            --frlgplus ~/projects/FRLG-Plus

FRLG+ keeps the save block from shifting, but it does insert maps and layouts,
which renumbers the IDs a save stores. Matching by name across the two trees
gives the mapping; nothing here is hand-written.

Map sections are a special case: the vanilla decomp generates
include/constants/region_map_sections.h at build time, so the vanilla side is
read from the pre-hack revision of that file inside the FRLG+ history, and the
revision is recorded in the table header.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)
# generate_tables already resolves the data directory for both the shared/ and the
# vendored location; reuse it rather than carrying a second resolver.
from generate_tables import DATA_DIR  # noqa: E402

OUT_DIR = os.path.join(DATA_DIR, "vanilla")


def map_ids(repo):
    """{map name: (group index, number within the group)} from data/maps/map_groups.json."""
    with open(os.path.join(repo, "data", "maps", "map_groups.json"), encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    for gi, group in enumerate(d["group_order"]):
        for ni, name in enumerate(d[group]):
            out[name] = (gi, ni)
    return out


def layout_order(repo):
    """Layout names in the order layouts.json lists them, which is what numbers them."""
    with open(os.path.join(repo, "data", "layouts", "layouts.json"), encoding="utf-8") as f:
        d = json.load(f)
    return [e.get("id") or e.get("name") for e in d["layouts"]]


def layout_ids(repo, offset):
    return {name: i + offset for i, name in enumerate(layout_order(repo)) if name}


def layout_id_offset(frlgplus_repo):
    """Solve for the offset between json position and LAYOUT_ id, don't assume it.

    include/constants/layouts.h is auto-generated from layouts.json and is the
    authoritative numbering (it reads `#define LAYOUT_PALLET_TOWN_PLAYERS_HOUSE_1F 1`),
    but the vanilla decomp generates that header at build time, so the vanilla
    side has to be numbered by position. One offset must explain every FRLG+
    entry, or the assumption is wrong and this exits.
    """
    path = os.path.join(frlgplus_repo, "include", "constants", "layouts.h")
    with open(path, encoding="utf-8") as f:
        ids = {m.group(1): int(m.group(2), 0) for m in
               re.finditer(r"^#define\s+(LAYOUT_[A-Z0-9_]+)\s+(\d+|0x[0-9A-Fa-f]+)",
                           f.read(), re.M)}
    offsets = {ids[name] - i for i, name in enumerate(layout_order(frlgplus_repo))
               if name in ids}
    if len(offsets) != 1:
        raise SystemExit(
            f"layouts.json position does not map to LAYOUT_ ids by one offset "
            f"(candidates: {sorted(offsets)[:5]}) — the numbering convention changed")
    return offsets.pop()


def mapsec_ids_from_text(text):
    return {m.group(1): int(m.group(2), 0)
            for m in re.finditer(r"^#define\s+(MAPSEC_[A-Z0-9_]+)\s+(0x[0-9A-Fa-f]+|\d+)",
                                 text, re.M)}


def vanilla_section_names(vanilla_repo):
    """Section names in the order the vanilla decomp numbers them (index == value)."""
    path = os.path.join(vanilla_repo, "src", "data", "region_map", "region_map_sections.json")
    with open(path, encoding="utf-8") as f:
        return [e["id"] for e in json.load(f)["map_sections"]]


def matches_vanilla(consts, names):
    """True if a header revision numbers every vanilla section as vanilla does.

    MAPSEC_NONE and MAPSEC_COUNT follow the real sections and are the only names
    a revision may carry beyond the vanilla list.
    """
    if any(consts.get(n) != i for i, n in enumerate(names)):
        return False
    return set(consts) - set(names) <= {"MAPSEC_NONE", "MAPSEC_COUNT"}


def vanilla_mapsec(frlgplus_repo, vanilla_repo):
    """The pre-hack revision of the map-section header, from the FRLG+ history.

    Not simply the oldest revision: the oldest one predates a pret rename
    (MAPSEC_ROUTE_4_FLYDUP became MAPSEC_ROUTE_4_POKECENTER), so it names two
    sections that vanilla at the checked-out revision does not. The right one is
    the oldest revision that agrees with vanilla's own section list on every
    name and value, and none is accepted without that agreement.
    """
    rel = "include/constants/region_map_sections.h"
    revs = subprocess.run(["git", "-C", frlgplus_repo, "log", "--format=%H", "--", rel],
                          capture_output=True, text=True, check=True).stdout.split()
    if not revs:
        raise SystemExit(f"no history for {rel} in {frlgplus_repo}")
    names = vanilla_section_names(vanilla_repo)
    for rev in reversed(revs):
        text = subprocess.run(["git", "-C", frlgplus_repo, "show", f"{rev}:{rel}"],
                              capture_output=True, text=True, check=True).stdout
        consts = mapsec_ids_from_text(text)
        if matches_vanilla(consts, names):
            consts.pop("MAPSEC_COUNT", None)
            return consts, rev
    raise SystemExit(f"no revision of {rel} in the FRLG+ history agrees with vanilla's "
                     f"region_map_sections.json, so the vanilla side cannot be established")


def write(path, header, rows):
    with open(path, "w", encoding="utf-8") as f:
        for line in header:
            f.write(f"# {line}\n")
        for row in rows:
            f.write("|".join(str(c) for c in row) + "\n")
    print(f"  {os.path.relpath(path, os.path.dirname(DATA_DIR))}: {len(rows)} rows")


def pair_up(kind, van, plus, fmt):
    """Rows for names in both trees, and a loud report of names in only one."""
    only_vanilla = sorted(set(van) - set(plus))
    only_plus = sorted(set(plus) - set(van))
    if only_vanilla:
        raise SystemExit(
            f"{kind}: {len(only_vanilla)} name(s) exist in vanilla but not in FRLG+ "
            f"({', '.join(only_vanilla[:5])}) — a save could point at one, so the remap "
            f"cannot be generated until this is handled deliberately")
    rows = []
    moved = 0
    for name in sorted(van):
        v, p = van[name], plus[name]
        rows.append(fmt(v) + fmt(p) + [name])
        if v != p:
            moved += 1
    print(f"  {kind}: {len(rows)} shared, {moved} moved, {len(only_plus)} new in FRLG+")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vanilla", required=True)
    ap.add_argument("--frlgplus", required=True)
    args = ap.parse_args()
    van_repo, plus_repo = os.path.abspath(args.vanilla), os.path.abspath(args.frlgplus)
    os.makedirs(OUT_DIR, exist_ok=True)

    rows = pair_up("maps", map_ids(van_repo), map_ids(plus_repo), lambda gn: [gn[0], gn[1]])
    write(os.path.join(OUT_DIR, "remap_maps.txt"),
          ["vanilla group|vanilla num|FRLG+ group|FRLG+ num|map name",
           "Generated by shared/tools/generate_remaps.py from data/maps/map_groups.json",
           "in both trees. Matched by map name."], rows)

    offset = layout_id_offset(plus_repo)
    print(f"  layout id offset from json position: {offset}")
    rows = pair_up("layouts", layout_ids(van_repo, offset), layout_ids(plus_repo, offset),
                   lambda i: [i])
    write(os.path.join(OUT_DIR, "remap_layouts.txt"),
          ["vanilla id|FRLG+ id|layout name",
           "Generated from data/layouts/layouts.json in both trees, matched by layout id.",
           f"Position-to-id offset {offset}, solved against FRLG+'s generated layouts.h."], rows)

    van_sec, base = vanilla_mapsec(plus_repo, van_repo)
    with open(os.path.join(plus_repo, "include", "constants", "region_map_sections.h"),
              encoding="utf-8") as f:
        plus_sec = mapsec_ids_from_text(f.read())
    plus_sec.pop("MAPSEC_COUNT", None)      # a count, not a section
    rows = pair_up("mapsec", van_sec, plus_sec, lambda i: [i])
    write(os.path.join(OUT_DIR, "remap_mapsec.txt"),
          ["vanilla value|FRLG+ value|constant",
           "Generated from include/constants/region_map_sections.h. The vanilla side comes",
           f"from the pre-hack revision {base} in the FRLG+ history, because the vanilla",
           "decomp generates that header at build time. That revision is the oldest one whose",
           "names and values all match vanilla's src/data/region_map/region_map_sections.json."],
          rows)
    print("done")


if __name__ == "__main__":
    sys.exit(main())
