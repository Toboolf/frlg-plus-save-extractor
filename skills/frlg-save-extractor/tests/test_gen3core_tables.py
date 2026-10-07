#!/usr/bin/env python3
"""Pin gen3core's substructure order table against a property, not against a copy.

Every fixture in both suites encrypts a Pokemon with SUB_ORDERS and every reader
decrypts with the same SUB_ORDERS, so a wrong ENTRY in that table is invisible to
every test either suite has: the fixture writes the substructures in the wrong
order, the reader reads them back in the same wrong order, and the round trip
agrees. The migrator's per-Pokemon verification cannot see it either, because it
decrypts source and result with one table. A scoped re-review found this blind
spot by swapping two entries and watching both suites stay green.

The defence is that the table is not arbitrary data. Gen 3 stores the four 12-byte
substructures in one of 24 orders selected by `personality % 24`, and those 24
orders are exactly the permutations of (G, A, E, M) in lexicographic order — which
is why `pid % 24` indexes them at all. itertools.permutations generates a tuple
sequence in lexicographic order of the input, so the whole table is derivable, and
any single transposition, rotation or duplicated row fails here.

Run: python3 tests/test_gen3core_tables.py
"""
import itertools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "shared"))

import gen3core                                                      # noqa: E402

FAILURES = []


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)


def test_sub_orders_is_the_24_permutations_of_gaem_in_lexicographic_order():
    want = ["".join(p) for p in itertools.permutations("GAEM")]
    got = list(gen3core.SUB_ORDERS)
    check(got == want,
          "SUB_ORDERS is not the 24 lexicographic permutations of GAEM. "
          f"First difference at index {next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), len(got))}: "
          f"{got[:4]!r} ... against {want[:4]!r} ...")


def test_sub_orders_has_24_distinct_rows_each_using_every_substructure_once():
    got = list(gen3core.SUB_ORDERS)
    check(len(got) == 24, f"SUB_ORDERS has {len(got)} rows, not 24")
    check(len(set(got)) == len(got), "SUB_ORDERS contains a duplicated row")
    for i, row in enumerate(got):
        check(sorted(row) == ["A", "E", "G", "M"],
              f"SUB_ORDERS[{i}] is {row!r}, which is not a permutation of G, A, E, M")


def test_the_index_is_personality_mod_24_over_the_whole_range():
    # Not a tautology: it fails if SUB_ORDERS is ever resized, which would make
    # `pid % 24` either overrun the table or stop covering part of it.
    seen = {gen3core.SUB_ORDERS[pid % 24] for pid in range(24 * 7 + 13)}
    check(len(seen) == 24,
          f"indexing SUB_ORDERS by pid % 24 reaches {len(seen)} distinct orders, not 24")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    for f in FAILURES:
        print(f"FAIL: {f}")
    print(f"\n{len(FAILURES)} failures")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
