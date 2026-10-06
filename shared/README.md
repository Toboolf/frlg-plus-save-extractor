# shared/

One copy of the Gen 3 save code, vendored into each skill package by
`build.sh`. The copies under `skills/*/scripts/` and `skills/*/tools/` are
committed and are **generated** — edit the file here and re-run `./build.sh`.

`tests/test_vendored_copies_match.py` in each skill fails if a copy has
drifted, because a writer and a reader that disagree about the save format
would let the reader validate a save the writer corrupted.

| file | what it knows |
|---|---|
| `gen3core.py` | sectors, checksums, the charmap, substructure crypto, stat and nature maths. No game-specific offsets or field semantics — not even which save layout to load, which is why `Tables` takes a required, keyword-only `layout=<profile>.LAYOUT`. |
| `frlgplus.py` | the FRLG+ v1.5.1 profile: its save layout, bag pockets, Key System, and the boxed HP/status/forme halfword in both directions (`decode_box_padding` / `encode_box_padding`). |
| `vanilla_frlg.py` | the vanilla FR/LG profile: its own save layout, the vanilla → FRLG+ ID remaps, and the fact that the same halfword is genuinely padding there. |
| `tools/generate_tables.py` | how to read a save layout and the name tables out of a decomp checkout, for `--game frlgplus` and `--game vanilla`. |
| `tools/generate_remaps.py` | how to pair vanilla and FRLG+ map, layout and map-section IDs up by name to produce the remap tables. |
| `tools/cparse.py` | the C-header parsing (`#define`s, enums, struct offsets) both generators are built on. |
| `tests/test_no_personal_data.py`, `tests/test_vendored_copies_match.py` | the leak guard and the drift test. Authored once here and vendored into each package's `tests/`, so the one file the guard exempts from its own scan, and the test that polices the copies, each have a single anchored original. |
| `tools/display_names.json` | the hand-maintained display-name overrides the generators apply; the one file here that is not code. |

`tools/` is vendored into each skill package as `tools/`, `tests/` as `tests/`, the three modules above
as `scripts/`. The regeneration recipe, including which generator to re-run when,
is in the top-level [`README.md`](../README.md#regenerating-the-tables).
