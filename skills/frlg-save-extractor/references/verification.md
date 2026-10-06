# Verification and regenerating the tables

The extractor no longer guesses where FRLG+ keeps things: every offset, capacity and name table in
`scripts/data/` is generated from the FRLG+ source (github.com/Deokishisu/FRLG-Plus). So there are
only two jobs left — confirming a section against the running game, and regenerating when you
patch to a new FRLG+ version.

## 1. Confirming a section in-game

`scripts/data/verification.json` records, per section, whether it is `source` (right per the source
and self-consistent) or `confirmed` (checked against a running game). The report lists which is
which, and `verification.sections[name].confirm_by` says what check would settle each one.

To confirm one:

1. Ask the player for the one thing `confirm_by` names — usually a screenshot or a value off a menu.
2. Compare it with the report, slot by slot / field by field.
3. If it matches, record it in `local.json`'s `sections` block: set that section's `evidence` to
   `"confirmed"`, add `how` describing what was checked, drop `confirm_by` (set it to `null` so the
   overlay deletes it), and update `checked_against_save`. Never edit `verification.json` itself for
   a personal confirmation — it is the shared public record and stays all `source`.
4. If it does **not** match, that is a real finding: the FRLG+ source checkout and the ROM are
   probably different versions. Re-run the generator (below) against the matching source before
   changing anything by hand.

A fresh install has nothing confirmed: every section in `verification.json` reads
`evidence: "source"`, which is the honest, correct state for a repository nobody has checked
against a running game yet. `local.json`'s `sections` block (see `local.example.json`) is where
one owner's confirmations live, merged over the public record without ever editing it.

What to check for a given section is not restated here: each section's own `confirm_by` in
`verification.json` is the single source of truth for that — the report prints it, and the
extraction result carries it in `verification.sections[name].confirm_by`. Read it there rather
than from a copy that could go stale. Not every section has one: `other_pokemon` and `hints` have
no independent in-game check (see their `how` text) and so carry no `confirm_by` at all — they
cannot be promoted to `confirmed` the way the others can.

Two sections carry a constraint `confirm_by` doesn't say out loud: `roamer` and
`master_trainers` cannot be confirmed at all until the player has beaten the Champion — before
that there is no roaming beast and no Master Trainers exist to beat, so there is nothing in game
to compare the report against yet.

One detail resists confirmation even inside an otherwise-confirmed section: the Key System's
**Exp. modifier** (bits 8-9 of the same word) reads `1x`, and nothing in the save can distinguish
`1x` from `0.5x` or `2x`. `0x` is ruled out because a Pokémon under it would never gain EXP.

## 2. Regenerating from the FRLG+ source

```bash
git clone https://github.com/Deokishisu/FRLG-Plus   # or pull an existing checkout
python3 tools/generate_tables.py --repo /path/to/FRLG-Plus
python3 tests/test_extractor.py        # must end with "All checks passed"
```

The generator refuses to write anything it cannot prove. In particular it walks the item block and
the Day Care through `SaveBlock1` and stops with an error if the walk does not land exactly on the
next field whose offset the source annotates (`seen1`, `ramScript`, `giftRibbons`). If a new FRLG+
version moves a pocket, that is where you will hear about it.

Two things it deliberately does not take from the source:

- **Display capitalisation.** The game stores names in all-caps. `tools/display_names.json` restores
  the normal casing, and the generator rejects any entry that is not the same name letter-for-letter
  ignoring case and punctuation — so it can never silently rename an item.
- **The `daycare` offset.** The `/*0x2F80*/` comment in `include/global.h` is a vanilla leftover:
  FRLG+ replaced `dewfordTrends[5]` (40 bytes) with `filler_EasyChatPairs[36]`, which moved the Day
  Care down 4 bytes to `0x2F7C`. The generator derives it and checks it against `giftRibbons`. A
  real save confirms it — the Four Island breeding step counter lands at `0x3098`.

After regenerating, skim `scripts/data/generated.json` (row counts and the derived layout) and
re-run the extractor on a real save.

## 3. Shipping an updated skill

1. Copy the installed skill somewhere writable: `cp -r SKILL_DIR /home/claude/frlg-save-extractor`
   (or unpack the ZIP from the Project files).
2. Edit what needs editing. Keep the folder name and the `name:` in SKILL.md unchanged.
3. Run `python3 tests/test_extractor.py` — it must end with "All checks passed".
4. Re-run the extractor on a real save and read the two status lines at the top.
5. Package: `./build.sh` from the repository root — it writes `dist/frlg-save-extractor.skill`.
   Install it over the old one, and update any other copy of the skill (e.g. in Project files).

Note: `tools/` is only needed to regenerate tables and requires a local FRLG+ checkout, which the
claude.ai sandbox does not have. Day to day the skill runs entirely off `scripts/data/`.
