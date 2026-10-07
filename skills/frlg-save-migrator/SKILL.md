---
name: frlg-save-migrator
description: Convert a vanilla Pokémon FireRed or LeafGreen save (.srm/.sav from RetroArch, mGBA or gpSP) into a save for FRLG+ (the v1.5.1 ROM hack), so a playthrough can continue in the hack. Re-sorts the bag, widens the Day Care, remaps maps, computes boxed HP, and sets the Key System to standard rules, then verifies the result. Prints a plan first and writes nothing without --apply. Use when the user wants to move a vanilla FR/LG save to FRLG+, or asks what would carry over. Not for saves that are already FRLG+ (use the extractor to read those), and not for Ruby/Sapphire/Emerald.
---

# FRLG save migrator

**This skill writes to save files.** Unlike the FRLG+ save extractor, which only ever reads,
this one produces a converted save. It is built so that nothing is written by accident:

- **The default is a plan.** `migrate.py SAVE` prints what it would change and writes nothing.
- **`--apply` is required to write.** Without it no file is created or modified.
- **It backs up whatever it overwrites.** `--apply` with no `--out` copies the save to
  `<save>.bak-<timestamp>` before overwriting it (an existing `--out` file is backed up too).
  `--apply --out NEW` to a new path leaves the source untouched and overwrites nothing, so it
  makes no backup; it is the safer way to run it.
- **It verifies in two stages.** Sector checksums are checked on the converted image in memory
  before anything is written. Agreement with the source (items conserved, money, coins,
  flags, variables, game stats, Pokédex, play time, the Day Care step counter) is checked
  after writing, on the file re-read from disk.
- **If verification fails, the written file is still on disk. Do not load it; discard it.**
  The tool exits with an error naming the check. After an in-place run, restore from the
  backup it names; after `--out`, the source was never touched.

`SKILL_DIR` below means the folder containing this file.

## Workflow

1. **Find the save.** Look in `/mnt/user-data/uploads/` for a `*.srm` or `*.sav` (128 KB) made by
   vanilla FireRed or LeafGreen. If there is none, ask the user to upload it.

2. **Show the plan.**
   ```bash
   python3 SKILL_DIR/scripts/migrate.py /mnt/user-data/uploads/<save>
   ```
   Read it to the user: what carries, what is converted, the losses, the decisions the tool
   made, and any warnings. Do not run `--apply` until they have seen it.

3. **Say which game made the save when it cannot be inferred.** If the tool reports that it
   cannot tell FireRed from LeafGreen, ask the user and pass `--source-version fr` or
   `--source-version lg`. Never guess; the choice is recorded in the Key System.

4. **Convert, to a new file.**
   ```bash
   python3 SKILL_DIR/scripts/migrate.py /mnt/user-data/uploads/<save> \
       --apply --out /home/claude/converted.srm
   ```
   Hand the converted file back to the user. Converting in place (`--apply` alone) is
   available, with its backup, but offer `--out` first.

5. **Optionally read the result.** The FRLG+ save extractor reads the converted file like any
   other FRLG+ save, which is a good way to show the user what they now have.

## What carries and what does not

`references/what-carries.md` is the field-by-field account: what is copied byte for byte
(flags, variables, game stats, the Pokédex, play time, money, coins, PC items), what is
converted (the bag, the Day Care, all 429 Pokémon's boxed HP/status halfword, map IDs), what is
initialised, and what is lost. The main loss is quantity: TMs are one bit each, so duplicate
TMs collapse to one, and key items are one byte each, so duplicates collapse too. Items that no
longer fit their FRLG+ pocket, key items FRLG+ has no index for, and unrecognised items are
dropped as well. The plan lists every loss before anything is written. Point the user at it rather than
summarising from memory.

## Recommend a safe place to convert

Convert a save made **in a Pokémon Center or in the player's own bedroom**. Two things cannot be
fully translated: a quest-log scene that is waiting to replay a script whose IDs changed, and
saved object-event state for a map FRLG+ laid out differently. In a Pokémon Center or the
bedroom no quest-log replay is pending and the object state is trivial. The plan prints a
warning when the save was made on a map where this matters; tell the user, and if they can,
have them save again somewhere safe in the original game and convert that.

## Requirements and limits

- Python 3, standard library only. The generated tables are in `scripts/data/`.
- The input must be a vanilla FR/LG save whose checksums pass. An FRLG+ save is refused.
- The tool invents nothing: it converts the Pokémon and items that are there.
- The one remaining check is yours: boot the converted save in FRLG+ and confirm the player is
  where they saved, the bag is right, a boxed Pokémon withdraws at full HP, and a battle awards EXP.

## Credits

Every offset and table is generated from [FRLG+](https://github.com/Deokishisu/FRLG-Plus) by
Deokishisu and [pret/pokefirered](https://github.com/pret/pokefirered); see the repository
README for the details.
