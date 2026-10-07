#!/usr/bin/env python3
"""Generate the vanilla -> FRLG+ ID remap tables.

    python3 shared/tools/generate_remaps.py --vanilla ~/projects/pokefirered \
                                            --frlgplus ~/projects/FRLG-Plus \
                                            [--skill <package under skills/>]

FRLG+ keeps the save block from shifting, but it does insert maps and layouts,
which renumbers the IDs a save stores. Matching by name across the two trees
gives the mapping; nothing here is hand-written.

Map sections are a special case: the vanilla decomp generates
include/constants/region_map_sections.h at build time, so the vanilla side is
built from vanilla's own src/data/region_map/region_map_sections.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)
# generate_tables already resolves the data directory for both the shared/ and the
# vendored location; reuse it rather than carrying a second resolver.
from generate_tables import DATA_DIR, _SKILLS_ROOT  # noqa: E402

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
    # Empty `{}` entries consume an ID: the decomp's tools/mapjson/mapjson.cpp,
    # generate_layouts_constants_text, does `i++` outside the `if` that skips the
    # #define, and generate_layouts_table_text emits NULL for an empty entry. So the
    # ID is position + offset, and skipping empties would shift every later ID.
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


def vanilla_mapsec(vanilla_repo):
    """Vanilla map sections, straight from the vanilla tree.

    The decomp generates include/constants/region_map_sections.h at build time, so
    a checkout has no header to read — but it does not need one. The template
    src/data/region_map/region_map_sections.constants.json.txt emits
    `enum { <every map_section.id>, MAPSEC_NONE, MAPSEC_COUNT }`, so the ids are
    the json's order and MAPSEC_NONE is the count.
    """
    path = os.path.join(vanilla_repo, "src", "data", "region_map",
                        "region_map_sections.json")
    if not os.path.isfile(path):
        raise SystemExit(f"{vanilla_repo} is missing src/data/region_map/region_map_sections.json, "
                         f"which the vanilla map-section remap needs")
    with open(path, encoding="utf-8") as f:
        sections = json.load(f)["map_sections"]
    seen = set()
    for entry in sections:
        if entry["id"] in seen:
            raise SystemExit(f"{path} lists {entry['id']} twice, which would shift every later id")
        seen.add(entry["id"])
    out = {entry["id"]: i for i, entry in enumerate(sections)}
    out["MAPSEC_NONE"] = len(sections)
    return out, "src/data/region_map/region_map_sections.json"


def object_counts(repo):
    """{map name: how many object events its map.json defines}."""
    out = {}
    maps_dir = os.path.join(repo, "data", "maps")
    for name in sorted(os.listdir(maps_dir)):
        path = os.path.join(maps_dir, name, "map.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            out[name] = len(json.load(f).get("object_events") or [])
    return out


def write(path, header, rows):
    with open(path, "w", encoding="utf-8") as f:
        for line in header:
            f.write(f"# {line}\n")
        for row in rows:
            f.write("|".join(str(c) for c in row) + "\n")
    print(f"  {os.path.relpath(path, os.path.dirname(os.path.dirname(OUT_DIR)))}: {len(rows)} rows")


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
    ap.add_argument("--skill", default="frlg-save-extractor",
                    help="which package under skills/ to write the remap tables into")
    args = ap.parse_args()
    global OUT_DIR
    if args.skill != "frlg-save-extractor":
        if _SKILLS_ROOT is None:
            raise SystemExit("--skill needs the source repo's shared/tools/ copy of this script")
        OUT_DIR = os.path.join(_SKILLS_ROOT, args.skill, "scripts", "data", "vanilla")
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

    van_sec, base = vanilla_mapsec(van_repo)
    with open(os.path.join(plus_repo, "include", "constants", "region_map_sections.h"),
              encoding="utf-8") as f:
        plus_sec = mapsec_ids_from_text(f.read())
    plus_sec.pop("MAPSEC_COUNT", None)      # a count, not a section
    rows = pair_up("mapsec", van_sec, plus_sec, lambda i: [i])
    write(os.path.join(OUT_DIR, "remap_mapsec.txt"),
          ["vanilla value|FRLG+ value|constant",
           "Generated from include/constants/region_map_sections.h. The vanilla side comes",
           f"from {base}: the names in order are values 0..N-1 and MAPSEC_NONE is N,",
           "because the vanilla decomp generates that header at build time."],
          rows)

    van_obj, plus_obj = object_counts(van_repo), object_counts(plus_repo)
    rows = [[van_obj[n], plus_obj[n], n] for n in sorted(van_obj)
            if n in plus_obj and van_obj[n] != plus_obj[n]]
    write(os.path.join(OUT_DIR, "remap_objcount.txt"),
          ["vanilla object events|FRLG+ object events|map name",
           "Generated from each map's data/maps/<name>/map.json in both trees.",
           "Only maps where the two disagree are listed: a save made on one of",
           "these carries object state for a layout FRLG+ changed (spec 7)."], rows)
    print(f"  object counts: {len(rows)} map(s) differ")
    print("done")


if __name__ == "__main__":
    sys.exit(main())
