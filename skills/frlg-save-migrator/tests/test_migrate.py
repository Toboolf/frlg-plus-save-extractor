#!/usr/bin/env python3
"""The migrator end to end, on synthetic saves. Run: python3 tests/test_migrate.py

The FRLG+ reader is the writer's test harness (spec 6.2): convert, then read the
result back with frlgplus.extract and compare it with what went in.
"""
import io
import os
import contextlib
import subprocess
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
import frlgplus  # noqa: E402
import migrate  # noqa: E402
import plan as planmod  # noqa: E402
import savewrite  # noqa: E402
import vanilla_frlg  # noqa: E402
from gen3core import SECTOR_SIZE, SECTORS_PER_SLOT, Tables  # noqa: E402
from vanilla_read import read_vanilla  # noqa: E402

TV = Tables(layout=vanilla_frlg.LAYOUT)
TF = Tables(layout=frlgplus.LAYOUT)
MIGRATE = os.path.join(SCRIPTS, "migrate.py")
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def item(name):
    return next(k for k, r in TF.items.items() if r["name"] == name)


def run(*args):
    return subprocess.run([sys.executable, MIGRATE, *args], capture_output=True, text=True)


def write_temp(raw, name="in.srm"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(raw)
    return p


def convert(raw, *extra):
    """Convert `raw` to a new file; return (CompletedProcess, extracted FRLG+ dict)."""
    src_path = write_temp(raw)
    out_path = os.path.join(os.path.dirname(src_path), "converted.srm")
    r = run(src_path, "--apply", "--out", out_path, "--source-version", "fr", *extra)
    if r.returncode != 0:
        return r, None
    with open(out_path, "rb") as f:
        return r, frlgplus.extract(f.read(), TF, source_name="converted.srm")


def pocket(got, name):
    return next(p for p in got["items"]["pockets"] if p["name"] == name)


def empty_boxes():
    return [[None] * 30 for _ in range(14)]


def test_the_extractor_reads_what_the_migrator_wrote():
    """Spec 6.2: the reader is the writer's test harness."""
    potion, ball = item("Potion"), item("Poké Ball")
    raw = fixtures.build_vanilla_save(
        money=54321, coins=77, save_counter=9,
        party=[fixtures.mon("Bulbasaur", 7)],
        boxes=[[fixtures.mon("Pikachu", 25, party=False, met_level=3)] + [None] * 29]
              + [[None] * 30 for _ in range(13)],
        items=[(potion, 4)], poke_balls=[(ball, 9)])
    r, got = convert(raw)
    check(r.returncode == 0, f"conversion failed: {r.stderr[-500:]}")
    if got is None:
        return
    check(got["save_health"]["valid"], f"result invalid: {got['save_health']['problems']}")
    check(got["trainer"]["money"] == 54321, f"money {got['trainer']['money']}")
    check(got["trainer"]["coins"] == 77, f"coins {got['trainer']['coins']}")
    check(got["key_system"]["exp_modifier"] == "1x",
          f"expMod should read 1x, got {got['key_system']['exp_modifier']}")
    check(got["key_system"]["version"] == "FireRed", f"version {got['key_system']['version']}")
    boxed = [m for b in got["boxes"]["boxes"] for m in b["pokemon"]]
    check(len(boxed) == 1, f"{len(boxed)} boxed Pokémon in the result")
    if boxed:
        check(boxed[0]["box_hp_recorded"] is True, "boxHP should now be recorded")
        check(boxed[0]["hp_current"] == boxed[0]["stats"]["HP"],
              f"boxHP {boxed[0]['hp_current']} should equal max HP {boxed[0]['stats']['HP']}")
    # The two source Pokémon differ in met level, so the SOURCE carries no such hint;
    # anything the hint finds in the result would have been put there by the migrator.
    check("multiple_starter_origins" not in got["hints"],
          "a conversion invents no Pokémon, so it must not look like a transplant")
    check(got["items"]["pocket_coherence_ok"], "the rebuilt bag should be coherent")
    check(got["items"].get("unknown_item_ids") == [], "no unknown item ids expected")


def test_the_bag_lands_in_the_pockets_its_data_assigns_with_real_quantities():
    """The rules work in plaintext; the writer must encrypt. This fails if a plaintext
    quantity reaches the save (FRLG+ would read it as qty ^ key)."""
    key_item = next(iter(TF.key_item_indices.values()))     # stored index -> item id
    raw = fixtures.build_vanilla_save(
        party=[fixtures.mon("Bulbasaur", 7)],
        items=[(item("Potion"), 4), (item("Escape Rope"), 2), (item("Leftovers"), 1)],
        key_items=[(key_item, 1)],
        poke_balls=[(item("Poké Ball"), 9)],
        tmhm=[(item("TM01"), 1), (item("TM05"), 3)],
        berries=[(item("Oran Berry"), 12)])
    r, got = convert(raw)
    check(r.returncode == 0, f"conversion failed: {r.stderr[-500:]}")
    if got is None:
        return

    def held(name):
        return sorted((i["name"], i["qty"]) for i in pocket(got, name)["items"])
    check(held("Medicine") == [("Potion", 4)], f"Medicine {held('Medicine')}")
    check(held("Items") == [("Escape Rope", 2)], f"Items {held('Items')}")
    check(held("Held Items") == [("Leftovers", 1)], f"Held Items {held('Held Items')}")
    check(held("Poké Balls") == [("Poké Ball", 9)], f"Poké Balls {held('Poké Balls')}")
    check(held("Berry Pouch") == [("Oran Berry", 12)], f"Berry Pouch {held('Berry Pouch')}")
    tms = sorted(i["name"] for i in pocket(got, "TM Case")["items"])
    check(tms == ["TM01", "TM05"], f"TM Case {tms}")
    keys = [i["name"] for i in pocket(got, "Key Items")["items"]]
    check(keys == [TF.item_name(key_item)], f"Key Items {keys}")
    check(got["items"]["pocket_coherence_ok"], "bag should read as coherent")
    check("TM05" in r.stdout and "had 3" in r.stdout,
          f"the plan should report the two TM05 copies the case cannot hold: {r.stdout[-600:]}")


def test_every_pokemon_returns_to_the_physical_slot_it_came_from():
    """A hole in the party and gaps in a box. Compacting would shift later Pokémon
    into the wrong slot, which no checksum can detect."""
    boxes = empty_boxes()
    boxes[2][3] = fixtures.mon("Pikachu", 25, party=False, pid=0x01010101)
    boxes[2][9] = fixtures.mon("Squirtle", 10, party=False, pid=0x02020202)
    party = [fixtures.mon("Bulbasaur", 7, pid=0x0A0A0A0A), bytes(100),
             fixtures.mon("Charmander", 9, pid=0x0B0B0B0B)]
    r, got = convert(fixtures.build_vanilla_save(party=party, boxes=boxes))
    check(r.returncode == 0, f"conversion failed: {r.stderr[-500:]}")
    if got is None:
        return
    check([m["where"] for m in got["party"]] == ["party 1", "party 3"],
          f"party slots {[m['where'] for m in got['party']]}")
    check([m["species"] for m in got["party"]] == ["Bulbasaur", "Charmander"],
          f"party {[m['species'] for m in got['party']]}")
    box3 = got["boxes"]["boxes"][2]["pokemon"]
    check([(m["where"], m["species"]) for m in box3]
          == [("box 3 slot 4", "Pikachu"), ("box 3 slot 10", "Squirtle")],
          f"box 3 {[(m['where'], m['species']) for m in box3]}")


def test_the_party_is_written_at_the_generated_offset():
    """Byte-level: slot i of the output party equals convert_mons' slot i."""
    party = [fixtures.mon("Bulbasaur", 7, pid=0x0A0A0A0A),
             fixtures.mon("Charmander", 9, pid=0x0B0B0B0B)]
    raw = fixtures.build_vanilla_save(party=party)
    src = read_vanilla(raw, TV)
    p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version="fr")
    sb1, _pc = migrate.assemble_blocks(p, src, TF)
    for i, want in enumerate(p["mons"]["party"]):
        o = TF.layout["sb1_party"] + 100 * i
        check(sb1[o:o + 100] == want, f"party slot {i} not at its generated offset")


def test_an_egg_and_a_damaged_pokemon_keep_their_original_bytes():
    egg = fixtures.mon("Pichu", 1, is_egg=True, pid=0x0C0C0C0C) if "Pichu" in TV.by_name \
        else fixtures.mon("Bulbasaur", 1, is_egg=True, pid=0x0C0C0C0C)
    bad = fixtures.mon("Squirtle", 5, bad_checksum=True, pid=0x0D0D0D0D)
    raw = fixtures.build_vanilla_save(party=[egg, bad, fixtures.mon("Bulbasaur", 5)])
    src = read_vanilla(raw, TV)
    p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version="fr")
    check(p["mons"]["party"][0] == src["party_raw"][0], "an egg must be left byte-identical")
    check(p["mons"]["party"][1] == src["party_raw"][1], "a bad-checksum mon must be left alone")
    check(p["mons"]["party"][2] != src["party_raw"][2], "a normal mon should be rewritten")
    check(len(p["skipped"]) == 2, f"skipped {p['skipped']}")


def test_the_plan_names_all_four_kinds_of_loss_distinctly():
    plan = {"writes": {}, "mons": {}, "skipped": [], "decisions": [], "notes": [],
            "source_version": "fr",
            "losses": [{"kind": "tm_quantity", "item": "TM05", "detail": "had 3"},
                       {"kind": "pocket_full", "item": "Potion", "detail": "did not fit"},
                       {"kind": "unmapped_key_item", "item": "Some Key", "detail": "no index"},
                       {"kind": "unknown_item", "item": "Odd Thing", "detail": "no pocket"}]}
    src = read_vanilla(fixtures.build_vanilla_save(), TV)
    text = planmod.render_plan(plan, src, TF.layout)
    for kind, heading in planmod.LOSS_HEADINGS.items():
        check(heading in text, f"{kind}: heading missing from the plan")
    for name in ("TM05", "Potion", "Some Key", "Odd Thing"):
        check(name in text, f"{name} missing from the plan")
    unknown = text.split(planmod.LOSS_HEADINGS["unknown_item"])[1]
    check("full" not in unknown.split("\n")[0].lower(),
          "an unknown item must not be described as a capacity problem")
    check("nothing written" not in text, "render_plan must not claim a plan-only run")


def test_an_in_place_run_backs_up_first_and_the_backup_is_the_original():
    raw = fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)])
    p = write_temp(raw)
    r = run(p, "--apply")
    check(r.returncode == 0, f"in-place failed: {r.stderr[-400:]}")
    names = sorted(os.listdir(os.path.dirname(p)))
    backups = [n for n in names if n.startswith("in.srm.bak-")]
    check(len(backups) == 1, f"expected one backup, got {names}")
    check(not [n for n in names if ".tmp-" in n], f"temp file left behind: {names}")
    if backups:
        with open(os.path.join(os.path.dirname(p), backups[0]), "rb") as f:
            check(f.read() == raw, "the backup is not the original bytes")
    with open(p, "rb") as f:
        out = f.read()
    check(out != raw, "the file was not converted")
    check(len(out) == len(raw), "the file's size changed")
    check(savewrite.verify_all_checksums(out, savewrite.section_sizes(TF.layout)) == [],
          "converted file's checksums do not verify")


def test_a_failed_verification_leaves_the_original_untouched_and_makes_no_backup():
    """The converted image must pass its own checksums BEFORE anything touches disk.
    Force verification to fail and show the input is bit-identical and nothing else
    was created: no backup, no temp file, no output."""
    raw = fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)])
    for extra in ([], ["--out", "NEW"]):
        p = write_temp(raw)
        d = os.path.dirname(p)
        argv = ["migrate.py", p, "--apply", "--source-version", "fr"]
        argv += [a if a != "NEW" else os.path.join(d, "new.srm") for a in extra]
        out, err = io.StringIO(), io.StringIO()
        code = None
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(savewrite, "verify_all_checksums",
                                  return_value=["slot 0 sector 0 (section 1)"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                migrate.main()
            except SystemExit as e:
                code = e.code
        check(isinstance(code, str) and "Refusing to write" in code,
              f"{extra}: should refuse, exit was {code!r}")
        with open(p, "rb") as f:
            check(f.read() == raw, f"{extra}: the original was modified")
        check(os.listdir(d) == ["in.srm"], f"{extra}: files created: {os.listdir(d)}")


def test_the_writer_recomputes_checksums_and_preserves_the_other_slot():
    raw = fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)])
    src = read_vanilla(raw, TV)
    sb1 = bytearray(src["sb1"])
    sb1[TF.layout["sb1_party"]] ^= 0x55
    out = savewrite.apply_writes(raw, src["slot"], TF.layout, bytes(sb1), src["pc"])
    check(out != raw, "the edit did not reach the image")
    check(savewrite.verify_all_checksums(out, savewrite.section_sizes(TF.layout)) == [],
          "checksums not recomputed")
    slot_b = SECTORS_PER_SLOT * SECTOR_SIZE
    check(out[slot_b:] == raw[slot_b:], "the inactive slot must not be touched")
    stale = bytearray(out)
    sizes = savewrite.section_sizes(TF.layout)
    check(len(sizes) == 14 and sizes[0] == TF.layout["sb2_size"], f"sizes {sizes}")
    # And the verifier really does notice a bad one.
    first_sb1 = next(i["physical_sector"] for i in src["slot"]["sector_info"]
                     if i["section_id"] == 1)
    stale[first_sb1 * SECTOR_SIZE + 0x38] ^= 0xFF
    check(len(savewrite.verify_all_checksums(bytes(stale), sizes)) == 1,
          "verify_all_checksums should flag exactly the damaged sector")


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
