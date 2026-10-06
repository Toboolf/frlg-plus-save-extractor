---
name: frlg-save-extractor
description: Decode a Pokémon FireRed & LeafGreen+ (FRLG+ v1.5.1 ROM hack) save file (.srm/.sav from RetroArch, mGBA or gpSP) into a full structured report — trainer, badges, money, Key System settings, Pokédex, every party and PC Pokémon with IVs/EVs/nature/moves/origin, the whole rebuilt bag, Day Care, roaming legendary, Master Trainers, event flags, game stats — plus what changed since the last run and where to catch the species still missing. Use this whenever an FRLG+ or FireRed GBA save file is uploaded, or the user asks to read their boxes, check IVs/natures/stats, see Pokédex progress or missing Pokémon, or asks what changed since last time. Not for vanilla FR/LG/Ruby/Sapphire/Emerald saves (those need a separate profile).
---

# FRLG+ save extractor

Turns an FRLG+ save into a full structured report. The script does the decoding; your job is
to run it, read the result, report back, and — only if the user has a tracker configured — fold
the result into it and keep the change-detection baseline current.

`SKILL_DIR` below means the folder containing this file (usually `/mnt/skills/user/frlg-save-extractor`).

## Workflow

1. **Find the save.** Look in `/mnt/user-data/uploads/` for `*.srm` or `*.sav` (128 KB). If there is
   none, ask the user to upload it (RetroArch keeps it in its `saves` folder, named after the ROM,
   `.srm`). If several, use the newest or ask.

2. **Find a previous baseline, if there is one.** If a baseline from a past run is
   available — a file the user points at, or the snapshot tab of a tracker when one is
   configured (see step 5) — save its full text to `/tmp/prev_snapshot.txt`. The loader
   finds the `FRLGSNAP1:` code inside fences and labels. No baseline → skip `--prev`;
   this run becomes the baseline.

3. **Run the extractor.**
   ```bash
   python3 SKILL_DIR/scripts/extract.py /mnt/user-data/uploads/<save> \
       --prev /tmp/prev_snapshot.txt --out /home/claude/save_extract
   ```
   It prints `summary.md` and writes `extract_full.json`, `snapshot_compact.json`, `snapshot_code.txt`
   to the out folder. It never modifies the save.

4. **Read the summary.** Start with "Changes since last run", "Needs your input" and the two status
   lines at the top. Open `extract_full.json` only for specific questions (e.g. one box, one
   Pokémon's IVs, the full missing-species list) — query it with a short Python snippet rather than
   printing it whole.

5. **Report, and sync if a tracker is configured.** Summarise what changed and what the
   user asked about. If `verification.tracker` is present in the result, the user has a
   tracker configured in `scripts/data/local.json`: follow the guide it names (by default
   `references/syncing-to-a-tracker.md`) to update that document, then store the single
   line from `snapshot_code.txt` as the new baseline — only after the update succeeded, so
   a failed run never moves the baseline. If `verification.tracker` is absent, stop after
   reporting; do not invent a tracker.

## Where the numbers come from

Everything the extractor knows about the save layout — offsets, pocket capacities, item and species
tables, flag and map names, wild encounters — is **generated from the FRLG+ source** by
`tools/generate_tables.py` into `scripts/data/*.txt`. Nothing is hand-written or guessed, so the
rebuilt bag, the Key System settings, the roaming legendary and the Master Trainers are read at
their real locations rather than scanned for.

## Trust rules (why the statuses matter)

Two independent things are reported, and they mean different things:

**`section_status`** — did this section decode cleanly *this run*?
- **ok** — decoded and passed its self-checks. Write it to the tracker as fact.
- **partial** — some checks failed; use only the parts the warnings don't cover and say so.
- **unverified** — found by scanning rather than at a known location (only `other_pokemon`).
- **failed** — not available this run.

**`verification`** — has the user confirmed this section against their own game?
- **confirmed** — the user has checked it in-game (or it is pinned by a cross-check that can only
  pass if the layout is right). Treat as settled.
- **source** — correct per the FRLG+ source and self-consistent, but never eyeballed in-game. Fine
  to report; mention it is unconfirmed if the user is about to act on it, and use
  `verification.sections[name].confirm_by` to ask for the one check that would settle it.

The self-checks worth knowing about, because they are what makes "ok" mean something:
- every sector checksum, and every Pokémon's own checksum;
- `GAME_STAT_SAVED_GAME` must equal the save counter — it can only match if both the game-stat
  offset and the encryption key are right;
- every bag item must belong to the pocket it was decoded from, with a quantity of 1-999;
- stored party stats must match stats recomputed from the base stats, IVs, EVs, nature **and the
  Key System's IV/EV calculation mode**.

Other rules:
- Pokémon with `checks.checksum = BAD` are not real data — never report them as owned.
- `stats_state: "stale"` is normal, not a bug: Gen 3 only recalculates stats on level-up, so EVs
  gained since then leave the stored stats slightly low. Only `stats_state: "mismatch"` points at a
  wrong table.
- `origin.class`: `caught_here`, `your_name_other_id` (the player's name but another trainer
  ID), `traded_in`, `in_game_trade`. The game treats all but `caught_here` as traded: 1.5×
  EXP and an obedience cap (shown under Hints). It is a hint, not proof — a save editor can
  make an imported Pokémon look natively caught, in which case `origin.class` reads
  `caught_here` for it. `hints.multiple_starter_origins` is the harder signal: two Pokémon
  cannot both have been chosen as the starter.
- `hints.boxed_hp_not_recorded`: boxed slots whose FRLG+ HP field was never written report
  `status: "not recorded"` and `hp_current: null`. **Never call these fainted.** They matter
  only if the player switches on No Free Heals or Nuzlocke, which would make them withdraw
  at 0 HP; the hint says so.
- Catch locations in "Still to catch" depend on the **Key System version toggle**; the report says
  which side it used. An entry marked "LeafGreen only" needs the player to flip the version key
  first.
- The Key Items pocket can hold stale repeats (FRLG+ never clears a slot it stops using). The
  extractor merges them exactly as the game does and notes how many it merged — that is not a bug.

If "Needs your input" starts with `⚠ ERROR`, tell the user before anything else (e.g. the newest
save slot is corrupt and the report shows an older save, so recent progress is missing).

## Personalising it

Everything user-specific lives in one optional file, `scripts/data/local.json`, which is
gitignored and ships only as `local.example.json`. Copy the example, keep what you want and
delete the rest. It deep-merges over `scripts/data/verification.json`, so you can change one
field without restating its siblings, and a `null` deletes a key — which is how you retire a
`confirm_by` once you have answered it.

- `tracker` — where to sync the report. Present means step 5 does the sync; absent means the
  skill stops after reporting.
- `sections` — promote a section to `evidence: "confirmed"` once you have checked it against
  your own game. `references/verification.md` says what to check for each one.
- `checked_against_save`, `save_history` — notes about the save itself, surfaced in the report.
  `save_history` is worth filling in if your save has an unusual past (imported Pokémon, a save
  editor, a mid-playthrough ROM change), because it explains results that otherwise look wrong.
- `overrides` — name an item id or label a flag when your ROM is newer than the tables.

With no `local.json`, every section reads `evidence: "source"`: correct per the FRLG+ source
and passing the extractor's cross-checks, but never eyeballed in a running game. That is the
honest default, and the report says so.

## Keeping up with a new FRLG+ version

If you patch to a newer FRLG+, re-run the generator against a matching source checkout and run
the tests; `references/verification.md` has the steps and how to re-package the skill.

## Reference files

- `references/syncing-to-a-tracker.md` — how to fold a report into a playthrough document — only
  used when a tracker is configured.
- `references/output_format.md` — every field in `extract_full.json` and the snapshot.
- `references/verification.md` — confirming a section in-game, regenerating the tables from the
  FRLG+ source, and re-packaging the skill.
- `tests/test_extractor.py`, `tests/test_local_overlay.py` — build synthetic saves and a synthetic
  `local.json` overlay to check decoding, diffing and the overlay rules end to end. Run both after
  any code or table change: `python3 SKILL_DIR/tests/run_all.py`.
- `tools/generate_tables.py` — regenerates `scripts/data/*` from the FRLG+ source.
