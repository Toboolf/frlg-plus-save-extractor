#!/usr/bin/env python3
"""Everything the migrator must refuse. Run: python3 tests/test_refusals.py"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
import frlgplus  # noqa: E402
from gen3core import SECTOR_SIZE, SECTORS_PER_SLOT, Tables  # noqa: E402
import vanilla_frlg  # noqa: E402
from migrate import detect_source_version, looks_like_frlgplus  # noqa: E402
from vanilla_read import read_vanilla  # noqa: E402

TV = Tables(layout=vanilla_frlg.LAYOUT)
TF = Tables(layout=frlgplus.LAYOUT)
MIGRATE = os.path.join(SCRIPTS, "migrate.py")
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def run(*args):
    return subprocess.run([sys.executable, MIGRATE, *args],
                          capture_output=True, text=True)


def write_temp(raw, name="in.srm"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(raw)
    return p


def test_a_plan_run_writes_nothing():
    """The default must be plan-only. This is the safety property everything else
    rests on, so it is asserted by comparing the file's bytes, not by trusting a
    printed word."""
    p = write_temp(fixtures.build_vanilla_save(party=[fixtures.mon("Bulbasaur", 5)]))
    before = open(p, "rb").read()
    r = run(p)
    check(r.returncode == 0, f"plan run failed: {r.stderr[-400:]}")
    check(open(p, "rb").read() == before, "a plan run modified the input file")
    check("--apply" in r.stdout, "the plan should say how to apply it")
    d = os.path.dirname(p)
    check(os.listdir(d) == [os.path.basename(p)],
          f"a plan run created files: {os.listdir(d)}")


def test_it_refuses_a_save_that_already_looks_like_frlgplus():
    """Running the tool twice must not double-convert."""
    p = write_temp(fixtures.build_vanilla_save())
    out = os.path.join(os.path.dirname(p), "converted.srm")
    r = run(p, "--apply", "--out", out, "--source-version", "fr")
    check(r.returncode == 0, f"first conversion failed: {r.stderr[-400:]}")
    r2 = run(out)
    check(r2.returncode != 0, "the tool should refuse its own output")
    check("frlg+" in (r2.stdout + r2.stderr).lower(),
          f"the refusal should say the save already looks like FRLG+: {r2.stderr[-300:]}")


def test_the_converted_output_trips_the_heuristic_through_the_key_flags_alone():
    """The synthetic save has an empty bag and no TMs, so the TM and berry signals
    are silent; the refusal rests on the Key System word, which a conversion always
    sets (expMod = 2). Prove that is what fires, not a coincidence."""
    p = write_temp(fixtures.build_vanilla_save())
    out = os.path.join(os.path.dirname(p), "converted.srm")
    r = run(p, "--apply", "--out", out, "--source-version", "lg")
    check(r.returncode == 0, f"conversion failed: {r.stderr[-300:]}")
    got = looks_like_frlgplus(read_vanilla(open(out, "rb").read(), TV), TF)
    check(got == "the Key System flags are already set", f"heuristic said {got!r}")
    check(looks_like_frlgplus(read_vanilla(open(p, "rb").read(), TV), TF) is None,
          "the vanilla input must not trip the heuristic")


def test_a_vanilla_save_with_many_key_items_is_not_mistaken_for_frlgplus():
    """FRLG+'s TM bitfield overlaps vanilla Key Items slots 18-19. Using it as an
    already-converted signal would refuse a legitimate late-game vanilla save."""
    key_ids = [i for i in TF.items if TF.item_pocket(i) == "key_items"][:25]
    check(len(key_ids) == 25, "need 25 key items for the fixture")
    p = write_temp(fixtures.build_vanilla_save(
        key_items=[(i, 1) for i in key_ids],
        party=[fixtures.mon("Bulbasaur", 5)]))
    v = read_vanilla(open(p, "rb").read(), TV)
    check(looks_like_frlgplus(v, TF) is None, "key items tripped the already-converted check")
    r = run(p)
    check(r.returncode == 0, f"a save with 25 key items was refused: {r.stderr[-300:]}")


def test_it_refuses_a_source_whose_checksums_do_not_verify():
    raw = bytearray(fixtures.build_vanilla_save())
    slot_b = SECTORS_PER_SLOT * SECTOR_SIZE
    # Both slots, or the older clean one is used instead. Byte 0, not 0x40: the reader
    # accepts a checksum that matches ANY prefix length, and in a mostly-zero sector
    # a flip further in can leave an earlier prefix that still sums to the stored value.
    for base in (0, slot_b):
        raw[base] ^= 0xFF
    p = write_temp(bytes(raw))
    r = run(p)
    check(r.returncode != 0, "a corrupt source should be refused")
    check("checksum" in (r.stdout + r.stderr).lower(),
          f"the refusal should name checksums: {r.stderr[-300:]}")


def test_it_refuses_a_file_that_is_not_a_save():
    p = write_temp(b"not a save" * 100)
    r = run(p)
    check(r.returncode != 0, "a non-save should be refused")


def test_a_save_sized_file_with_missing_sections_is_refused_cleanly():
    """read_vanilla raises for a gap; the CLI must turn that into one sentence and a
    non-zero exit, not a Python traceback (a bare returncode check cannot tell)."""
    cases = [("zeros", bytes(0x20000)), ("ff", b"\xff" * 0x20000)]
    # A real save with one section's signature destroyed in both slots.
    raw = bytearray(fixtures.build_vanilla_save())
    for base in (0, SECTORS_PER_SLOT * SECTOR_SIZE):
        raw[base + 0xFF8:base + 0xFFC] = bytes(4)       # the signature word
    cases.append(("one section gone", bytes(raw)))
    for name, data in cases:
        r = run(write_temp(data))
        check(r.returncode != 0, f"{name}: should be refused")
        check("Traceback" not in r.stderr, f"{name}: leaked a traceback: {r.stderr[-300:]}")
        check("does not look like" in r.stderr,
              f"{name}: should say it is not a save: {r.stderr[-300:]}")
        check("sections [" not in r.stderr, f"{name}: printed the reader's raw message")


def test_refusal_never_creates_or_alters_files():
    p = write_temp(bytes(0x20000))
    before = open(p, "rb").read()
    run(p, "--apply")
    check(open(p, "rb").read() == before, "a refused --apply modified the input")
    check(os.listdir(os.path.dirname(p)) == [os.path.basename(p)],
          "a refused --apply left files behind")


def test_an_ambiguous_source_version_is_refused_not_guessed():
    """Review Focus 3. With no own-OT Pokemon there is nothing to infer from, and
    both keyFlags.version and the Deoxys forme depend on the answer."""
    raw = fixtures.build_vanilla_save()          # no party, no boxes
    v = read_vanilla(raw, TV)
    version, why = detect_source_version(v)
    check(version is None, f"should not guess, got {version!r} ({why})")
    p = write_temp(raw)
    r = run(p, "--apply", "--out", os.path.join(os.path.dirname(p), "o.srm"))
    check(r.returncode != 0, "apply should refuse without a version")
    check("--source-version" in (r.stdout + r.stderr),
          f"the refusal should name the flag: {r.stderr[-300:]}")


def test_the_version_is_inferred_from_the_players_own_pokemon():
    for game, want in ((4, "fr"), (5, "lg")):
        raw = fixtures.build_vanilla_save(
            party=[fixtures.mon("Bulbasaur", 5, game=game)])
        v = read_vanilla(raw, TV)
        version, why = detect_source_version(v)
        check(version == want, f"game {game}: inferred {version!r} ({why})")


def test_only_one_valid_slot_is_still_convertible():
    """Review Focus 5. A freshly started game has written one slot; the other has
    no valid signature. That is normal, not a corrupt save."""
    p = write_temp(fixtures.build_vanilla_save(
        party=[fixtures.mon("Squirtle", 5)], one_slot_only=True))
    r = run(p)
    check(r.returncode == 0, f"a one-slot save should convert: {r.stderr[-400:]}")
    check("slot" in r.stdout.lower(), "the plan should say which slot it read")


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
