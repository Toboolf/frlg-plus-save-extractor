#!/usr/bin/env python3
"""Fails if anything personal is about to ship. Run: python3 tests/test_no_personal_data.py

The denylist is structural on purpose. This file ships publicly, so listing a
real name or trainer ID here would publish the very data it is meant to keep
out. Instead it matches classes that identify a person without naming one, and
reads extra literal strings from tests/denylist.local.txt when that gitignored
file exists — which is where someone keeps their own values.
"""
import fnmatch
import glob
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)
LOCAL_DENYLIST = os.path.join(HERE, "denylist.local.txt")
# Files that legitimately contain denied-looking text: this scanner's own
# patterns, and the owner's private denylist.
SELF = {os.path.abspath(__file__), os.path.abspath(LOCAL_DENYLIST)}
SKIP_DIRS = {"__pycache__", ".git"}
# .DS_Store matches build.sh's own archive exclusion — kept in sync deliberately,
# not by accident of the decode path skipping files it can't read.
SKIP_NAMES = {"local.json", ".DS_Store"}
# Every skill package carries its own copy of this guard and its own private denylist, and each
# guard also scans the whole repo root. A private denylist holds its owner's literals by design
# (gitignored at any depth and excluded from the zip), so it is skipped by name. The guard
# files hold deliberate seeded patterns and are skipped only by ABSOLUTE PATH, as the exact
# set shared/tests/test_no_personal_data.py plus its vendored copies
# skills/*/tests/test_no_personal_data.py (see guard_copies), never by basename: this file
# ships, so a basename skip would exempt any file of that name anywhere. Those copies are
# anchored to the one original by the VENDORED map and test_vendored_copies_match.py.
SKIP_BY_NAME = {"denylist.local.txt"}
# Save files are binary and carry no pattern this scanner could match, so they are caught by
# filename: anything that looks like one inside a tree about to be zipped is a failure.
SAVE_NAME_GLOBS = ("*.srm", "*.sav", "*.bak-*")  # matched against the lowercased name
# Extra directories skipped only when walking the source repo root: build output is
# regenerated from the package, which is scanned on its own.
REPO_SKIP_DIRS = {"dist"}

PATTERNS = [
    # /home/claude is the claude.ai sandbox's own home: identical for every user, so it
    # identifies nobody, and the skill's instructions legitimately name it.
    ("absolute home path", re.compile(r"/(?:Users|home)/(?!claude\b)[A-Za-z0-9_.\-]+")),
    ("email address", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")),
    ("backup file", re.compile(r"\.bak-\d{6,}")),
    ("access token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
]

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def extra_patterns():
    """Literal strings from the optional gitignored local denylist."""
    if not os.path.exists(LOCAL_DENYLIST):
        return []
    out = []
    with open(LOCAL_DENYLIST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append((f"local denylist entry {line!r}", re.compile(re.escape(line), re.I)))
    return out


def find_repo_root(package):
    """The source-repo root, or None when the package is installed standalone.

    The guard ships inside the .skill, where nothing sits above the package. A shared/
    directory two levels up positively identifies the source repo, which is how
    test_vendored_copies_match.py recognises it too. .git is deliberately not required: a
    GitHub zip or source tarball has no .git, and demanding one made detection fail
    silently there, so the guard stopped scanning the repo root (the hole behind finding I2).
    An installed skill has no shared/ above it, so it cannot wander out of its package.
    """
    root = os.path.dirname(os.path.dirname(package))
    if os.path.isdir(os.path.join(root, "shared")):
        return root
    return None


def guard_copies():
    """Absolute paths of this guard's original and every package's vendored copy in the source
    repo, or the empty set for an installed package, which has nothing above it to exempt."""
    root = find_repo_root(PACKAGE)
    if not root:
        return set()
    found = set()
    for parts in (("shared", "tests"), ("skills", "*", "tests")):
        found.update(os.path.abspath(p) for p in glob.glob(
            os.path.join(root, *parts, "test_no_personal_data.py")))
    return found


def scan_shipped(package, patterns):
    """Scan the package always, and the repo root too when running inside the source repo."""
    hits = scan_tree(package, patterns)
    root = find_repo_root(package)
    if root:
        hits += scan_tree(root, patterns, skip_dirs=REPO_SKIP_DIRS, skip_paths={package})
    return hits


def scan_tree(root, patterns, skip_dirs=(), skip_paths=()):
    """Every (path, label, matched text) a pattern hits under root.

    Reads raw bytes and decodes leniently (errors="replace") rather than opening in text
    mode: a handful of stray non-UTF-8 bytes anywhere in a file used to raise
    UnicodeDecodeError on the whole-file read, which was caught and silently skipped —
    meaning a file with one bad byte and an otherwise plain, matching email address was
    reported as clean. Replacing the bad bytes degrades only the bytes around them; the
    rest of the file, where the real leak usually sits, still gets scanned.

    A file that cannot be opened at all (permissions, a race with deletion, etc.) is not
    skipped either: it is reported as a hit under the "unreadable" label, because a file
    this guard could not read is a file it cannot certify as clean. Silence is not a safe
    default for a guard whose only job is to refuse to ship personal data.

    Each pattern is matched with finditer rather than search so a file with more than one
    distinct leak of the same class is fully reported, not just the first; identical matched
    strings within one file are de-duplicated so a value repeated many times produces one
    line instead of flooding the report.
    """
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d not in skip_dirs
                       and os.path.join(dirpath, d) not in skip_paths]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if any(fnmatch.fnmatchcase(name.lower(), g) for g in SAVE_NAME_GLOBS):
                hits.append((os.path.relpath(path, root), "stray save file", name))
                continue
            if os.path.abspath(path) in SELF or name in SKIP_NAMES or name in SKIP_BY_NAME \
                    or os.path.abspath(path) in guard_copies() or name.endswith(".pyc"):
                continue
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError as exc:
                hits.append((os.path.relpath(path, root), "unreadable",
                             f"could not read file: {exc}"))
                continue
            text = raw.decode("utf-8", errors="replace")
            for label, pattern in patterns:
                seen = set()
                for found in pattern.finditer(text):
                    value = found.group(0)
                    if value in seen:
                        continue
                    seen.add(value)
                    hits.append((os.path.relpath(path, root), label, value))
    return hits


def test_scanner_detects_a_seeded_violation():
    """A scanner that matches nothing would pass forever. Prove it bites."""
    sandbox = tempfile.mkdtemp()
    seeded = {
        "home.md": "see /Users/someone/projects/thing",
        "linux_home.md": "see /home/someone/projects/thing",
        "mail.txt": "write to someone@example.com",
        "doc.json": '{"doc_id": "deadbeef-1111-4222-8333-444455556666"}',
        "backup.md": "my_save.srm.bak-20260101-120000",
        "token.txt": "ghp_0123456789abcdefghij",
    }
    for name, body in seeded.items():
        with open(os.path.join(sandbox, name), "w", encoding="utf-8") as f:
            f.write(body)
    hits = {path for path, _, _ in scan_tree(sandbox, PATTERNS)}
    missed = set(seeded) - hits
    check(not missed, f"the scanner failed to flag seeded violations: {sorted(missed)}")


def test_scanner_accepts_clean_text():
    """Guard against patterns so broad they flag ordinary prose."""
    sandbox = tempfile.mkdtemp()
    with open(os.path.join(sandbox, "ok.md"), "w", encoding="utf-8") as f:
        f.write("Look for a *.srm or *.sav file, about 128 KB, in the saves folder.\n"
                "Offsets like 0x2F7C and ids like 48 are fine.\n"
                "Write output to /home/claude/save_extract — the sandbox home names nobody.\n")
    hits = scan_tree(sandbox, PATTERNS)
    check(not hits, f"the scanner flagged clean prose: {hits}")


def test_scanner_tolerates_invalid_utf8():
    """A few bad bytes elsewhere in a file must not blind the scan to a real match."""
    sandbox = tempfile.mkdtemp()
    path = os.path.join(sandbox, "mixed.txt")
    with open(path, "wb") as f:
        f.write(b"write to someone@example.com and also \xff\xfe stray bytes")
    hits = scan_tree(sandbox, PATTERNS)
    check(any(label == "email address" for _, label, _ in hits),
          f"a plain email in a file with stray non-UTF-8 bytes was not flagged: {hits}")


def test_scanner_reports_every_distinct_match():
    """The first reported line must not be the only leak — every distinct value must appear."""
    sandbox = tempfile.mkdtemp()
    with open(os.path.join(sandbox, "two_emails.txt"), "w", encoding="utf-8") as f:
        f.write("contact alice@example.com and also bob@example.com")
    values = [v for _, label, v in scan_tree(sandbox, PATTERNS) if label == "email address"]
    check(set(values) == {"alice@example.com", "bob@example.com"},
          f"expected both distinct emails reported, got: {values}")


def test_scanner_deduplicates_repeated_matches():
    """A value repeated many times must not flood the report with duplicate lines."""
    sandbox = tempfile.mkdtemp()
    with open(os.path.join(sandbox, "repeated.txt"), "w", encoding="utf-8") as f:
        f.write("alice@example.com ... alice@example.com ... alice@example.com")
    values = [v for _, label, v in scan_tree(sandbox, PATTERNS) if label == "email address"]
    check(values == ["alice@example.com"], f"expected exactly one de-duplicated match, got: {values}")


def test_scanner_reports_unreadable_files_as_failures():
    """A file the guard could not open at all must be reported, never silently skipped."""
    sandbox = tempfile.mkdtemp()
    path = os.path.join(sandbox, "locked.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("irrelevant")
    os.chmod(path, 0o000)
    try:
        hits = scan_tree(sandbox, PATTERNS)
    finally:
        os.chmod(path, 0o644)  # restore so cleanup of the temp dir doesn't trip over it
    check(any(label == "unreadable" for _, label, _ in hits),
          f"an unreadable file was silently skipped instead of reported: {hits}")


def _make_repo(tmp, git="dir"):
    """A temp copy of the source-repo layout: shared/ above skills/<pkg>/tests.

    git is "dir" (a clone), "file" (a worktree or submodule, where .git is a pointer
    file) or None (a GitHub zip or tarball, which has no .git at all).
    """
    pkg = os.path.join(tmp, "skills", "frlg-save-extractor")
    os.makedirs(os.path.join(pkg, "tests"))
    os.makedirs(os.path.join(tmp, "shared"))
    if git == "file":
        with open(os.path.join(tmp, ".git"), "w") as f:
            f.write("gitdir: /elsewhere\n")
    elif git == "dir":
        os.makedirs(os.path.join(tmp, ".git"))
    open(os.path.join(tmp, "build.sh"), "w").close()
    return pkg


def test_scanner_flags_stray_save_files_by_name():
    """A save dropped in the package would be zipped and published; flag it by filename."""
    sandbox = tempfile.mkdtemp()
    for name in ("my.srm", "my.sav", "my.srm.bak-x"):
        with open(os.path.join(sandbox, name), "wb") as f:
            f.write(b"\x00" * 16)
    flagged = {p for p, label, _ in scan_tree(sandbox, PATTERNS) if label == "stray save file"}
    check(flagged == {"my.srm", "my.sav", "my.srm.bak-x"}, f"stray save files not all flagged: {flagged}")


def test_guard_scans_repo_root_inside_source_repo():
    tmp = tempfile.mkdtemp()
    pkg = _make_repo(tmp)
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
        f.write("write to someone@example.com")
    hits = scan_shipped(pkg, PATTERNS)
    check(any(p == "README.md" and label == "email address" for p, label, _ in hits),
          f"a violation at the repo root was not reported: {hits}")


def test_guard_detects_repo_when_git_is_a_file():
    tmp = tempfile.mkdtemp()
    pkg = _make_repo(tmp, git="file")
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
        f.write("write to someone@example.com")
    check(find_repo_root(pkg) == tmp, "a worktree/submodule layout (.git as a file) was not detected")
    check(any(p == "README.md" for p, _, _ in scan_shipped(pkg, PATTERNS)),
          "a root violation was not reported when .git is a file")


def test_guard_detects_repo_without_git():
    """A GitHub zip or tarball has no .git; it must still be scanned as the source repo."""
    tmp = tempfile.mkdtemp()
    pkg = _make_repo(tmp, git=None)
    check(not os.path.exists(os.path.join(tmp, ".git")), "test setup left a .git behind")
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as f:
        f.write("write to someone@example.com")
    check(find_repo_root(pkg) == tmp, "a .git-less copy of the repo was not detected as the source repo")
    check(any(p == "README.md" for p, _, _ in scan_shipped(pkg, PATTERNS)),
          "a root violation was not reported in a .git-less copy")


def test_repo_root_found_from_the_real_layout():
    """skills/<name>/ is two levels under the repo root, so the walk reaches it.

    Only meaningful in the source repo: the unzipped .skill has no repo above it, which
    test_standalone_install_finds_no_repo covers, so there is nothing to find there.
    """
    if not os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(PACKAGE)), "shared")):
        return
    root = find_repo_root(PACKAGE)
    check(root is not None, "find_repo_root returned None inside the source repo")
    if root:
        check(os.path.isdir(os.path.join(root, "shared")),
              f"{root} does not look like the repo root (no shared/)")


def test_standalone_install_finds_no_repo():
    """An installed .skill must not wander out of its package."""
    with tempfile.TemporaryDirectory() as d:
        pkg = os.path.join(d, "frlg-save-extractor")
        os.makedirs(os.path.join(pkg, "scripts"))
        check(find_repo_root(pkg) is None,
              "find_repo_root must return None when there is no shared/ above the package")


def test_scanner_flags_stray_save_files_case_insensitively():
    sandbox = tempfile.mkdtemp()
    names = ("DROPPED.SRM", "Thing.BAK-20260101", "Old.Sav")
    for name in names:
        with open(os.path.join(sandbox, name), "wb") as f:
            f.write(b"\x00")
    flagged = {p for p, label, _ in scan_tree(sandbox, PATTERNS) if label == "stray save file"}
    check(flagged == set(names), f"upper-case stray saves not all flagged: {flagged}")


def test_guard_skips_git_and_dist_in_repo_root():
    tmp = tempfile.mkdtemp()
    pkg = _make_repo(tmp)
    os.makedirs(os.path.join(tmp, "dist"))
    for rel in ("dist/x.md", ".git/config"):
        with open(os.path.join(tmp, rel), "w", encoding="utf-8") as f:
            f.write("someone@example.com")
    hits = scan_shipped(pkg, PATTERNS)
    check(not hits, f".git or dist was scanned: {hits}")


def test_guard_scans_only_package_when_installed_standalone():
    tmp = tempfile.mkdtemp()
    pkg = os.path.join(tmp, "mid", "frlg-save-extractor")  # no build.sh or .git above it
    os.makedirs(os.path.join(pkg, "tests"))
    # Two levels up is where the root candidate sits, so this would be scanned if detection
    # were unconditional.
    with open(os.path.join(tmp, "outside.md"), "w", encoding="utf-8") as f:
        f.write("someone@example.com")
    check(find_repo_root(pkg) is None, "standalone install was mistaken for a source repo")
    check(not scan_shipped(pkg, PATTERNS), "standalone scan reported something outside the package")


def test_shipped_tree_is_clean():
    hits = scan_shipped(PACKAGE, PATTERNS + extra_patterns())
    for path, label, text in hits:
        FAILS.append(f"{path}: {label} — {text!r}")


def test_a_file_sharing_the_guards_basename_is_still_scanned():
    """The exemption is by absolute path, so a same-named file elsewhere is not exempt."""
    with tempfile.TemporaryDirectory() as tmp:
        decoy = os.path.join(tmp, "notes", "test_no_personal_data.py")
        os.makedirs(os.path.dirname(decoy))
        with open(decoy, "w", encoding="utf-8") as f:
            f.write("owner = 'someone@example.com'\n")
        hits = scan_tree(tmp, PATTERNS)
        check(any(label == "email address" for _, label, _ in hits),
              "a file named like the guard but outside the exempt set was not scanned")


def main():
    test_a_file_sharing_the_guards_basename_is_still_scanned()
    test_scanner_detects_a_seeded_violation()
    test_scanner_accepts_clean_text()
    test_scanner_tolerates_invalid_utf8()
    test_scanner_reports_every_distinct_match()
    test_scanner_deduplicates_repeated_matches()
    test_scanner_reports_unreadable_files_as_failures()
    test_scanner_flags_stray_save_files_by_name()
    test_guard_scans_repo_root_inside_source_repo()
    test_guard_skips_git_and_dist_in_repo_root()
    test_guard_detects_repo_when_git_is_a_file()
    test_guard_detects_repo_without_git()
    test_repo_root_found_from_the_real_layout()
    test_standalone_install_finds_no_repo()
    test_scanner_flags_stray_save_files_case_insensitively()
    test_guard_scans_only_package_when_installed_standalone()
    test_shipped_tree_is_clean()
    if FAILS:
        print(f"FAILURES ({len(FAILS)}):")
        for line in FAILS:
            print(" -", line)
        sys.exit(1)
    print("All checks passed. (no personal data)")


if __name__ == "__main__":
    main()
