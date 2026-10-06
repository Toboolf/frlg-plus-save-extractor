#!/usr/bin/env python3
"""Smoke test for extras/fix_boxed_hp.py, the only tool here that writes a save.

Run: python3 tests/test_fix_boxed_hp.py

The no-regression gate diffs the extractor's JSON, so it structurally cannot see
this tool at all — which is how a silent no-op in it stayed hidden once already.
This builds a synthetic save with one boxed Pokemon whose box_hp is 0, runs the
tool's selection logic as a dry run, and asserts it picks exactly that slot and
writes nothing. It never touches a real save file.

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
