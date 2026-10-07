#!/usr/bin/env python3
"""Convert a vanilla Pokémon FireRed/LeafGreen save into an FRLG+ v1.5.1 save.

  python3 migrate.py SAVE                      show the plan, write nothing
  python3 migrate.py SAVE --apply --out NEW    write the converted save
  python3 migrate.py SAVE --apply              convert in place, after a backup

The default is a plan. Nothing is written without --apply.
"""
import argparse
import datetime
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frlgplus  # noqa: E402
import plan as planmod  # noqa: E402
import savewrite  # noqa: E402
import vanilla_frlg  # noqa: E402
import verify  # noqa: E402
from gen3core import SECTOR_SIZE, SECTORS_PER_SLOT, Tables  # noqa: E402
from vanilla_read import BOX_MON_SIZE, PARTY_MON_SIZE, read_vanilla  # noqa: E402

# Two save slots of 14 sectors. A real file is 128 KB (the rest is the Hall of Fame
# and the like), but only the two slots matter to a migration.
MIN_SIZE = 2 * SECTORS_PER_SLOT * SECTOR_SIZE


def detect_source_version(src):
    """FireRed or LeafGreen, from the player's own Pokémon.

    A save does not record which game wrote it, but every Pokémon carries the
    game it was met in. Only the player's own count: a traded one says nothing.
    """
    tid, sid = src["player"]["tid"], src["player"]["sid"]
    votes = {}
    for m in [x for x in src["party"] if x] + [m for row in src["boxes"] for m in row if m]:
        o = m["origin"]
        if (o["ot_tid"], o["ot_sid"]) != (tid, sid) or o["ot_name"] != src["player"]["name"]:
            continue
        votes[o["game"]] = votes.get(o["game"], 0) + 1
    if not votes:
        return None, "no Pokémon in this save was caught by this trainer"
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    # max() would break a tie by insertion order, which is not an inference. Spec 5.4
    # refuses an ambiguous source instead: getting this wrong sets the wrong
    # keyFlags.version and the wrong Deoxys forme, and neither shows up afterwards.
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        tied = ", ".join(f"{name} ({n})" for name, n in ranked
                         if n == ranked[0][1])
        return None, (f"this trainer's Pokémon are split evenly between {tied}, "
                      f"so nothing here says which game wrote the save")
    best = ranked[0][0]
    if best == "FireRed":
        return "fr", f"{votes[best]} of this trainer's Pokémon were met in FireRed"
    if best == "LeafGreen":
        return "lg", f"{votes[best]} of this trainer's Pokémon were met in LeafGreen"
    return None, f"this trainer's Pokémon say {best}, which is neither FireRed nor LeafGreen"


def looks_like_frlgplus(src, plus_tables):
    """FRLG+ writes fields vanilla leaves as filler. If they are set, this save has
    already been converted or was made by FRLG+, and converting again would be wrong.

    Two signals, both in space a vanilla save leaves zero:
      - the Key System word. A conversion always sets expMod to 2, and FRLG+'s own
        new game does the same, so this fires on every save either one has written,
        however empty its bag.
      - the relocated berry pocket.
    The TM Case bitfield is deliberately NOT a signal: FRLG+ puts it at
    sb1_bag_tmhm_bits, which overlaps vanilla's own Key Items slots 18-19, so a
    vanilla save with enough key items would be refused as already converted.
    """
    L = plus_tables.layout
    sb1 = src["sb1"]
    if any(sb1[L["sb1_key_flags"]:L["sb1_key_flags"] + 2]):
        return "the Key System flags are already set"
    if any(sb1[L["sb1_bag_berries"]:L["sb1_bag_berries"] + 4 * L["bag_berries_count"]]):
        return "the relocated berry pocket already holds data"
    return None


def assemble_blocks(p, src, tf, vanilla_L=None):
    """Apply the plan's writes to copies of SaveBlock1 and the PC. Every offset is a
    generated layout key. Pokémon go back to the PHYSICAL slot they came from:
    compacting would hand the game a different party."""
    L = tf.layout
    sb1 = bytearray(src["sb1"])
    for key, data in p["writes"].items():
        if key.startswith("__"):
            continue
        sb1[L[key]:L[key] + len(data)] = data
    # The source's Day Care step counter sits 2 bytes past where FRLG+ keeps it. In
    # FRLG+ that byte is struct padding (the counter is a lone u8 inside a 4-byte
    # aligned struct), so the copied source byte would linger as stale data and make
    # the counter unusable as a layout probe. Clear it, but only if it really is
    # padding, i.e. within the 3 bytes after the FRLG+ counter.
    if vanilla_L is not None:
        pad = vanilla_L["sb1_daycare_step_counter"] - L["sb1_daycare_step_counter"]
        if 0 < pad < 4:
            sb1[vanilla_L["sb1_daycare_step_counter"]] = 0
    leftover = p["writes"]["__leftover_item_slots"]
    start = L["sb1_bag_held_items"] + 4 * L["bag_held_items_count"]
    sb1[start:start + len(leftover)] = leftover
    for i, m in enumerate(p["mons"]["party"]):
        if m is not None:
            o = L["sb1_party"] + PARTY_MON_SIZE * i
            sb1[o:o + len(m)] = m
    pc = bytearray(src["pc"])
    for bx, row in enumerate(p["mons"]["boxes"]):
        for sl, m in enumerate(row):
            if m is None:
                continue
            o = L["pc_boxes"] + (bx * L["in_box_count"] + sl) * BOX_MON_SIZE
            pc[o:o + len(m)] = m
    return bytes(sb1), bytes(pc)


def _write_atomically(path, data):
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save")
    ap.add_argument("--apply", action="store_true", help="write the conversion")
    ap.add_argument("--out", help="write here instead of converting in place")
    ap.add_argument("--source-version", choices=["fr", "lg"],
                    help="which game wrote the save, when it cannot be inferred")
    args = ap.parse_args()

    try:
        with open(args.save, "rb") as f:
            raw = f.read()
    except OSError as e:
        sys.exit(f"Cannot read {args.save}: {e.strerror}.")
    if len(raw) < MIN_SIZE:
        sys.exit(f"{args.save} is {len(raw):,} bytes; a FireRed save is 131,072 "
                 f"(and never under {MIN_SIZE:,}).")

    tv = Tables(layout=vanilla_frlg.LAYOUT)
    tf = Tables(layout=frlgplus.LAYOUT)
    try:
        src = read_vanilla(raw, tv)
    except ValueError:
        # The reader's own message repeats internal wording; say it plainly instead.
        sys.exit(f"{args.save} does not look like a FireRed/LeafGreen save: its save "
                 f"slots do not contain all 14 sections a save needs. Nothing was written.")
    if not src["slot"]["valid"]:
        sys.exit("No save slot passed its checksums, so nothing here can be trusted: "
                 + "; ".join(src["slot"]["problems"][:5]))
    # read_slot's matcher accepts a checksum that matches ANY 4-byte-aligned prefix of
    # a sector, because a hack may use a different number of its bytes. That is right
    # for a reader of an unknown hack and wrong for a writer's input gate: it accepts
    # corruption at a percent-level rate, and the conversion would then be strictly
    # re-checksummed on output and declared verified. Spec 5.5 promises refusal when
    # the SOURCE's checksums do not verify, so gate on the strict matcher too — the
    # same one the written file is held to below.
    bad_src = savewrite.verify_all_checksums(raw, savewrite.section_sizes(tv.layout))
    if bad_src:
        sys.exit(f"{args.save}'s own sector checksums do not reproduce, so it cannot "
                 f"be converted safely: " + "; ".join(bad_src[:5])
                 + ". Nothing was written.")
    if src["saved_game_stat"] != src["slot"]["counter"]:
        sys.exit(f"GAME_STAT_SAVED_GAME ({src['saved_game_stat']}) does not match the "
                 f"sector save counter ({src['slot']['counter']}), so this does not "
                 f"decode as a vanilla FireRed/LeafGreen save.")
    already = looks_like_frlgplus(src, tf)
    if already:
        sys.exit(f"This already looks like an FRLG+ save — {already}. "
                 f"Converting it again would corrupt it.")

    version = args.source_version
    if not version:
        version, why = detect_source_version(src)
        if not version:
            sys.exit(f"Cannot tell whether this is FireRed or LeafGreen: {why}. "
                     f"Re-run with --source-version fr or --source-version lg.")
        print(f"Source game inferred: {'FireRed' if version == 'fr' else 'LeafGreen'} "
              f"({why})")

    p = planmod.build_plan(src, tv.layout, tf.layout, tf, source_version=version)
    print(planmod.render_plan(p, src, tf.layout))
    if not args.apply:
        print("\nPlan only — nothing written. Re-run with --apply to write the file.")
        return

    # Everything below up to the write happens in memory. The converted image must
    # pass its own checksums BEFORE any file is touched, so a failure here leaves
    # the original exactly as it was.
    sb1, pc = assemble_blocks(p, src, tf, tv.layout)
    out_raw = savewrite.apply_writes(raw, src["slot"], tf.layout, sb1, pc)
    bad = savewrite.verify_all_checksums(out_raw, savewrite.section_sizes(tf.layout))
    if bad:
        sys.exit("Refusing to write: the converted image's own checksums do not "
                 "reproduce: " + "; ".join(bad))

    target = args.out or args.save
    backup = None
    try:
        # Anything this run is about to overwrite is backed up first: the input when
        # converting in place, and equally an existing --out, which may be an
        # unrelated save the user mistyped.
        if os.path.exists(target):
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = f"{target}.bak-{stamp}"
            n = 1
            while os.path.exists(backup):
                n += 1
                backup = f"{target}.bak-{stamp}-{n}"
            shutil.copy2(target, backup)
            print(f"\nBackup: {backup}")
        _write_atomically(target, out_raw)
    except OSError as e:
        sys.exit(f"Could not write {target}: {e.strerror or e}. The original save was "
                 f"not modified.")
    print(f"Written: {target}")
    with open(target, "rb") as f:
        leftover_bad = savewrite.verify_all_checksums(
            f.read(), savewrite.section_sizes(tf.layout))
    if leftover_bad:
        sys.exit("Wrote the file but its checksums no longer reproduce: "
                 + "; ".join(leftover_bad))
    print("Re-checked every recognisable sector checksum on the written file: valid.")

    # The checksums only say the file is well formed. This says it agrees with its
    # source. Re-read from disk, so it checks what was actually written.
    with open(target, "rb") as f:
        written = f.read()
    problems = verify.verify_conversion(src, written, tv.layout, tf.layout, tv, tf,
                                        losses=p["losses"])
    if problems:
        print("\nVERIFICATION FAILED: the written file disagrees with its source:",
              file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        recovery = (f"Restore the previous file from the backup, {backup}." if backup
                    else "The source file was not touched.")
        sys.exit(f"{target} was written but does not agree with its source, so do not "
                 f"use it. {recovery}")
    print("Verified against the source: every Pokémon byte for byte outside its boxed "
          "HP/status halfword and met location, items conserved, and money, coins, "
          "flags, vars, stats, Pokédex, play time, mail, the Fame Checker, the "
          "Trainer Tower, the quest log and the Day Care step counter all agree.")


if __name__ == "__main__":
    main()
