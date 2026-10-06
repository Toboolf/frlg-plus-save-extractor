#!/usr/bin/env python3
"""Smoke test for extras/fix_boxed_hp.py, the only tool here that writes a save.

Run: python3 tests/test_fix_boxed_hp.py

The no-regression gate diffs the extractor's JSON, so it structurally cannot see
this tool at all — which is how a silent no-op in it stayed hidden once already.
This builds a synthetic save with one boxed Pokemon whose box_hp is 0, runs the
tool's selection logic as a dry run, and asserts it picks exactly that slot and
writes nothing. A second test calls set_box_hp directly and decodes the bytes it
produced, which is the only place the tool's actual output is checked. Neither
touches a real save file.

extras/ is not part of the skill package, so inside an installed .skill there is
nothing to test here and this module says so and passes.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PACKAGE))
TOOL = os.path.join(REPO, "extras", "fix_boxed_hp.py")
sys.path.insert(0, HERE)

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


# Box 1 slot 6 (0-based slot 5): empty in the stock synthetic save, so filling it
# here cannot disturb anything test_extractor.py pins.
ZERO_HP_SLOT = 5
WHERE = "box 1 slot 6"


def build_save_with_an_unwritten_box_slot():
    """The stock synthetic save, with one boxed Pokemon left at box_hp 0."""
    from test_extractor import SECTOR, L, assemble, build_save, make_mon

    sections = build_save(0)
    pc = bytearray(b"".join(sections[5 + i] for i in range(9)))
    off = L["pc_boxes"] + ZERO_HP_SLOT * 80
    pc[off:off + 80] = make_mon("Pidgey", 10, 0x00000030, party=False,
                                met=88, met_level=5, box_hp=0)
    for i in range(9):
        sections[5 + i] = bytes(pc[i * SECTOR:(i + 1) * SECTOR])
    return assemble(sections, 41)


def test_dry_run_selects_the_unwritten_slot():
    if not os.path.isfile(TOOL):
        print("standalone install: no extras/fix_boxed_hp.py to test — nothing to check")
        return
    raw = build_save_with_an_unwritten_box_slot()
    with tempfile.TemporaryDirectory() as tmp:
        save = os.path.join(tmp, "synthetic.bin")
        with open(save, "wb") as f:
            f.write(raw)
        r = subprocess.run([sys.executable, TOOL, save], capture_output=True, text=True)
        out = r.stdout + r.stderr
        print("\n".join("  " + line for line in out.strip().splitlines()[-6:]))
        check(r.returncode == 0, f"dry run exited {r.returncode}:\n{out}")
        check("1 boxed Pokémon would get their HP written" in out,
              f"expected exactly one selected slot, got:\n{out}")
        check(WHERE in out, f"the selected slot should be {WHERE}, got:\n{out}")
        check("Dry run" in out, f"a run without --apply must say it wrote nothing:\n{out}")
        with open(save, "rb") as f:
            check(f.read() == raw, "a dry run must not modify the save file")
        strays = [n for n in os.listdir(tmp) if n != "synthetic.bin"]
        check(not strays, f"a dry run must not leave files behind, found {strays}")


def test_set_box_hp_writes_the_hp_and_keeps_the_forme():
    """The one assertion that reads the tool's own bytes back.

    set_box_hp fills in a missing HP and must change nothing else. It routes through
    frlgplus.write_box_padding, whose `forme` argument DEFAULTS TO 0 — so calling it
    without first reading the forme back would quietly reset a Deoxys to its Normal
    forme, in the only tool here that writes a save. The dry-run test above cannot
    see that: it never decodes the produced bytes, and its fixture has no forme.
    """
    if not os.path.isfile(TOOL):
        print("standalone install: no extras/fix_boxed_hp.py to test — nothing to check")
        return
    sys.path.insert(0, os.path.join(REPO, "extras"))
    import fix_boxed_hp
    import frlgplus
    import gen3core
    from test_extractor import make_mon

    # Deoxys in its Defense forme, deposited with no HP recorded.
    before = make_mon("Deoxys", 50, 0x0000004A, party=False, box_hp=0, forme=2)
    check(frlgplus.decode_box_padding(
        gen3core.u16(gen3core.decrypt_substructures(before)[0]["G"], 10))
        == {"box_hp": 0, "box_status": 0, "forme": 2},
        "premise: the fixture should start at boxHP 0 with forme 2")

    after = fix_boxed_hp.set_box_hp(before, 219)
    got = frlgplus.decode_box_padding(
        gen3core.u16(gen3core.decrypt_substructures(after)[0]["G"], 10))
    check(got == {"box_hp": 219, "box_status": 0, "forme": 2},
          f"set_box_hp should write HP and keep forme 2, got {got}")
    check(len(after) == len(before), f"length changed: {len(after)} vs {len(before)}")
    check(after[:0x1C] == before[:0x1C], "the unencrypted header changed")

    # And the status argument reaches the field without disturbing the forme either.
    sick = fix_boxed_hp.set_box_hp(before, 219, 9)
    got = frlgplus.decode_box_padding(
        gen3core.u16(gen3core.decrypt_substructures(sick)[0]["G"], 10))
    check(got == {"box_hp": 219, "box_status": 9, "forme": 2},
          f"a status write should also keep forme 2, got {got}")

    # Everything outside that one halfword must be untouched.
    b_subs = gen3core.decrypt_substructures(before)[0]
    a_subs = gen3core.decrypt_substructures(after)[0]
    for part in ("A", "E", "M"):
        check(a_subs[part] == b_subs[part], f"substructure {part} changed")
    check(a_subs["G"][:10] == b_subs["G"][:10], "the rest of substructure G changed")


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
