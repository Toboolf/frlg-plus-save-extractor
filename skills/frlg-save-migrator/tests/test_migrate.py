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
import rules  # noqa: E402
import savewrite  # noqa: E402
import vanilla_frlg  # noqa: E402
import verify  # noqa: E402
from gen3core import SECTOR_SIZE, SECTORS_PER_SLOT, Tables  # noqa: E402
from gen3core import SECTOR_DATA_SIZE, u16, u32  # noqa: E402
from verify import (item_multiset, item_multiset_frlgplus,  # noqa: E402
                    read_frlgplus_sb1, verify_conversion)
import gen3core  # noqa: E402
from vanilla_read import (MAX_PARTY, PARTY_MON_SIZE, box_mon_slots,  # noqa: E402
                          daycare_mon_slots, read_vanilla)

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


def test_the_plan_names_every_kind_of_loss_distinctly():
    plan = {"writes": {}, "mons": {}, "skipped": [], "decisions": [], "notes": [],
            "source_version": "fr",
            "losses": [{"kind": "tm_quantity", "item": "TM05", "detail": "had 3"},
                       {"kind": "pocket_full", "item": "Potion", "detail": "did not fit"},
                       {"kind": "unmapped_key_item", "item": "Some Key", "detail": "no index"},
                       {"kind": "unknown_item", "item": "Odd Thing", "detail": "no pocket"},
                       {"kind": "key_item_quantity", "item": "Bicycle", "detail": "had 2"}]}
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
        # The same function gates the SOURCE on the way in and the converted image on
        # the way out. Only the second must fail here: a source that failed would be
        # refused before anything was converted, which is a different refusal.
        calls = {"n": 0}

        def fail_after_the_input_gate(*_a, **_k):
            calls["n"] += 1
            return [] if calls["n"] == 1 else ["slot 0 sector 0 (section 1)"]

        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(savewrite, "verify_all_checksums",
                                  side_effect=fail_after_the_input_gate), \
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
    """Item conservation: nothing dropped, duplicated or mis-routed by the conversion.

    It does NOT detect a moved pocket boundary in general. Phase A showed 'every item
    in its pocket at quantity 1-999' holds for a vanilla bag read through the FRLG+
    layout, and the multiset is nearly as blind: both layouts carve the same region
    into 4-byte slots, so it only changes where slots fall into the TM-bit/key-index
    bytes or past the region (the Key Items fixture below). Layout detection is the
    job of the Day Care probe; this test defends the items.

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
    """Reading a converted image through the wrong (vanilla) layout is detectable.

    This, not item conservation, is what defends against a layout mix-up. Item
    conservation defends against dropped, duplicated and mis-routed items; it cannot
    see a moved pocket boundary in general, because both layouts carve the same
    region into 4-byte slots.

    Two independent halves, so neither is load-bearing alone:
      1. WRITTEN FIELDS. The conversion itself writes offspringPersonality (u32 at
         the FRLG+ offset) and the two Day Care Pokemon (4 bytes earlier than
         vanilla). A vanilla-layout read of the result gets the step counter where
         it expects the offspring, and shifted mon bytes. This holds whatever the
         padding does.
      2. PADDING. The conversion clears the source's stale step-counter byte, which
         sits in FRLG+ struct padding. Without that, a vanilla read of the step
         counter on the result still sees the old value. This defends that clearing.
    """
    mons_len = TV.layout["daycare_mon_size"] * TV.layout["daycare_mon_count"]
    pattern = bytes((i * 7) % 251 + 1 for i in range(mons_len))
    raw = fixtures.build_vanilla_save(daycare_step=137, daycare_offspring=0xBEEF,
                                      daycare_mons=pattern)
    out = _convert_to_bytes(raw)
    v_out = read_vanilla(out, TV)           # deliberately the WRONG layout
    f_sb1, v_sb1 = read_frlgplus_sb1(out), v_out["sb1"]
    LF, LV = TF.layout, TV.layout
    check(LF["sb1_daycare_offspring"] != LV["sb1_daycare_offspring"]
          and LF["sb1_daycare"] != LV["sb1_daycare"]
          and LF["sb1_daycare_step_counter"] != LV["sb1_daycare_step_counter"],
          "the two layouts must place the Day Care fields at different offsets")

    # Half 1: fields the conversion writes.
    check(u32(f_sb1, LF["sb1_daycare_offspring"]) == 0xBEEF,
          "FRLG+ layout should read the offspring personality the conversion wrote")
    check(f_sb1[LF["sb1_daycare"]:LF["sb1_daycare"] + mons_len] == pattern,
          "FRLG+ layout should read the Day Care Pokemon the conversion moved")
    check(u16(v_sb1, LV["sb1_daycare_offspring"]) != 0xBEEF,
          "a vanilla-layout read must NOT recover the offspring personality")
    check(v_sb1[LV["sb1_daycare"]:LV["sb1_daycare"] + mons_len] != pattern,
          "a vanilla-layout read must NOT recover the Day Care Pokemon")

    # Half 2: the padding the conversion clears.
    check(f_sb1[LF["sb1_daycare_step_counter"]] == 137, "FRLG+ layout should see 137")
    check(v_sb1[LV["sb1_daycare_step_counter"]] != 137,
          "the vanilla layout should NOT see the step counter; the stale source byte "
          "in FRLG+ padding must have been cleared")

    # The same probe on the SOURCE, as Phase A measured on the real save
    # (206 under the vanilla layout, 0 under FRLG+'s).
    # (Own fixture: with an offspring set, FRLG+'s step offset overlaps vanilla's
    # offspring field and would read part of it.)
    src_sb1 = read_vanilla(fixtures.build_vanilla_save(daycare_step=137), TV)["sb1"]
    check(src_sb1[LV["sb1_daycare_step_counter"]] == 137
          and src_sb1[LF["sb1_daycare_step_counter"]] == 0,
          "the source should read 137 under vanilla and 0 under FRLG+")


def _day_care_fixture():
    """A vanilla save with BOTH Day Cares occupied: two at Four Island, one on Route 5."""
    V = TV.layout
    stride = V["daycare_mon_size"]
    four = bytearray(stride * V["daycare_mon_count"])
    four[0:80] = fixtures.mon("Pikachu", 25, pid=0x0A0B0C0D, party=False)
    four[stride:stride + 80] = fixtures.mon("Gyarados", 40, pid=0x1A2B3C4D, party=False)
    return fixtures.build_vanilla_save(
        party=[fixtures.mon("Bulbasaur", 5)],
        daycare_step=137, daycare_offspring=0xBEEF, daycare_mons=bytes(four),
        route5_daycare_mon=fixtures.mon("Abra", 16, pid=0x5E6F7A8B, party=False))


def test_a_pokemon_in_either_day_care_gets_its_box_hp_written():
    """A FireRed save holds 429 struct BoxPokemon, not 426: three of them sit at
    offset 0 of a struct DaycareMon.

    FRLG+ treats that halfword as live in both directions for a Day Care Pokemon:
    StorePokemonInDaycare calls StoreHPAndStatusInBoxMon (src/daycare.c:442) and
    TakeSelectedPokemonFromDaycare calls PopulateBoxHpAndStatusToPartyMon
    (src/daycare.c:522), which with keyFlags.nuzlocke == 1 branches on currentHP == 0.
    A Day Care Pokemon converted with boxHP 0 therefore withdraws dead, which under
    Nuzlocke is unrecoverable. This is the exact failure rule 3 exists to prevent.
    """
    r, got = convert(_day_care_fixture())
    check(got is not None, f"the conversion failed: {r.stderr}")
    if got is None:
        return
    dc = got["day_care"]
    check(dc["four_island"]["count"] == 2 and dc["route_5"]["count"] == 1,
          f"both Day Cares should still be occupied: {dc['four_island']['count']} "
          f"and {dc['route_5']['count']}")
    mons = dc["four_island"]["pokemon"] + dc["route_5"]["pokemon"]
    check(len(mons) == 3, f"expected 3 Day Care Pokemon, got {len(mons)}")
    for m in mons:
        check(m["checks"]["checksum"] == "ok",
              f"{m['where']}: checksum {m['checks']['checksum']} after conversion")
        check(m.get("box_hp_recorded") is True,
              f"{m['where']}: no boxed HP was recorded")
        check(m.get("hp_current") == m["stats"]["HP"],
              f"{m['where']}: recorded HP {m.get('hp_current')} is not its max "
              f"HP {m['stats']['HP']}")


def test_the_day_care_pokemon_get_their_met_location_remapped():
    """Rule 4 applies to all 429, not only the 426 in the party and the boxes."""
    raw = _day_care_fixture()
    src = read_vanilla(raw, TV)
    out = _convert_to_bytes(raw)
    f_sb1 = read_frlgplus_sb1(out)
    remap = src["remaps"]["mapsec"]
    # The Four Island Day Care sits four bytes earlier in FRLG+, so the result is read
    # through the FRLG+ layout's own slot offsets, matched to the source by identity.
    plus_off = {ident: off for ident, _label, off in daycare_mon_slots(TF.layout)}
    for entry in src["day_care"]:
        was = entry["mon"]["origin"]["met_location_id"]
        want = remap.get(was, was)
        off = plus_off[entry["id"]]
        after = gen3core.decrypt_substructures(f_sb1[off:off + 80])[0]["M"][1]
        check(after == want,
              f"{entry['where']}: metLocation {was} should remap to {want}, got {after}")


def test_the_plan_mentions_a_pokemon_in_the_day_care():
    raw = _day_care_fixture()
    src = read_vanilla(raw, TV)
    p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version="fr")
    text = planmod.render_plan(p, src, TF.layout)
    check("3 in the Day Care" in text,
          "the plan should say how many Pokemon are in the Day Care")
    check("all 4 get boxHP written" in text,
          "the boxHP claim should count the Day Care Pokemon too")
    for label in ("Route 5 Day Care", "Four Island Day Care slot 1",
                  "Four Island Day Care slot 2"):
        check(label in text, f"the plan never names the Pokemon in the {label}")


def test_every_box_pokemon_slot_a_save_can_hold_is_in_the_inventory():
    """429 = 6 party + 14x30 boxes + 1 Route 5 + DAYCARE_MON_COUNT at Four Island.

    The number is derived from the generated layout, so a slot that moved or a
    count that changed fails here rather than being quietly left unconverted.
    """
    V = TV.layout
    slots = box_mon_slots(V)
    expected = (MAX_PARTY + V["total_boxes"] * V["in_box_count"]
                + 1 + V["daycare_mon_count"])
    check(len(slots) == expected, f"{len(slots)} slots, expected {expected}")
    check(len(slots) == 429, f"a FireRed save holds 429 BoxPokemon, not {len(slots)}")
    check(len({(blk, off) for _i, _l, blk, off, _n in slots}) == len(slots),
          "two slots share an offset")
    # Every slot in the vanilla inventory must have a counterpart in FRLG+'s, or a
    # Pokemon has nowhere to be written to and nothing to be compared against.
    check({i for i, *_ in slots} == {i for i, *_ in box_mon_slots(TF.layout)},
          "the two layouts' slot inventories do not have the same identities")


def _verify(raw, out, losses=()):
    src = read_vanilla(raw, TV)
    return verify_conversion(src, out, TV.layout, TF.layout, TV, TF, losses=losses)


_REAL_REWRITE = gen3core.rewrite_substructures


def _repack(mon, subs):
    """Re-encrypt and re-checksum one Pokemon from edited PLAINTEXT substructures,
    exactly as the real writer does. Used to inject a fault that reproduces every
    checksum, which is the only kind the other checks cannot see."""
    b = bytearray(mon)
    pid, otid = u32(b, 0), u32(b, 4)
    plain = b"".join(subs[c] for c in gen3core.SUB_ORDERS[pid % 24])
    struct.pack_into("<H", b, 0x1C, sum(struct.unpack("<24H", plain)) & 0xFFFF)
    key = pid ^ otid
    for i in range(0, 48, 4):
        struct.pack_into("<I", b, 32 + i, struct.unpack_from("<I", plain, i)[0] ^ key)
    return bytes(b)


def _halfword_two_bytes_early(mon, *, g_halfword=None, met_location=None):
    """The fault: boxHP written at G[8:10] (ppBonuses and friendship) rather than
    G[10:12]. An off-by-one inside a substructure, which is what this path can
    plausibly get wrong."""
    out = _REAL_REWRITE(mon, g_halfword=None, met_location=met_location)
    subs = dict(gen3core.decrypt_substructures(out)[0])
    g = bytearray(subs["G"])
    struct.pack_into("<H", g, 8, (g_halfword or 0) & 0xFFFF)
    subs["G"] = bytes(g)
    return _repack(out, subs)


def _convert_in_process(raw, version="fr"):
    """Assemble a conversion without going through the CLI, so a fault can be
    injected into the rewrite path."""
    src = read_vanilla(raw, TV)
    p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version=version)
    sb1, pc = migrate.assemble_blocks(p, src, TF, TV.layout)
    return src, p, savewrite.apply_writes(raw, src["slot"], TF.layout, sb1, pc)


def _pokemon_everywhere_save():
    """Party, boxes and both Day Cares occupied: all four kinds of physical slot."""
    boxes = empty_boxes()
    boxes[0][0] = fixtures.mon("Pikachu", 25, pid=0x33445566, party=False)
    boxes[13][29] = fixtures.mon("Gyarados", 40, pid=0x778899AA, party=False)
    stride = TV.layout["daycare_mon_size"]
    four = bytearray(stride * TV.layout["daycare_mon_count"])
    four[0:80] = fixtures.mon("Squirtle", 18, pid=0x0A0B0C0D, party=False)
    four[stride:stride + 80] = fixtures.mon("Charmander", 22, pid=0x1A2B3C4D, party=False)
    return fixtures.build_vanilla_save(
        party=[fixtures.mon("Bulbasaur", 5, pid=0x01020304),
               fixtures.mon("Abra", 16, pid=0x05060708)],
        boxes=boxes, daycare_mons=bytes(four),
        route5_daycare_mon=fixtures.mon("Nidoking", 30, pid=0x5E6F7A8B, party=False))


def test_a_fault_in_the_rewrite_path_that_reproduces_every_checksum_is_caught():
    """Spec 6.2's per-Pokemon equality, and the one way the migrator can corrupt a
    save with nothing to notice.

    rewrite_substructures recomputes the internal checksum over whatever plaintext
    it produced, and savewrite recomputes the sector checksum over whatever bytes it
    was handed. So a wrong offset inside a substructure yields a save where every
    checksum reproduces, items are conserved, the Day Care step probe passes and
    every UNTOUCHED region matches. Only comparing the Pokemon themselves sees it.
    """
    raw = _pokemon_everywhere_save()
    with mock.patch.object(gen3core, "rewrite_substructures",
                           _halfword_two_bytes_early):
        src, p, out = _convert_in_process(raw)
    # Every other check still passes on the faulty image: that is the point.
    check(savewrite.verify_all_checksums(out, savewrite.section_sizes(TF.layout)) == [],
          "the injected fault must still reproduce every sector checksum")
    problems = verify_conversion(src, out, TV.layout, TF.layout, TV, TF,
                                 losses=p["losses"])
    check(problems, "the verifier did not notice a fault inside substructure G")
    check(any("substructure G" in x for x in problems),
          f"the problem should name substructure G: {problems[:4]}")
    # Every occupied slot is affected, so every kind of slot should be named.
    text = " ".join(problems)
    for label in ("party 1", "box 1 slot 1", "box 14 slot 30", "Route 5 Day Care",
                  "Four Island Day Care slot 1"):
        check(label in text, f"the report never names {label}: {problems[:4]}")


def test_the_per_pokemon_comparison_passes_a_clean_conversion():
    """It must allow exactly the two fields the conversion writes - the boxed
    HP/status/forme halfword and metLocation - and nothing else."""
    raw = _pokemon_everywhere_save()
    src, p, out = _convert_in_process(raw)
    problems = verify_conversion(src, out, TV.layout, TF.layout, TV, TF,
                                 losses=p["losses"])
    check(problems == [], f"a clean conversion should verify: {problems}")
    # And the halfword really did change in every occupied slot, so the comparison
    # is passing because it excludes that field rather than because nothing moved.
    f_sb1 = read_frlgplus_sb1(out)
    off = TF.layout["sb1_party"]
    before = gen3core.decrypt_substructures(src["sb1"][off:off + 80])[0]["G"]
    after = gen3core.decrypt_substructures(f_sb1[off:off + 80])[0]["G"]
    check(before[10:12] != after[10:12], "the fixture's boxHP halfword did not change")
    check(before[:10] == after[:10], "nothing else in G should have moved")


def test_a_damaged_party_tail_is_caught_too():
    """A party Pokemon's stored level, HP and stats live past 0x50, outside the
    encrypted region, and are carried through untouched."""
    raw = _pokemon_everywhere_save()
    src, p, out = _convert_in_process(raw)
    # Damage the first party Pokemon's stored level in the written image, then
    # re-checksum its sector, so the only detectable difference is the Pokemon.
    off = TF.layout["sb1_party"] + 0x54
    section = 1 + off // SECTOR_DATA_SIZE
    best, _ = gen3core.choose_slot(out)
    phys = {i["section_id"]: i["physical_sector"] for i in best["sector_info"]
            if i.get("signature_ok")}[section]
    sector = phys * SECTOR_SIZE
    broken = bytearray(out)
    broken[sector + off % SECTOR_DATA_SIZE] ^= 0x10
    sizes = savewrite.section_sizes(TF.layout)
    struct.pack_into("<H", broken, sector + 0xFF6, savewrite.sector_checksum(
        broken[sector:sector + SECTOR_SIZE], sizes[section]))
    broken = bytes(broken)
    check(savewrite.verify_all_checksums(broken, sizes) == [],
          "the damaged image must still pass its sector checksums")
    problems = verify_conversion(src, broken, TV.layout, TF.layout, TV, TF,
                                 losses=p["losses"])
    check(any("party tail" in x for x in problems),
          f"a changed party tail should be reported: {problems[:4]}")


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


def poke_and_reseal(img, block, offset, value):
    """Change one byte of SaveBlock1 ("sb1") or SaveBlock2 ("sb2") in a written image
    and re-seal that sector's checksum, so only the content differs and the verifier
    alone has to notice."""
    sid = 0 if block == "sb2" else 1 + offset // SECTOR_DATA_SIZE
    in_sector = offset if block == "sb2" else offset % SECTOR_DATA_SIZE
    best, _ = gen3core.choose_slot(img)
    base = {i["section_id"]: i["physical_sector"]
            for i in best["sector_info"]}[sid] * SECTOR_SIZE
    buf = bytearray(img)
    buf[base + in_sector] = value
    struct.pack_into("<H", buf, base + 0xFF6, savewrite.sector_checksum(
        bytes(buf[base:base + SECTOR_SIZE]), savewrite.section_sizes(TF.layout)[sid]))
    return bytes(buf)


def test_verify_flags_each_way_a_result_can_disagree():
    raw = _bag_save()
    out = _convert_to_bytes(raw)
    L = TF.layout
    src = read_vanilla(raw, TV)
    best, _ = verify.choose_slot(out)
    sizes = savewrite.section_sizes(L)
    poke = poke_and_reseal

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
        # Stat index 0 is GAME_STAT_SAVED_GAME: this one trips the cross-check with the
        # sector counter, which the byte-equality case below does not exercise.
        "saved-game stat": (poke(out, "sb1", L["sb1_game_stats"],
                                 sb1[L["sb1_game_stats"]] ^ 1), "GAME_STAT_SAVED_GAME"),
        "stats": (poke(out, "sb1", L["sb1_game_stats"] + 8, sb1[L["sb1_game_stats"] + 8] ^ 1),
                  "sb1_game_stats"),
        "pc_items": (poke(out, "sb1", L["sb1_pc_items"], 1), "sb1_pc_items"),
        "pokedex": (poke(out, "sb2", L["sb2_dex_seen"] + 51, 1), "sb2_pokedex"),
        "play time": (poke(out, "sb2", L["sb2_play_time_seconds"], 59),
                      "sb2_play_time_hours"),
        "an item (a pocket emptied)": (poke(out, "sb1", L["sb1_bag_poke_balls"], 0),
                                       "not conserved"),
        # Spec 5.3's remaining do-not-touch regions. seen1 is the one that is not
        # trivially true: it begins at sb1_item_block_end, so a one-slot overrun in
        # any pocket write lands in it.
        "seen1": (poke(out, "sb1", L["sb1_seen1"], 1), "sb1_seen1"),
        "seen2": (poke(out, "sb1", L["sb1_seen2"] + 7, 1), "sb1_seen2"),
        "mail": (poke(out, "sb1", L["sb1_mail"] + 11, 1), "sb1_mail"),
        "the Fame Checker": (poke(out, "sb1", L["sb1_fame_checker"] + 3, 1),
                             "sb1_fame_checker"),
        "the Trainer Tower": (poke(out, "sb1", L["sb1_trainer_tower"] + 5, 1),
                              "sb1_trainer_tower"),
        # Inside quest-log scene 0's flag and var snapshots, which live in a region
        # remap_ids rewrites wholesale. Both offsets are generated: `vars`' own
        # /*0x02c8*/ annotation is stale (0x268 is where NUM_FLAG_BYTES puts it).
        "a quest-log flag snapshot": (
            poke(out, "sb1", L["sb1_quest_log"] + L["quest_log_scene_flags"], 1),
            "quest log"),
        "a quest-log var snapshot": (
            poke(out, "sb1", L["sb1_quest_log"] + L["quest_log_scene_vars"], 1),
            "quest log"),
    }
    for name, (img, needle) in cases.items():
        got = problems_for(img)
        check(any(needle in x for x in got), f"tampering with {name} not flagged: {got}")


def out_sb1_byte0(out):
    return read_frlgplus_sb1(out)[0]


def test_an_unrecognised_loss_kind_is_a_problem_not_an_excuse():
    raw = fixtures.build_vanilla_save(items=[(item("Potion"), 4)])
    out = _convert_to_bytes(raw)
    bogus = [{"kind": "made_up", "item": "Potion", "id": item("Potion"), "lost": 4,
              "detail": "x"}]
    got = _verify(raw, out, bogus)
    check(any("unrecognised loss kind" in x for x in got), f"bogus kind not flagged: {got}")
    # The bogus excuse is refused: Potion is intact, so the only problem is the kind.
    check(len(got) == 1, f"only the unrecognised kind should be reported: {got}")
    # And it really is not subtracted: with the Potion actually missing from the
    # result, the unrecognised excuse must not cover for it, so conservation fails
    # alongside the unrecognised kind rather than instead of it.
    gone = poke_and_reseal(out, "sb1", TF.layout["sb1_bag_medicine"], 0)
    got2 = _verify(raw, gone, bogus)
    check(any("unrecognised loss kind" in x for x in got2),
          f"the unrecognised kind should still be reported: {got2}")
    check(any("not conserved" in x and "Potion" in x for x in got2),
          f"the bogus excuse must not cover a real loss: {got2}")


def test_a_key_item_with_quantity_two_is_reported_as_a_loss():
    bike = next(i for i in sorted(set(TF.key_item_indices.values()))
                if TF.item_pocket(i) == "key_items")
    raw = fixtures.build_vanilla_save(key_items=[(bike, 2)])
    src = read_vanilla(raw, TV)
    p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version="fr")
    kinds = [(l["kind"], l["lost"]) for l in p["losses"]]
    check(kinds == [("key_item_quantity", 1)], f"losses {kinds}")
    text = planmod.render_plan(p, src, TF.layout)
    line = next((x for x in text.splitlines() if "had 2" in x), "")
    print("REPORT LINE:", line.strip())
    check("could not carry" in line, f"report line: {line!r}")
    out = _convert_to_bytes(raw)
    check(_verify(raw, out, p["losses"]) == [], f"should verify with the loss: {_verify(raw, out, p['losses'])}")
    check(any("not conserved" in x for x in _verify(raw, out)),
          "without the loss it must still fail")


def test_the_write_set_is_disjoint_and_inside_saveblock1():
    """assemble_blocks applies about twenty writes blindly by offset in dict order.
    Nothing checked that two of them do not land on the same byte, or that one does
    not run off the end of SaveBlock1, so a last-writer-wins collision would be
    invisible. The Four Island Day Care is the case that nearly was one: its block
    carries two Pokemon, which is why they are spliced into it rather than written
    again on top of it.
    """
    raw = _pokemon_everywhere_save()
    src = read_vanilla(raw, TV)
    p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version="fr")
    check(migrate.check_write_set(p, TF.layout) == [],
          f"the real write set is not sound: {migrate.check_write_set(p, TF.layout)}")
    spans = migrate.write_spans(p, TF.layout)
    check(len(spans) > 15, f"only {len(spans)} write spans; the guard covers too little")
    names = {n for _lo, _hi, n in spans}
    for expected in ("sb1_daycare", "sb1_route5_daycare_mon",
                     "__leftover_item_slots", "party slot 1"):
        check(expected in names, f"the guard does not cover {expected}")

    # The guard must be able to fail, in both ways.
    overlap = dict(p, writes=dict(p["writes"]))
    # One byte too long, so the Day Care block runs into offspringPersonality.
    overlap["writes"]["sb1_daycare"] = p["writes"]["sb1_daycare"] + b"\0"
    check(any("overlaps" in x for x in migrate.check_write_set(overlap, TF.layout)),
          "an overlapping write was not reported")
    over = dict(p, writes=dict(p["writes"]))
    over["writes"]["sb1_trainer_tower"] = bytes(TF.layout["sb1_size"])
    problems = migrate.check_write_set(over, TF.layout)
    check(any("outside SaveBlock1" in x for x in problems),
          f"a write past the end of SaveBlock1 was not reported: {problems}")

    # And assemble_blocks refuses rather than applying an unsound set.
    try:
        migrate.assemble_blocks(overlap, src, TF, TV.layout)
        check(False, "assemble_blocks should refuse an overlapping write set")
    except ValueError:
        pass


def test_an_unsound_write_set_is_one_sentence_not_a_traceback():
    raw = fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)])
    src_path = write_temp(raw)
    out_path = os.path.join(os.path.dirname(src_path), "c.srm")
    argv = ["migrate.py", src_path, "--apply", "--out", out_path,
            "--source-version", "fr"]
    out, err = io.StringIO(), io.StringIO()
    code = None
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.object(migrate, "check_write_set",
                              return_value=["sb1_coins overlaps sb1_money"]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            migrate.main()
        except SystemExit as e:
            code = e.code
    check(isinstance(code, str) and "write set" in code,
          f"should refuse in one sentence, exit was {code!r}")
    check(not os.path.exists(out_path), "a refused run wrote the output anyway")


def test_stale_vanilla_bytes_inside_the_key_system_struct_are_cleared():
    """End to end for rule 5's width: the two bytes past the bitfield halfword are
    `u16 padding2` of a struct FRLG+ reads. A source holding data there must not pass
    it through."""
    L = TF.layout
    stale = b"\xAB\xCD"
    raw = fixtures.build_vanilla_save(
        party=[fixtures.mon("Bulbasaur", 5)],
        extra_sb1=[(L["sb1_key_flags"] + 2, stale)])
    src = read_vanilla(raw, TV)
    check(src["sb1"][L["sb1_key_flags"] + 2:L["sb1_key_flags"] + 4] == stale,
          "the fixture should hold stale bytes inside the Key System struct")
    out = _convert_to_bytes(raw)
    got = read_frlgplus_sb1(out)[L["sb1_key_flags"]:
                                 L["sb1_key_flags"] + L["key_flags_bytes"]]
    check(got == rules.build_key_flags(L, "fr"),
          f"the whole struct should be written, got {got!r}")
    check(got[2:] == bytes(L["key_flags_bytes"] - 2),
          f"stale bytes survived inside struct KeySystemFlags: {got!r}")


def test_the_untouched_list_is_all_actually_compared():
    """UNTOUCHED once named two fields the size table did not cover, so they were
    silently skipped. Every entry must resolve to a real, non-empty region."""
    for name in verify.UNTOUCHED:
        for L in (TV.layout, TF.layout):
            off, size = verify._region(L, name)
            check(size > 0 and off >= 0, f"{name} has no real size in a layout")


def test_the_untouched_list_names_every_region_spec_5_3_protects():
    """Spec 5.3's do-not-touch list, in full. Mail, both `seen` arrays, the Fame
    Checker and the Trainer Tower were not compared anywhere; seen1 is the one that
    is not trivially true, since it starts exactly where the rewritten item block
    ends."""
    for name in ("sb1_mail", "sb1_seen1", "sb1_seen2", "sb1_fame_checker",
                 "sb1_trainer_tower"):
        check(name in verify.UNTOUCHED, f"{name} is not in UNTOUCHED")
    check(TF.layout["sb1_seen1"] == TF.layout["sb1_item_block_end"],
          "seen1 no longer abuts the item block; the overrun hazard has moved")


def test_a_one_slot_overrun_in_a_pocket_write_is_caught():
    """The concrete hazard: the migrator rewrites the whole item block, and `seen1`
    (Pokedex seen flags) begins at its end. Four bytes too many at the end of that
    block eat the first seen flags, change nothing else, and still reproduce every
    checksum.

    The overrun is injected into the zero-fill that finishes the block, because that
    is the write which actually abuts `seen1`: the pockets ahead of it are themselves
    overwritten by it, so only the last write in the block can reach past the end.
    """
    raw = fixtures.build_vanilla_save(items=[(item("Potion"), 4)])
    src = read_vanilla(raw, TV)

    real = rules.zeroed_regions

    def one_slot_too_long(plus_L):
        writes = real(plus_L)
        writes["__leftover_item_slots"] += b"\x01\x00\x01\x00"
        return writes

    with mock.patch.object(rules, "zeroed_regions", one_slot_too_long):
        p = planmod.build_plan(src, TV.layout, TF.layout, TF, source_version="fr")
        sb1, pc = migrate.assemble_blocks(p, src, TF, TV.layout)
    out = savewrite.apply_writes(raw, src["slot"], TF.layout, sb1, pc)
    check(savewrite.verify_all_checksums(out, savewrite.section_sizes(TF.layout)) == [],
          "the overrun must still reproduce every sector checksum")
    problems = verify_conversion(src, out, TV.layout, TF.layout, TV, TF,
                                 losses=p["losses"])
    check(any("sb1_seen1" in x for x in problems),
          f"an overrun into the Pokedex seen flags was not reported: {problems}")


def test_the_quest_log_region_is_compared_with_only_the_remapped_bytes_masked():
    """remap_ids copies all four scenes out and writes them back, changing two bytes
    per scene. Everything else in that region - each scene's flag and var snapshots
    above all - must come across untouched, so the whole region is compared with
    exactly those two bytes per scene masked."""
    remapped = (1, 72)           # CeruleanCave_1F, which FRLG+ renumbers to (1, 75)
    raw = fixtures.build_vanilla_save(quest_log=[remapped, (0, 0), (0, 0), (0, 0)])
    src = read_vanilla(raw, TV)
    out = _convert_to_bytes(raw)
    problems = verify_conversion(src, out, TV.layout, TF.layout, TV, TF)
    check(problems == [], f"a clean conversion should verify: {problems}")
    # The masked bytes really did change, so the pass above is the mask working
    # rather than nothing having moved.
    base = TF.layout["sb1_quest_log"]
    f_sb1 = read_frlgplus_sb1(out)
    check((f_sb1[base + 1], f_sb1[base + 2]) == (1, 75),
          f"scene 0's map should have been remapped: "
          f"{(f_sb1[base + 1], f_sb1[base + 2])}")
    stride = TF.layout["quest_log_scene_size"]
    check(f_sb1[base + 3:base + stride] == src["sb1"][base + 3:base + stride],
          "the rest of scene 0 should be byte-identical")
    # Including both snapshots, which are what this region most has to preserve.
    for key, what in (("quest_log_scene_flags", "flag"), ("quest_log_scene_vars", "var")):
        lo = base + TF.layout[key]
        hi = lo + (TF.layout["num_flag_bytes"] if what == "flag"
                   else 2 * TF.layout["vars_count"])
        check(f_sb1[lo:hi] == src["sb1"][lo:hi],
              f"scene 0's {what} snapshot did not carry unchanged")
        check(any(src["sb1"][lo:hi]), f"the fixture's {what} snapshot is all zero")


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
