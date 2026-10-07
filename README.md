# frlg-plus-tools

Tools for Pokémon FireRed & LeafGreen+ (FRLG+) save files: a save extractor skill, a shared
Gen 3 core, and standalone save utilities.

## FRLG+ Save Extractor

A Claude skill that decodes a Pokémon FireRed & LeafGreen+ (FRLG+) GBA save file into a
structured report. It reads the trainer and rival name, the Key System settings (FRLG+'s
version toggle, difficulty, Nuzlocke, IV/EV calculation mode, No Free Heals, Exp. modifier),
badges, Pokédex and Extended Dex progress, the party and all 14 PC boxes with IVs, EVs,
natures, moves and origins, the fully rebuilt FRLG+ bag (all seven pockets), both Day Cares,
the roaming legendary, Master Trainer progress, event flags and story variables, game stats,
the Hall of Fame, and a diff against a previous run.

This tool exists because of someone else's work. **[FRLG+](https://github.com/Deokishisu/FRLG-Plus)
is a ROM hack by [Deokishisu](https://github.com/Deokishisu)**, built on
[pret](https://github.com/pret)'s [decompilation of FireRed and LeafGreen](https://github.com/pret/pokefirered).
Everything this skill knows about where FRLG+ keeps things — every offset, every item and
species name, every map and flag name, the wild-encounter tables — is generated from that
published source, which is the only reason a save reader for a hack this extensively modified
is possible at all. Go play the hack: it is far more interesting than this is. See
[Provenance and credits](#provenance-and-credits) for the details, and
[its features list](https://github.com/Deokishisu/FRLG-Plus/blob/master/FEATURES.md) for what
it changes.

## What's in here

| | reads a save | writes a save |
|---|---|---|
| `skills/frlg-save-extractor/` | yes | never |
| `shared/` (the Gen 3 core, the FRLG+ and vanilla profiles, the table generator) | library code that the skill carries as generated copies and `extras/` imports from them; it never touches a save on its own | no |
| `skills/frlg-save-migrator/` | yes | yes: plan-only by default, and it backs up whatever it overwrites |
| `extras/fix_boxed_hp.py` | yes | yes: dry run by default, and it backs up before it writes |

`shared/` is the original source of that code; `./build.sh` copies it into each skill package
(`skills/frlg-save-extractor/` and `skills/frlg-save-migrator/`), so the skills' copies are generated and are not edited by hand.

Each skill's own `SKILL.md` states what that skill does to your save. Nothing
in this repo overwrites a save file without first making a timestamped backup of
what it overwrites, and each writer checks the checksums of the sectors it
recognises before and after. (The migrator's `--apply --out` to a new path
overwrites nothing, so it makes no backup.)

### What `extras/fix_boxed_hp.py` does when it writes

- **Dry run by default.** It prints what it would change and writes nothing unless you pass `--apply`.
- **Checks first.** It reproduces the checksum of every recognisable sector (one with a valid signature and a known section id, across both slots, up to 28) and refuses to continue if any of those disagree. A sector with no valid signature or an unknown id is skipped rather than treated as a failure, so a slot that was never written does not block a save. Separately, it only writes to a slot that passes its own integrity check: all 14 sections present exactly once, each with a valid signature and checksum, and one consistent save counter.
- **Backs up, then writes.** Immediately before writing it copies the save to `<save>.bak-<timestamp>`.
- **Checks afterwards.** It re-runs the same check on the bytes it wrote and exits with an error if any no longer match.
- **Active slot only.** Only the more recent valid save slot is modified; the other slot is left as a rollback point.

## Install

1. Download `frlg-save-extractor.skill` from the [latest Release](../../releases/latest).
2. Install it as a user skill. The archive unpacks to a `frlg-save-extractor/` directory
   containing `SKILL.md`.

Or clone this repository and run `./build.sh` to produce `dist/frlg-save-extractor.skill`
yourself.

## Example output

See [`examples/summary.example.md`](examples/summary.example.md) for a full report. It is
generated from the synthetic save that `tests/test_extractor.py` builds, not from a real
playthrough — every trainer, Pokémon and item in it is invented.

## Personalising it

Copy `skills/frlg-save-extractor/scripts/data/local.example.json` to `local.json` (it's
gitignored) to personalise the report: point `tracker` at a document to sync the report into,
promote a `sections` entry to `confirmed` once you've checked it against your own game, and
use `checked_against_save`/`save_history` to note which save you verified against and
anything unusual about its history. `overrides` is an escape hatch for naming an item or
labelling a flag the generated tables don't yet know, for a ROM newer than the checkout used
to build them. With no `local.json` at all, every section simply reports `evidence: "source"`,
which is the honest default.

## How it knows the save layout

Every offset and name table the extractor uses is generated from the FRLG+ source itself by
`shared/tools/generate_tables.py`. The generator refuses to emit a layout unless its walk lands
exactly on the next field the source annotates — it does not guess at padding or gaps.

Two cross-checks in the extractor make the resulting report trustworthy rather than merely
plausible: `GAME_STAT_SAVED_GAME` must equal the sector save counter, which can only happen if
both the game-stat offset and the save's encryption key are right, and every bag item must
belong to the pocket it was decoded from, with a quantity between 1 and 999.

## Regenerating the tables

Three generator runs produce everything under `skills/frlg-save-extractor/scripts/data/`,
and they do not all go stale at the same time:

| run | writes | re-run when |
|---|---|---|
| `generate_tables.py --repo ../FRLG-Plus` | `scripts/data/*.txt` and `scripts/data/generated.json` — the FRLG+ names, offsets, flags and encounter tables | FRLG+ releases a new version |
| `generate_tables.py --game vanilla --repo ../pokefirered` | `scripts/data/vanilla/save_layout.txt` and `scripts/data/vanilla/generated.json` — the offsets a *vanilla* FR/LG save is read with | the `pokefirered` checkout moves, or a new save-layout key is added |
| `generate_remaps.py --vanilla ../pokefirered --frlgplus ../FRLG-Plus` | `scripts/data/vanilla/remap_maps.txt`, `remap_layouts.txt` and `remap_mapsec.txt` — the vanilla → FRLG+ map, layout and map-section ID translations | *either* checkout moves, since the IDs are paired up by name across both trees |

Each generator also takes `--skill`, which picks the package it writes into, and the default
is `frlg-save-extractor`. The migrator carries its own copy of every table, so
`--skill frlg-save-migrator` writes *that* package's tables: a new FRLG+ version means running
each generator twice, once per skill, and the same applies to the vanilla and remap runs.

Both of the last two need a [pret/pokefirered](https://github.com/pret/pokefirered) checkout
as well as the FRLG+ one. Regenerating only the FRLG+ tables leaves everything under
`scripts/data/vanilla/` untouched and therefore silently stale, so after an FRLG+ version
bump run all three:

```bash
git clone https://github.com/Deokishisu/FRLG-Plus ../FRLG-Plus
git clone https://github.com/pret/pokefirered ../pokefirered
python3 shared/tools/generate_tables.py --repo ../FRLG-Plus
python3 shared/tools/generate_tables.py --game vanilla --repo ../pokefirered
python3 shared/tools/generate_remaps.py --vanilla ../pokefirered --frlgplus ../FRLG-Plus
# and again for the migrator's own copy of the tables
python3 shared/tools/generate_tables.py --skill frlg-save-migrator --repo ../FRLG-Plus
python3 shared/tools/generate_tables.py --skill frlg-save-migrator --game vanilla --repo ../pokefirered
python3 shared/tools/generate_remaps.py --skill frlg-save-migrator --vanilla ../pokefirered --frlgplus ../FRLG-Plus
./build.sh
python3 skills/frlg-save-extractor/tests/run_all.py
python3 skills/frlg-save-migrator/tests/run_all.py
```

Run the generators from `shared/tools/`, not from the skill's copies in
`skills/frlg-save-extractor/tools/`: `shared/` is the original and the skill copies are
generated, so an edit made to a copy is overwritten by the next `./build.sh`.

## Running the tests

```bash
python3 skills/frlg-save-extractor/tests/run_all.py
```

The same suite runs from the repo checkout and, after `./build.sh`, from the unzipped
`.skill` archive (`frlg-save-extractor/tests/run_all.py`). After editing `shared/`, run
`./build.sh` and then the suite from the repo.

## Provenance and credits

The save-layout and name tables under `skills/frlg-save-extractor/scripts/data/` (the skill's `scripts/data/`) are
machine-generated from [Deokishisu/FRLG-Plus](https://github.com/Deokishisu/FRLG-Plus), the
ROM hack, itself based on [pret/pokefirered](https://github.com/pret/pokefirered), the
decompilation it is built on. They exist so a save file produced by that hack can be read —
they are not a substitute for the hack, and this repository includes no ROM, patch or game
asset of any kind, and none will be accepted. Anything the hack's author objects to in how
this project uses or describes their work will be removed on request. Questions and comments
about FRLG+ itself belong on
[the PokéCommunity thread](https://www.pokecommunity.com/showthread.php?t=454382), not here.

## Licence

The code in this repository is MIT licensed — see [`LICENSE`](LICENSE), which also notes
that the generated tables under `skills/frlg-save-extractor/scripts/data/` are covered separately.
