#!/usr/bin/env python3
"""Tests for the local.json personal overlay.

Run: python3 tests/test_local_overlay.py

The overlay is how one codebase serves both a public install (no local.json,
nothing confirmed, no tracker) and a personal one (confirmations and a tracker
target), so these tests pin both shapes and the merge rules between them.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, HERE)

import frlgplus  # noqa: E402
from gen3core import Tables, deep_merge  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def temp_data(local=None):
    """A private copy of scripts/data, optionally with a local.json in it."""
    target = os.path.join(tempfile.mkdtemp(), "data")
    shutil.copytree(os.path.join(SCRIPTS, "data"), target)
    stray = os.path.join(target, "local.json")
    if os.path.exists(stray):
        os.remove(stray)
    if local is not None:
        with open(stray, "w", encoding="utf-8") as f:
            json.dump(local, f)
    return target


def test_deep_merge_rules():
    base = {"a": {"b": 1, "c": 2}, "d": 3, "keep": 4}
    overlay = {"a": {"b": 9}, "d": None, "new": {"x": 1}}
    got = deep_merge(base, overlay)
    check(got["a"] == {"b": 9, "c": 2}, f"objects merge key by key, got {got['a']}")
    check("d" not in got, "a null in the overlay must delete the key")
    check(got["keep"] == 4, "untouched keys survive")
    check(got["new"] == {"x": 1}, "new keys are added")
    check(base == {"a": {"b": 1, "c": 2}, "d": 3, "keep": 4}, "deep_merge must not mutate base")
    check(overlay == {"a": {"b": 9}, "d": None, "new": {"x": 1}}, "deep_merge must not mutate overlay")


def test_deep_merge_result_shares_no_structure_with_base():
    # A branch the overlay never touches must still come back as an independent copy —
    # mutating the result afterwards must not be able to reach into base.
    base = {"a": {"b": {"c": 1}}}
    overlay = {"a": {"d": 2}}
    out = deep_merge(base, overlay)
    out["a"]["b"]["c"] = 999
    check(base == {"a": {"b": {"c": 1}}}, f"mutating the result must not affect base, got {base}")


def test_local_json_must_be_an_object():
    target = temp_data()
    with open(os.path.join(target, "local.json"), "w", encoding="utf-8") as f:
        json.dump(["not", "an", "object"], f)
    try:
        Tables(target, layout=frlgplus.LAYOUT)
        check(False, "a non-object local.json must raise an error")
    except Exception as e:  # noqa: BLE001 - any exception type is fine, the message is what matters
        check("local.json" in str(e), f"error must name local.json, got: {e}")


def test_local_json_malformed():
    target = temp_data()
    with open(os.path.join(target, "local.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")
    try:
        Tables(target, layout=frlgplus.LAYOUT)
        check(False, "malformed local.json must raise an error")
    except Exception as e:  # noqa: BLE001 - any exception type is fine, the message is what matters
        check("local.json" in str(e), f"error must name local.json, got: {e}")


def test_without_overlay():
    tables = Tables(temp_data(), layout=frlgplus.LAYOUT)
    check(tables.has_local is False, "has_local must be False with no local.json")
    sections = tables.verification["sections"]
    odd = {k: v["evidence"] for k, v in sections.items() if v["evidence"] != "source"}
    check(not odd, f"every public section must read evidence 'source', got {odd}")
    check(tables.verification.get("tracker") is None, "no tracker without an overlay")
    check(tables.verification.get("checked_against_save") is None,
          "no checked_against_save without an overlay")
    check(tables.verification.get("save_history") is None, "no save_history without an overlay")


OVERLAY = {
    "tracker": {"kind": "claude-doc", "title": "My Tracker", "doc_id": "abc-123",
                "snapshot_tab": "Save snapshot", "guide": "references/syncing-to-a-tracker.md"},
    "sections": {"items": {"evidence": "confirmed", "how": "checked in game", "confirm_by": None}},
}


def test_with_overlay():
    tables = Tables(temp_data(OVERLAY), layout=frlgplus.LAYOUT)
    check(tables.has_local is True, "has_local must be True when local.json exists")
    items = tables.verification["sections"]["items"]
    check(items["evidence"] == "confirmed", f"overlay must win, got {items['evidence']}")
    check(items["how"] == "checked in game", "overlay replaces the scalar")
    check("confirm_by" not in items, "a null confirm_by must delete the key, not set it to null")
    check(tables.verification["sections"]["day_care"]["evidence"] == "source",
          "sections the overlay does not mention are untouched")
    check(tables.verification["tracker"]["doc_id"] == "abc-123", "tracker block comes through")


def test_extraction_reports_the_overlay():
    from test_extractor import assemble, build_save
    raw = assemble(build_save(0), 41)
    plain = frlgplus.extract(raw, Tables(temp_data(), layout=frlgplus.LAYOUT), source_name="t.srm")
    check(plain["verification"]["personal"] is False, "personal must be False with no overlay")
    check(plain["verification"]["tracker"] is None, "tracker must be None with no overlay")
    personal = frlgplus.extract(raw, Tables(temp_data(OVERLAY), layout=frlgplus.LAYOUT), source_name="t.srm")
    check(personal["verification"]["personal"] is True, "personal must be True with an overlay")
    check(personal["verification"]["tracker"]["doc_id"] == "abc-123",
          "the extraction result must carry the tracker block")
    check(personal["verification"]["sections"]["items"]["evidence"] == "confirmed",
          "the extraction result must carry the overlay's evidence")


def main():
    test_deep_merge_rules()
    test_deep_merge_result_shares_no_structure_with_base()
    test_local_json_must_be_an_object()
    test_local_json_malformed()
    test_without_overlay()
    test_with_overlay()
    test_extraction_reports_the_overlay()
    if FAILS:
        print(f"FAILURES ({len(FAILS)}):")
        for line in FAILS:
            print(" -", line)
        sys.exit(1)
    print("All checks passed. (local overlay)")


if __name__ == "__main__":
    main()
