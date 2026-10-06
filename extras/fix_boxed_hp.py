#!/usr/bin/env python3
"""Write the missing FRLG+ boxed HP for PC Pokémon that have none.

FRLG+ stores a boxed Pokémon's HP and status in the halfword that is padding in
vanilla (struct PokemonSubstruct0: boxHP:10, boxStatus:4, forme:2). The game
writes it on every deposit — with No Free Heals off it stores max HP and no
status (StoreHPAndStatusInBoxMon in src/pokemon_storage_system_data.c).

Some slots in this save have never had it written, so they hold zero. That reads
as "0 HP", and if the No Free Heals or Nuzlocke key is ever switched on,
PopulateBoxHpAndStatusToPartyMon copies that zero straight into HP and the
Pokémon comes out of the box fainted.

This sets those slots to exactly what the game itself would have written:
max HP (clamped to the game's own 714 limit) and no status. It touches nothing
else — not the party, not the other boxes, not the second save slot.

    python3 extras/fix_boxed_hp.py SAVE            # dry run, prints what it would do
    python3 extras/fix_boxed_hp.py SAVE --apply    # back up, then write

Only the active save slot is written; the other slot is left as a rollback point.
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import struct
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_DIR, "skills", "frlg-save-extractor", "scripts"))

from gen3core import (SECTOR_DATA_SIZE, SUB_ORDERS, Tables, decode_pokemon,  # noqa: E402
                      read_slot, u32)

SECTOR_SIZE = 0x1000
SECTORS_PER_SLOT = 14
SAVE_SIGNATURE = 0x08012025
MAX_LEGAL_BOX_HP = 714          # the cap the game itself applies


def section_sizes(tables):
    """The byte count the game checksums for each section, from the save layout."""
    L = tables.layout
    sizes = {0: 0xF24}                                  # sizeof(struct SaveBlock2)
    for i, size in enumerate(_chunks(0x3D68, 4)):       # sizeof(struct SaveBlock1)
        sizes[1 + i] = size
    for i, size in enumerate(_chunks(L["pc_end"], 9)):  # sizeof(struct PokemonStorage)
        sizes[5 + i] = size
    return sizes


def _chunks(total, count):
    return [min(max(0, total - i * SECTOR_DATA_SIZE), SECTOR_DATA_SIZE) for i in range(count)]


def sector_checksum(data, size):
    total = 0
    for i in range(0, size, 4):
        total = (total + struct.unpack_from("<I", data, i)[0]) & 0xFFFFFFFF
    return ((total >> 16) + (total & 0xFFFF)) & 0xFFFF


def verify_all_checksums(raw, sizes):
    """Reproduce every stored sector checksum, so we know our maths matches the game."""
    bad = []
    for slot in range(2):
        for phys in range(SECTORS_PER_SLOT):
            off = (slot * SECTORS_PER_SLOT + phys) * SECTOR_SIZE
            chunk = raw[off:off + SECTOR_SIZE]
            sid, stored, sig, _ = struct.unpack_from("<HHII", chunk, 0xFF4)
            if sig != SAVE_SIGNATURE or sid not in sizes:
                continue
            if sector_checksum(chunk, sizes[sid]) != stored:
                bad.append(f"slot {slot} sector {phys} (section {sid})")
    return bad


def set_box_hp(mon80, box_hp, box_status=0):
    """Return a new 80-byte BoxPokemon with boxHP/boxStatus replaced.

    Decrypts the substructures, edits substructure G's last halfword, recomputes
    the 16-bit checksum over the plaintext and re-encrypts — exactly the order the
    game's own accessors use.
    """
    b = bytearray(mon80)
    pid, otid = u32(b, 0), u32(b, 4)
    key = pid ^ otid
    dec = bytearray(48)
    for i in range(0, 48, 4):
        struct.pack_into("<I", dec, i, u32(b, 32 + i) ^ key)
    g_index = SUB_ORDERS[pid % 24].index("G")
    packed = struct.unpack_from("<H", dec, g_index * 12 + 10)[0]
    forme = (packed >> 14) & 3                      # keep the forme bits untouched
    new = (box_hp & 0x3FF) | ((box_status & 0xF) << 10) | (forme << 14)
    struct.pack_into("<H", dec, g_index * 12 + 10, new)
    struct.pack_into("<H", b, 0x1C, sum(struct.unpack("<24H", dec)) & 0xFFFF)
    for i in range(0, 48, 4):
        struct.pack_into("<I", b, 32 + i, struct.unpack_from("<I", dec, i)[0] ^ key)
    return bytes(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save")
    ap.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    args = ap.parse_args()

    with open(args.save, "rb") as f:
        raw = bytearray(f.read())
    if len(raw) < SECTORS_PER_SLOT * 2 * SECTOR_SIZE:
        sys.exit(f"{args.save} is only {len(raw):,} bytes — expected a 128 KB GBA save.")

    tables = Tables()
    L = tables.layout
    sizes = section_sizes(tables)

    bad = verify_all_checksums(raw, sizes)
    if bad:
        sys.exit("Refusing to touch this save: these sector checksums do not reproduce, so the "
                 "layout assumptions are wrong here — " + "; ".join(bad[:5]))
    print(f"All {2 * SECTORS_PER_SLOT} sector checksums reproduce exactly.")

    slots = [read_slot(raw, 0), read_slot(raw, 1)]
    valid = [(i, s) for i, s in enumerate(slots) if s["valid"]]
    if not valid:
        sys.exit("Neither save slot passes its integrity checks — not touching it.")
    slot_index, slot = max(valid, key=lambda pair: pair[1]["counter"])
    print(f"Active slot: {slot['slot']} (save #{slot['counter']}); "
          f"the other slot is left untouched as a rollback point.")

    phys_of_section = {info["section_id"]: info["physical_sector"]
                       for info in slot["sector_info"] if info.get("signature_ok")}
    missing = [sid for sid in range(5, 14) if sid not in phys_of_section]
    if missing:
        sys.exit(f"PC sections {missing} are missing from the active slot.")

    pc = bytearray(b"".join(slot["sections"][sid] for sid in range(5, 14)))
    key_system_modes = {"iv": "actual", "ev": "actual", "no_free_heals": False}

    changes, skipped = [], []
    for index in range(L["total_boxes"] * L["in_box_count"]):
        off = L["pc_boxes"] + index * 80
        mon = decode_pokemon(pc[off:off + 80], tables,
                             where=f"box {index // L['in_box_count'] + 1} "
                                   f"slot {index % L['in_box_count'] + 1}",
                             calc_modes=key_system_modes)
        if mon is None or mon.get("box_hp_recorded") is not False:
            continue
        label = f"{mon['where']}: {mon['nickname'] or mon['species']} Lv{mon['level']}"
        if mon["checks"]["checksum"] != "ok":
            skipped.append(f"{label} — bad checksum, left alone")
            continue
        if mon["is_egg"]:
            skipped.append(f"{label} — egg, left alone")
            continue
        max_hp = (mon.get("stats") or {}).get("HP")
        if not max_hp:
            skipped.append(f"{label} — could not work out max HP, left alone")
            continue
        new_hp = min(max_hp, MAX_LEGAL_BOX_HP)
        pc[off:off + 80] = set_box_hp(pc[off:off + 80], new_hp)
        changes.append(f"{label} → HP {new_hp}")

    for line in skipped:
        print(f"  skip  {line}")
    print(f"{len(changes)} boxed Pokémon would get their HP written"
          f"{' (' + str(len(skipped)) + ' skipped)' if skipped else ''}.")
    for line in changes[:8]:
        print(f"  set   {line}")
    if len(changes) > 8:
        print(f"  …and {len(changes) - 8} more")
    if not changes:
        print("Nothing to do.")
        return
    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to write the file.")
        return

    # Put the PC buffer back into its sectors and re-checksum each one.
    for i, sid in enumerate(range(5, 14)):
        base = phys_of_section[sid] * SECTOR_SIZE
        chunk = bytearray(raw[base:base + SECTOR_SIZE])
        chunk[:SECTOR_DATA_SIZE] = pc[i * SECTOR_DATA_SIZE:(i + 1) * SECTOR_DATA_SIZE]
        struct.pack_into("<H", chunk, 0xFF6, sector_checksum(chunk, sizes[sid]))
        raw[base:base + SECTOR_SIZE] = chunk

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{args.save}.bak-{stamp}"
    shutil.copy2(args.save, backup)
    with open(args.save, "wb") as f:
        f.write(raw)
    print(f"\nBackup: {backup}")
    print(f"Written: {args.save}")

    leftover = verify_all_checksums(raw, sizes)
    if leftover:
        sys.exit("Wrote the file but its checksums no longer reproduce: " + "; ".join(leftover))
    print(f"Re-checked all {2 * SECTORS_PER_SLOT} sector checksums: still valid.")


if __name__ == "__main__":
    main()
