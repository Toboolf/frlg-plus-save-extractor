#!/usr/bin/env bash
# Package the skill into dist/frlg-save-extractor.skill (a zip archive).
# The archive's top-level directory is frlg-save-extractor/, which is how the
# skill installs, so the zip is made from inside skills/.
set -euo pipefail
cd "$(dirname "$0")"
PKG=frlg-save-extractor
rm -rf dist && mkdir -p dist
( cd skills \
  && find "$PKG" -name '__pycache__' -prune -o -name '*.pyc' -prune -o -type f -print \
     | grep -v -e '/local\.json$' -e '/denylist\.local\.txt$' -e '/\.DS_Store$' \
     | sort \
     | zip -q -X "../dist/$PKG.skill" -@ )
cp "dist/$PKG.skill" "dist/$PKG.zip"
echo "dist/$PKG.skill  $(wc -c < "dist/$PKG.skill" | tr -d ' ') bytes"
unzip -l "dist/$PKG.skill" | tail -n 3
