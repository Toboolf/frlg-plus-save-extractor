#!/usr/bin/env python3
"""FRLG+ save extractor.

Usage:
  python3 extract.py SAVE_FILE [--prev PREVIOUS_SNAPSHOT] [--out OUT_DIR]

Writes to OUT_DIR (default ./save_extract):
  summary.md             — read this first; sections mirror the tracker
  extract_full.json      — everything decoded (every Pokémon, flag, candidate pocket, diagnostics)
  snapshot_compact.json  — small baseline for the next diff (readable JSON)
  snapshot_code.txt      — the same baseline as one compressed line (FRLGSNAP1:...) to store in the Doc
and prints summary.md to stdout.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frlgplus  # noqa: E402
import report  # noqa: E402
from gen3core import Tables  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save")
    ap.add_argument("--prev", help="previous snapshot_compact.json (or text containing it)")
    ap.add_argument("--out", default="save_extract")
    ap.add_argument("--quiet", action="store_true", help="don't print the summary")
    args = ap.parse_args()

    with open(args.save, "rb") as f:
        raw = f.read()
    if len(raw) < 0x1C000:
        sys.exit(f"File is only {len(raw):,} bytes — a FireRed save should be 128 KB (131,072 bytes).")

    tables = Tables()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    res = frlgplus.extract(raw, tables, source_name=os.path.basename(args.save))
    compact = report.make_compact(res, now)

    prev, changes = None, None
    if args.prev:
        try:
            prev = report.load_snapshot(args.prev)
            changes = report.diff(prev, compact, tables)
        except Exception as e:  # a bad baseline must never block the extraction
            res["diagnostics"]["warnings"].append(f"previous snapshot unreadable ({e}); no diff produced")
            prev = None
    res["changes"] = changes
    summary = report.render_summary(res, changes, prev is not None, now)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)
    with open(os.path.join(args.out, "extract_full.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "snapshot_compact.json"), "w", encoding="utf-8") as f:
        json.dump(compact, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "snapshot_code.txt"), "w", encoding="utf-8") as f:
        f.write(report.encode_snapshot(compact))
    if not args.quiet:
        print(summary)
    sizes = {n: os.path.getsize(os.path.join(args.out, n))
             for n in ("summary.md", "extract_full.json", "snapshot_compact.json", "snapshot_code.txt")}
    print(f"[written to {os.path.abspath(args.out)}: " + ", ".join(f"{k} {v:,} B" for k, v in sizes.items()) + "]")


if __name__ == "__main__":
    main()
