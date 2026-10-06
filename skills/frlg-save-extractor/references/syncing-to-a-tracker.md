# Syncing a report into a tracker

Only relevant when `verification.tracker` is present in the result — that means the user has a
`tracker` block in `scripts/data/local.json` naming a playthrough document (its `title`, and for a
Claude Doc its `doc_id` and `snapshot_tab`). This guide assumes that document mixes facts the save
knows with things only the player knows (plans, opinions, session history). Update only what the
save can confirm, section by section, and keep everything else. Stamp updated values with the sync
date, e.g. *(save sync, 2026-10-05)*.

| Tracker section | Source in summary / extract_full.json | Rule |
|---|---|---|
| Trainer Info | `trainer.name`, `tid`, `sid`, `rival_name` | Add SID and rival name if missing. Never change the starter story or play-style notes. |
| Current Status → Money, Pokédex, Coins, Play time | `trainer.money/coins/play_time`, `pokedex.owned_count`, `pokedex.extended_owned_count` | Overwrite with new values + date. Pokédex is worth giving twice: owned/seen and Extended Dex x/246. |
| Current Status → location | `location.map_name` / `region` | Now a real place name, so it can go in. Keep the player's own "what I'm doing next" text. |
| Key System settings | `key_system` | Add a one-line row (version, difficulty, nuzlocke, exp modifier). Flag it if the version toggle changed since last sync — it changes which Pokémon the player can catch. |
| Badges | `progress.badges` | Tick boxes. Only add, never untick unless the save clearly shows fewer and the player confirms. |
| Current Team table | `party[]` | Species, nickname, level, all 4 moves, ability, held item, nature in Notes. Keep the player's extra notes per Pokémon (e.g. "Your starter"). Update the "Team as of" date. |
| Box / Notable Pokémon | `boxes`, `day_care`, `other_pokemon` | Update level/nature/IVs of Pokémon already listed (e.g. a notable legendary). Add newly caught legendaries, shinies, and anything the player has called out before. Add one line: "PC: N Pokémon in M boxes (save sync, date)" and one for anything in a Day Care. Don't list every box Pokémon — the player can ask. |
| Legendary tracker | `pokedex.owned` (#144 Articuno, #145 Zapdos, #146 Moltres, #150 Mewtwo; beasts via `roamer`) | Tick when owned. If `roamer.active`, add which beast is roaming and its level/IVs. |
| Key Items & Inventory | `items.pockets` | The bag is read at its real offsets now, so write it as fact. HMs/TMs come from the TM Case pocket. If a pocket has a `note`, don't pass the note into the tracker. |
| Story Milestones | badges + `progress.named_flags_set` | Flag names come from the FRLG+ source, so a name like `FLAG_GOT_SS_TICKET` is a real milestone. Still don't invent narrative around `FLAG_HIDE_*` / `FLAG_HIDDEN_ITEM_*` flags — those are object and item bookkeeping. |
| Master Trainers | `master_trainers` | Only once the player is Champion and has beaten some. One line: title + count + names. |
| Still to catch | `hints.missing_extended_dex` | A short list of the nearest catchable species for the player's current access is useful; don't paste all of them. Note which need the other version key. |
| Session Log | `changes.lines` | Add one row: date · "Save sync" · short list of what changed · "Tracker updated". |
| Open Questions | `diagnostics.needs_input`, `verification` | Add new questions; remove ones the save just answered. The standing one is whichever sections are still `evidence: source`. |

## Writing style in the tracker
- Same tone and table formats the tracker document already uses.
- A section whose `verification.evidence` is `source` can still go in, but say "unconfirmed" if the
  player is going to act on it (e.g. before they trust a bag count).
- Don't paste raw JSON, offsets or hex into the main tracker sections; those belong in the snapshot
  tab named by `local.json`'s `tracker.snapshot_tab` (baseline code only) or in chat.

## What to tell the user afterwards
Two or three sentences: the headline changes (new catches, level-ups, badges, money), what you
updated in the tracker, and the one thing you need from them (usually a screenshot to confirm a
`source` section).
