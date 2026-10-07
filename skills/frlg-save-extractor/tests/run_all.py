#!/usr/bin/env python3
"""Run every test module. Run: python3 tests/run_all.py"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = ["test_extractor.py", "test_local_overlay.py", "test_no_personal_data.py",
           "test_vendored_copies_match.py", "test_gen3core_tables.py", "test_profiles.py",
           "test_vanilla_layout.py", "test_remaps.py", "test_fix_boxed_hp.py"]


def main():
    failed = []
    for name in MODULES:
        print(f"\n=== {name} ===")
        result = subprocess.run([sys.executable, os.path.join(HERE, name)],
                                capture_output=True, text=True)
        tail = (result.stdout or result.stderr).strip().splitlines()[-12:]
        print("\n".join(tail))
        if result.returncode != 0:
            failed.append(name)
    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    print(f"All {len(MODULES)} test modules passed.")


if __name__ == "__main__":
    main()
