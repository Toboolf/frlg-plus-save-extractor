#!/usr/bin/env bash
# Vendor shared/ into each skill package, then package each skill into
# dist/<name>.skill (a zip whose top-level directory is <name>/, which is how a
# skill installs — so each zip is made from inside skills/).
#
#   ./build.sh            vendor, then build every skill
#   ./build.sh --check    verify the vendored copies are current; change nothing
set -euo pipefail
cd "$(dirname "$0")"

# The authoritative vendoring map: "<path under shared/> <path inside a skill package>".
# tests/test_vendored_copies_match.py (itself vendored from shared/tests/) parses this block, so keep one pair per line.
VENDORED=(
"shared/gen3core.py scripts/gen3core.py"
"shared/frlgplus.py scripts/frlgplus.py"
"shared/vanilla_frlg.py scripts/vanilla_frlg.py"
"shared/vanilla_read.py scripts/vanilla_read.py"
"shared/tools/generate_tables.py tools/generate_tables.py"
"shared/tools/cparse.py tools/cparse.py"
"shared/tools/generate_remaps.py tools/generate_remaps.py"
"shared/tools/display_names.json tools/display_names.json"
"shared/tests/test_no_personal_data.py tests/test_no_personal_data.py"
"shared/tests/test_vendored_copies_match.py tests/test_vendored_copies_match.py"
"shared/tests/test_gen3core_tables.py tests/test_gen3core_tables.py"
)

usage() {
  cat >&2 <<'USAGE'
usage: ./build.sh [--check]
  (no argument)  vendor shared/ into each skill package, then build every skill
  --check        verify the vendored copies are current; change nothing, exit 1 if stale
USAGE
}

# --check is advertised as read-only and build-personal.sh depends on that, so an
# argument that is not exactly --check must not fall through to a full build
# (which would copy every vendored file and rm -rf dist).
if [ "$#" -gt 1 ]; then
  echo "build.sh: too many arguments: $*" >&2
  usage
  exit 2
fi
CHECK_ONLY=${1:-}
if [ -n "$CHECK_ONLY" ] && [ "$CHECK_ONLY" != "--check" ]; then
  echo "build.sh: unknown argument: $CHECK_ONLY" >&2
  usage
  exit 2
fi

stale=0
for pair in "${VENDORED[@]}"; do
  set -- $pair
  src=$1 dst=$2
  [ -f "$src" ] || { echo "missing $src" >&2; exit 1; }
  for skill in skills/*/; do
    name=$(basename "$skill")
    target="$skill$dst"
    [ -d "$(dirname "$target")" ] || continue
    if ! cmp -s "$src" "$target"; then
      if [ "$CHECK_ONLY" = "--check" ]; then
        echo "stale: $target differs from $src"
        stale=1
      else
        cp "$src" "$target"
        echo "vendored: $src -> $target"
      fi
    fi
  done
done

if [ "$CHECK_ONLY" = "--check" ]; then
  [ "$stale" = 0 ] && echo "all vendored copies are current"
  exit $stale
fi

rm -rf dist && mkdir -p dist
for skill in skills/*/; do
  PKG=$(basename "$skill")
  [ -f "$skill/SKILL.md" ] || { echo "skipping $PKG: no SKILL.md"; continue; }
  ( cd skills \
    && find "$PKG" -name '__pycache__' -prune -o -name '*.pyc' -prune -o -type f -print \
       | grep -v -e '/local\.json$' -e '/denylist\.local\.txt$' -e '/\.DS_Store$' \
       | grep -vi -e '\.srm$' -e '\.sav$' \
       | sort \
       | zip -q -X "../dist/$PKG.skill" -@ )
  cp "dist/$PKG.skill" "dist/$PKG.zip"
  echo "dist/$PKG.skill  $(wc -c < "dist/$PKG.skill" | tr -d ' ') bytes"
  unzip -l "dist/$PKG.skill" | tail -n 1
done
