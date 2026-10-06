#!/usr/bin/env python3
"""Fails if a vendored copy has drifted from its shared/ original.

Run: python3 tests/test_vendored_copies_match.py

build.sh copies shared/ into each skill package and those copies are committed,
so the repo holds the same module more than once on purpose. If someone edits a
copy instead of the original, the reader and the writer stop agreeing about the
save format — which is the one failure this repo's structure exists to prevent.

The source repo is recognised by a shared/ directory two levels above the
package. If shared/ is there, a missing or malformed VENDORED block in build.sh
is a failure. Inside an installed .skill there is no shared/ above the package,
so this test reports that it has nothing to compare and passes.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PACKAGE))
FAILS = []


def vendored_map(build_sh):
    """Parse the VENDORED map out of build.sh so the test cannot disagree with the build."""
    with open(build_sh, encoding="utf-8") as f:
        text = f.read()
    block = re.search(r"VENDORED=\(\n(.*?)\n\)", text, re.S)
    if not block:
        return None
    pairs = []
    for line in block.group(1).splitlines():
        line = line.strip().strip('"')
        if not line or line.startswith("#"):
            continue
        src, dst = line.split()
        pairs.append((src, dst))
    return pairs


def main():
    build_sh = os.path.join(REPO, "build.sh")
    if not os.path.isdir(os.path.join(REPO, "shared")):
        print("standalone install: no shared/ to compare against — nothing to check")
        return 0
    # shared/ exists, so this is the source repo: from here a broken build.sh is a failure.
    pairs = []
    if not os.path.isfile(build_sh):
        FAILS.append("shared/ exists but build.sh is missing, so the vendoring map cannot be read")
    else:
        pairs = vendored_map(build_sh)
        if pairs is None:
            FAILS.append("build.sh has no VENDORED=( ... ) block for this test to read")
            pairs = []
    checked = 0
    for src, dst in pairs:
        origin = os.path.join(REPO, src)
        if not os.path.isfile(origin):
            FAILS.append(f"{src}: listed in build.sh but missing from the repo")
            continue
        with open(origin, "rb") as f:
            want = f.read()
        for skill in sorted(os.listdir(os.path.join(REPO, "skills"))):
            copy = os.path.join(REPO, "skills", skill, dst)
            if not os.path.isdir(os.path.dirname(copy)):
                continue  # this package does not vendor into that directory
            if not os.path.isfile(copy):
                FAILS.append(f"skills/{skill}/{dst} is missing — run ./build.sh")
                continue
            with open(copy, "rb") as f:
                got = f.read()
            checked += 1
            if got != want:
                FAILS.append(
                    f"skills/{skill}/{dst} has drifted from {src} — "
                    f"edit {src} and re-run ./build.sh, never the copy")
    if not checked and not FAILS:
        FAILS.append("no vendored copies were compared; the map or the skills tree is wrong")
    for f in FAILS:
        print(f"FAIL: {f}")
    print(f"\n{checked} vendored copies compared, {len(FAILS)} failures")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
