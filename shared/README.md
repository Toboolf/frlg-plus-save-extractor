# shared/

One copy of the Gen 3 save code, vendored into each skill package by
`build.sh`. The copies under `skills/*/scripts/` are committed and are
**generated** — edit the file here and re-run `./build.sh`.

`tests/test_vendored_copies_match.py` in each skill fails if a copy has
drifted, because a writer and a reader that disagree about the save format
would let the reader validate a save the writer corrupted.

| file | what it knows |
|---|---|
| `gen3core.py` | sectors, checksums, the charmap, substructure crypto, stat and nature maths. No game-specific offsets or field semantics. |
| `frlgplus.py` | the FRLG+ v1.5.1 profile: its save layout, bag pockets, Key System and boxed-HP semantics. |
