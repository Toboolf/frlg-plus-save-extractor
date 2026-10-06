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
| `extras/fix_boxed_hp.py` | yes | yes, with a backup and a dry run by default |

Each skill's own `SKILL.md` states what that skill does to your save. Nothing
in this repo writes to a save file without making a timestamped backup first
and checking the checksums of the sectors it recognises before and after.

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
`tools/generate_tables.py`. The generator refuses to emit a layout unless its walk lands
exactly on the next field the source annotates — it does not guess at padding or gaps.

Two cross-checks in the extractor make the resulting report trustworthy rather than merely
plausible: `GAME_STAT_SAVED_GAME` must equal the sector save counter, which can only happen if
both the game-stat offset and the save's encryption key are right, and every bag item must
belong to the pocket it was decoded from, with a quantity between 1 and 999.

## Regenerating the tables

If FRLG+ updates and the generated tables fall behind:

```bash
git clone https://github.com/Deokishisu/FRLG-Plus ../FRLG-Plus
python3 skills/frlg-save-extractor/tools/generate_tables.py --repo ../FRLG-Plus
python3 skills/frlg-save-extractor/tests/run_all.py
```

## Running the tests

```bash
python3 skills/frlg-save-extractor/tests/run_all.py
```

## Provenance and credits

The save-layout and name tables under `skills/frlg-save-extractor/scripts/data/` are
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
that the generated tables under `scripts/data/` are covered separately.
