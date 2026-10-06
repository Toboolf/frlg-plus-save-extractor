#!/usr/bin/env python3
"""The migrator end to end, on synthetic saves. Run: python3 tests/test_migrate.py

The FRLG+ reader is the writer's test harness (spec 6.2): convert, then read the
result back with frlgplus.extract and compare it with what went in.
"""
import io
import struct
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
import verify  # noqa: E402
from gen3core import SECTOR_SIZE, SECTORS_PER_SLOT, Tables  # noqa: E402
from gen3core import u16, u32  # noqa: E402
from verify import (item_multiset, item_multiset_frlgplus,  # noqa: E402
                    read_frlgplus_sb1, verify_conversion)
from vanilla_read import PARTY_MON_SIZE, read_vanilla  # noqa: E402

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
    party = [fixtures.mon("Bulbasaur", 7, pid=0x0A0A0A0A), bytes(PARTY_MON_SIZE),
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
        o = TF.layout["sb1_party"] + PARTY_MON_SIZE * i
        check(sb1[o:o + PARTY_MON_SIZE] == want, f"party slot {i} not at its generated offset")


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


def lines_under(text, heading):
    """The loss lines beneath a heading, up to the next heading or blank line."""
    out, on = [], False
    for line in text.split("\n"):
        if on:
            if not line.startswith("    "):
                break
            out.append(line)
        elif line.strip() == heading + ":":
            on = True
    return out


def capacity_words(lines):
    return [w for w in ("full", "room", "fit", "capacity", "holds")
            if any(w in l.lower() for l in lines)]


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
    lines = lines_under(text, planmod.LOSS_HEADINGS["unknown_item"])
    check(lines == ["    - Odd Thing: no pocket"], f"unknown_item lines: {lines}")
    check(not capacity_words(lines),
          f"an unknown item must not be described as a capacity problem: {lines}")
    # The guard must be able to fail: the same check over capacity-flavoured text.
    bad = dict(plan, losses=[{"kind": "unknown_item", "item": "Odd Thing",
                              "detail": "the pocket is full, no room"}])
    check(capacity_words(lines_under(planmod.render_plan(bad, src, TF.layout),
                                     planmod.LOSS_HEADINGS["unknown_item"])),
          "the capacity guard did not fire on capacity-flavoured text")
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


def test_an_existing_out_file_is_backed_up_not_destroyed():
    """Chosen: back up, not refuse. Re-running to the same --out is legitimate, but a
    mistyped --out must never silently destroy an unrelated save."""
    raw = fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)])
    unrelated = b"someone else's save" * 50
    src_path = write_temp(raw)
    d = os.path.dirname(src_path)
    out = os.path.join(d, "other.srm")
    with open(out, "wb") as f:
        f.write(unrelated)
    r = run(src_path, "--apply", "--out", out, "--source-version", "fr")
    check(r.returncode == 0, f"failed: {r.stderr[-300:]}")
    backups = [n for n in os.listdir(d) if n.startswith("other.srm.bak-")]
    check(len(backups) == 1, f"expected a backup of the existing --out, got {os.listdir(d)}")
    if backups:
        with open(os.path.join(d, backups[0]), "rb") as f:
            check(f.read() == unrelated, "the backup is not the unrelated file's bytes")
    with open(src_path, "rb") as f:
        check(f.read() == raw, "the input must be untouched when --out is elsewhere")


def test_an_unwritable_destination_is_one_sentence_not_a_traceback():
    raw = fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)])
    src_path = write_temp(raw)
    d = tempfile.mkdtemp()
    out = os.path.join(d, "o.srm")
    os.chmod(d, 0o555)
    try:
        if os.access(d, os.W_OK):
            return                      # running as root: cannot make it unwritable
        r = run(src_path, "--apply", "--out", out, "--source-version", "fr")
    finally:
        os.chmod(d, 0o755)
    check(r.returncode != 0, "an unwritable destination should fail")
    check("Traceback" not in r.stderr, f"leaked a traceback: {r.stderr[-300:]}")
    check("Could not write" in r.stderr, f"no plain sentence: {r.stderr[-300:]}")
    check(os.listdir(d) == [], f"left files behind: {os.listdir(d)}")
    with open(src_path, "rb") as f:
        check(f.read() == raw, "the input was modified")
    # In place, in a read-only directory: backup cannot be made, original untouched.
    d2 = os.path.dirname(src_path)
    os.chmod(d2, 0o555)
    try:
        r = run(src_path, "--apply")
    finally:
        os.chmod(d2, 0o755)
    check(r.returncode != 0 and "Traceback" not in r.stderr and "Could not write" in r.stderr,
          f"in-place read-only: {r.returncode} {r.stderr[-300:]}")
    with open(src_path, "rb") as f:
        check(f.read() == raw, "in-place: the input was modified")


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

def _convert_to_bytes(raw, **kw):
    src_path = write_temp(raw)
    out_path = os.path.join(os.path.dirname(src_path), "c.srm")
    r = run(src_path, "--apply", "--out", out_path, "--source-version", "fr")
    check(r.returncode == 0, f"conversion failed: {r.stdout[-300:]} {r.stderr[-400:]}")
    with open(out_path, "rb") as f:
        return f.read()


def _bag_save():
    potion, ball, oran = item("Potion"), item("Poké Ball"), item("Oran Berry")
    return fixtures.build_vanilla_save(items=[(potion, 4)], poke_balls=[(ball, 9)],
                                       berries=[(oran, 2)], daycare_step=137)


def test_item_count_conservation_catches_a_boundary_error():
    """The check that a wrong pocket boundary cannot pass.

    Phase A established that 'every item in its pocket at quantity 1-999' is true
    of a vanilla bag read through the FRLG+ layout, so it cannot catch a layout
    mistake. The multiset of (item, quantity) can.

    Both sides are PLAINTEXT: read_vanilla decrypts, and item_multiset_frlgplus
    decrypts the converted image's XOR-encrypted quantities.
    """
    raw = _bag_save()
    out = _convert_to_bytes(raw)
    before = item_multiset(read_vanilla(raw, TV)["pockets"])
    after = item_multiset_frlgplus(out, TF.layout, TF)
    check(before == after, f"items not conserved:\n  before {sorted(before.items())}"
                           f"\n  after  {sorted(after.items())}")
    check(sum(before.values()) == 15, f"fixture bag should total 15, got {before}")

    # A bag long enough in Key Items to reach vanilla slots 18-19, which FRLG+ keeps
    # as the TM Case bitfield. Only there do the two layouts stop agreeing on what a
    # 4-byte slot is: everywhere else both carve the same region into slots.
    ids = sorted(set(TF.key_item_indices.values()))
    ids = [i for i in ids if TF.item_pocket(i) == "key_items"][:20]
    check(len(ids) == 20, "need 20 mappable key items for the boundary fixture")
    raw = fixtures.build_vanilla_save(key_items=[(i, 1) for i in ids])
    before = item_multiset(read_vanilla(raw, TV)["pockets"])
    out = _convert_to_bytes(raw)
    check(before == item_multiset_frlgplus(out, TF.layout, TF),
          "a long Key Items pocket should be conserved by a correct conversion")

    # The disproved check. Read the SOURCE bag through the FRLG+ pocket boundaries:
    # every id is a real item and every quantity is in range, so the old assertion
    # passes... but the multiset is different, so the new one fails.
    src = read_vanilla(raw, TV)
    sb1, key16 = src["sb1"], src["key"] & 0xFFFF
    wrong, plausible = {}, True
    for off_key, cnt_key in (("sb1_bag_items", "bag_items_count"),
                             ("sb1_bag_medicine", "bag_medicine_count"),
                             ("sb1_bag_held_items", "bag_held_items_count"),
                             ("sb1_bag_poke_balls", "bag_poke_balls_count"),
                             ("sb1_bag_berries", "bag_berries_count")):
        for k in range(TF.layout[cnt_key]):
            iid = u16(sb1, TF.layout[off_key] + 4 * k)
            if iid:
                q = u16(sb1, TF.layout[off_key] + 4 * k + 2) ^ key16
                plausible &= iid in TF.items and 1 <= q <= 999
                wrong[iid] = wrong.get(iid, 0) + q
    check(plausible, "the wrong-layout read should look plausible (that is the point)")
    check(wrong != before, "reading the bag through the wrong layout must change the "
                           "multiset, or conservation cannot catch a layout error")


def test_conservation_compares_like_with_like_encryption():
    """The converted image stores quantities encrypted. Reading them raw must NOT
    equal the plaintext source, which is exactly the confusion item_multiset_frlgplus
    exists to prevent."""
    raw = _bag_save()
    out = _convert_to_bytes(raw)
    sb1 = read_frlgplus_sb1(out)
    L = TF.layout
    stored = u16(sb1, L["sb1_bag_medicine"] + 2)   # Potion is Medicine in FRLG+
    check(stored == 4 ^ (fixtures.KEY & 0xFFFF), f"stored quantity {stored} should be encrypted")
    check(item_multiset_frlgplus(out, L, TF)[item("Potion")] == 4, "decrypted quantity should be 4")


def test_the_daycare_step_counter_discriminates_the_layouts():
    """A byte that moves between the layouts, so reading the result with the wrong
    one is detectable. It needs no decryption, which is why it is the cheap probe."""
    raw = fixtures.build_vanilla_save(daycare_step=137)
    out = _convert_to_bytes(raw)
    v_out = read_vanilla(out, TV)           # deliberately the WRONG layout
    f_step = read_frlgplus_sb1(out)[TF.layout["sb1_daycare_step_counter"]]
    check(f_step == 137, f"FRLG+ layout should see 137, got {f_step}")
    wrong = v_out["sb1"][TV.layout["sb1_daycare_step_counter"]]
    check(wrong != 137,
          "the vanilla layout should NOT see the converted step counter — if it does, "
          "this probe cannot discriminate and the test is worthless")
    # The same probe on the SOURCE, which is what Phase A measured on the real save
    # (206 under the vanilla layout, 0 under FRLG+'s).
    src_sb1 = read_vanilla(raw, TV)["sb1"]
    check(src_sb1[TV.layout["sb1_daycare_step_counter"]] == 137
          and src_sb1[TF.layout["sb1_daycare_step_counter"]] == 0,
          "the source should read 137 under vanilla and 0 under FRLG+")
    check(TV.layout["sb1_daycare_step_counter"] != TF.layout["sb1_daycare_step_counter"],
          "the two layouts must put the step counter at different offsets")


def _verify(raw, out, losses=()):
    src = read_vanilla(raw, TV)
    return verify_conversion(src, out, TV.layout, TF.layout, TV, TF, losses=losses)


def test_verify_passes_a_clean_conversion_and_documents_tm_duplicates():
    tm05 = item("TM05")
    raw = fixtures.build_vanilla_save(tmhm=[(item("TM01"), 1), (tm05, 3)],
                                      key_items=[(item("Bike Voucher"), 1)]
                                      if any(r["name"] == "Bike Voucher" for r in TF.items.values()) else [],
                                      items=[(item("Potion"), 4)])
    out = _convert_to_bytes(raw)
    src = read_vanilla(raw, TV)
    p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version="fr")
    check(_verify(raw, out, p["losses"]) == [],
          f"a clean conversion should verify: {_verify(raw, out, p['losses'])}")
    # Without the documented loss, the 2 missing TM05 are an unexplained difference.
    undocumented = _verify(raw, out)
    check(any("not conserved" in x and "TM05" in x for x in undocumented),
          f"undocumented TM loss should be flagged: {undocumented}")


def test_verify_flags_each_way_a_result_can_disagree():
    raw = _bag_save()
    out = _convert_to_bytes(raw)
    L = TF.layout
    src = read_vanilla(raw, TV)
    best, _ = verify.choose_slot(out)
    sizes = savewrite.section_sizes(L)
    where = {i["section_id"]: i["physical_sector"] for i in best["sector_info"]}

    def poke(img, block, offset, value):
        """Change one byte of SaveBlock1 ("sb1") or SaveBlock2 ("sb2") and re-seal
        that sector's checksum, so only the content differs and the verifier alone
        has to notice."""
        sid = 0 if block == "sb2" else 1 + offset // 0xF80
        in_sector = offset if block == "sb2" else offset % 0xF80
        base = where[sid] * SECTOR_SIZE
        buf = bytearray(img)
        buf[base + in_sector] = value
        chunk = bytes(buf[base:base + SECTOR_SIZE])
        struct.pack_into("<H", buf, base + 0xFF6, savewrite.sector_checksum(chunk, sizes[sid]))
        return bytes(buf)

    def problems_for(img):
        return verify_conversion(src, img, TV.layout, L, TV, TF)

    check(savewrite.verify_all_checksums(poke(out, "sb1", 0, out_sb1_byte0(out)), sizes) == [],
          "poke must re-seal checksums, or these cases test the checksum not the verifier")
    check(problems_for(poke(out, "sb1", 0, out_sb1_byte0(out))) == [],
          "an unmodified re-seal should verify")
    sb1 = read_frlgplus_sb1(out)
    sb2 = best["sections"][0]
    cases = {
        "money": (poke(out, "sb1", L["sb1_money"], sb1[L["sb1_money"]] ^ 1), "money"),
        "coins": (poke(out, "sb1", L["sb1_coins"], sb1[L["sb1_coins"]] ^ 1), "coins"),
        "step": (poke(out, "sb1", L["sb1_daycare_step_counter"], 9), "Day Care"),
        "flags": (poke(out, "sb1", L["sb1_flags"] + 5, 1), "sb1_flags"),
        "vars": (poke(out, "sb1", L["sb1_vars"] + 7, 1), "sb1_vars"),
        "stats": (poke(out, "sb1", L["sb1_game_stats"] + 8, sb1[L["sb1_game_stats"] + 8] ^ 1),
                  "sb1_game_stats"),
        "pc_items": (poke(out, "sb1", L["sb1_pc_items"], 1), "sb1_pc_items"),
        "pokedex": (poke(out, "sb2", L["sb2_dex_seen"] + 51, 1), "sb2_pokedex"),
        "play time": (poke(out, "sb2", L["sb2_play_time_seconds"], 59),
                      "sb2_play_time_hours"),
        "an item (a pocket emptied)": (poke(out, "sb1", L["sb1_bag_poke_balls"], 0),
                                       "not conserved"),
    }
    for name, (img, needle) in cases.items():
        got = problems_for(img)
        check(any(needle in x for x in got), f"tampering with {name} not flagged: {got}")


def out_sb1_byte0(out):
    return read_frlgplus_sb1(out)[0]


def test_the_untouched_list_is_all_actually_compared():
    """UNTOUCHED once named two fields the size table did not cover, so they were
    silently skipped. Every entry must resolve to a real, non-empty region."""
    for name in verify.UNTOUCHED:
        for L in (TV.layout, TF.layout):
            off, size = verify._region(L, name)
            check(size > 0 and off >= 0, f"{name} has no real size in a layout")


def test_migrate_exits_nonzero_and_names_the_problem_when_verification_fails():
    raw = _bag_save()
    src_path = write_temp(raw)
    out_path = os.path.join(os.path.dirname(src_path), "c.srm")
    argv = ["migrate.py", src_path, "--apply", "--out", out_path, "--source-version", "fr"]
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.object(verify, "verify_conversion", return_value=["a thing disagreed"]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            migrate.main()
            code = 0
        except SystemExit as e:
            code = e.code
    check(code not in (0, None), "a failed verification must exit non-zero")
    check("a thing disagreed" in err.getvalue(), f"problem not printed: {err.getvalue()}")
    check("do not use it" in str(code), f"exit message should warn: {code}")
    # In place: the message must point at the backup.
    src2 = write_temp(raw, "inplace.srm")
    argv = ["migrate.py", src2, "--apply", "--source-version", "fr"]
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.object(verify, "verify_conversion", return_value=["x"]), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            migrate.main()
            code = 0
        except SystemExit as e:
            code = e.code
    check("backup" in str(code).lower() and ".bak-" in str(code),
          f"in-place failure should name the backup: {code}")


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
