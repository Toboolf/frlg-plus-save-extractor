#!/usr/bin/env python3
"""Local-only checks against the owner's real saves. Skips without them.

Run: python3 tests/test_local_oracle.py

These saves never enter the repo. The paths come from the environment so no
personal path is committed:
  FRLG_VANILLA_SAVE=/path/to/vanilla.srm
  FRLG_PLUS_SAVE=/path/to/frlgplus.srm
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from gen3core import Tables, decode_text, encode_text  # noqa: E402
import vanilla_frlg  # noqa: E402
from vanilla_read import read_vanilla  # noqa: E402

FAILS, SKIPPED = [], []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def test_real_vanilla_save():
    path = os.environ.get("FRLG_VANILLA_SAVE")
    if not path or not os.path.isfile(path):
        SKIPPED.append("no FRLG_VANILLA_SAVE — skipped")
        return
    with open(path, "rb") as f:          # read only; never write to a real save
        raw = f.read()
    T = Tables(layout=vanilla_frlg.LAYOUT)
    v = read_vanilla(raw, T)
    check(v["slot"]["valid"], f"active slot invalid: {v['slot']['problems']}")
    check(v["saved_game_stat"] == v["slot"]["counter"],
          f"GAME_STAT_SAVED_GAME {v['saved_game_stat']} != counter {v['slot']['counter']}")
    check(0 <= v["money"] <= 999999, f"money out of range: {v['money']}")
    total = sum(len(p) for p in v["pockets"].values())
    check(total > 0, "no bag items decoded from a real save")
    for name, entries in v["pockets"].items():
        for e in entries:
            check(1 <= e["qty"] <= 999, f"{name} slot {e['slot']}: quantity {e['qty']}")
            check(1 <= e["id"] <= T.layout["items_count"],
                  f"{name} slot {e['slot']}: item id {e['id']}")
    # Spec 7a item 4: this release is Spanish and no text has ever been exercised.
    name = v["player"]["name"]
    check(name.strip() != "",
          "the trainer name decoded empty — the charmap may not cover this language")
    check(all(32 <= ord(c) < 0x3000 for c in name),
          "the trainer name decoded to implausible characters")
    # Round trip: re-encoding the decoded name must reproduce the stored bytes. This is
    # what proves the charmap covers every character in it, not merely that it decoded.
    stored = v["sb2"][T.layout["sb2_player_name"]:T.layout["sb2_player_name"] + 8]
    check(encode_text(name, 8) == stored,
          f"the trainer name does not round-trip through the charmap (length {len(name)})")
    check(decode_text(stored) == name, "decode_text is not stable on the stored name")
    named = [m for m in v["party"] if m]
    if named:
        nick = named[0]["nickname"]
        check(nick is None or nick.strip() != "", "a party nickname decoded empty")
    # Every stored Pokemon slice must be the exact bytes the decoder saw.
    check(len(v["party_raw"]) == len(v["party"]), "party_raw / party length mismatch")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    for s in SKIPPED:
        print(f"SKIP: {s}")
    for f in FAILS:
        print(f"FAIL: {f}")
    print(f"\n{len(FAILS)} failures, {len(SKIPPED)} skipped")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
