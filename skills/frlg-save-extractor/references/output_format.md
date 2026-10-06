# Output format (schema_version 2)

## Files
- `summary.md` — human/Claude summary, ordered like the tracker. Read first.
- `extract_full.json` — everything below.
- `snapshot_compact.json` / `snapshot_code.txt` — diff baseline (same data; the code is zlib+base64
  with prefix `FRLGSNAP1:` and is what gets stored wherever the configured tracker keeps a
  baseline, if one is configured).

## extract_full.json top level
| Key | Content |
|---|---|
| `schema_version`, `profile`, `source_file`, `file_size` | run metadata |
| `section_status` | `{section: ok / partial / unverified / failed}` — did it decode cleanly this run |
| `verification` | `personal` (whether a local.json overlay was applied), `tracker` (the configured sync target, or null), `checked_against_save`, and per section `{status, evidence: confirmed/source, how, confirm_by}` — how far it is trusted |
| `save_health` | active slot (A/B), save counter, validity, problems, other slot, per-sector info (`checksum_len_range`, `data_end`), `pc_tail_bytes_used_beyond_vanilla` |
| `key_system` | version (FireRed/LeafGreen), difficulty, nuzlocke, iv_calculation, ev_calculation, no_free_heals, exp_modifier, raw |
| `trainer` | name, gender, tid, sid, rival_name, play_time "H:MM:SS", play_minutes, money, coins, registered_item, master_trainer_title, options_raw, security_key |
| `location` | map "group.number", map_name, region (region-map area), x, y, last_heal_map, last_heal_map_name |
| `party` | list of Pokémon records (below) |
| `boxes` | current_box, total, boxes[] {box, name, wallpaper, count, pokemon[]} |
| `pokedex` | owned_count, seen_count, owned[] / seen[] (national numbers), extended_owned_count, extended_total, header_raw |
| `progress` | badges[], badge_count, named_flags_set{hex: FLAG_NAME}, flags_set[] (hex), flags_set_count, story_flags_set_count, trainers_defeated_flags, system_flags_set_count, vars_nonzero{hex: {value, name}} |
| `game_stats` | {label: value}, labels from constants/game_stat.h |
| `game_stats_check` | `{saved_game_stat, save_counter, matches}` — the cross-check that pins the offset and key |
| `items` | source, `pockets[]` {name, offset_hex, capacity, format, items[], used, empty_slots, note?}, `pocket_coherence_ok`, `totals`, `unknown_item_ids`, `problems` |
| `day_care` | four_island / route_5 {offset_hex, count, pokemon[] (each with `steps`)}, step_counter, step_counter_belongs_to, offspring_personality, egg_waiting |
| `roamer` | active; when active: species, dex, level, hp, ivs, personality, nature, shiny, note |
| `master_trainers` | title, cleared[], cleared_count, total |
| `other_pokemon` | Pokémon records found outside everything the known layout covers, with storage_guess |
| `hall_of_fame` | entries[] (teams of {species, level, nickname}), count |
| `hints` | obedience_cap_for_outsiders, outsiders[], evolutions_overdue[], party_field_moves[], shinies[], pokerus[], species_held_but_not_marked_owned[], missing_extended_dex[] {dex, species, where[]}, missing_extended_dex_count, missing_kanto[], missing_johto_count, missing_hoenn_count, owned_species_not_currently_held, dex_version_note |
| `changes` | null on first run, else {previous_extracted_at, previous_save_counter, lines[]} |
| `diagnostics` | errors[], warnings[], needs_input[], known_gaps[] |

Bag pocket `format` is one of `item_slots_xor_quantity` (quantity XOR'd with the low half of the
encryption key), `item_slots_raw_quantity` (PC storage — not XOR'd), `bitfield` (TM Case: one bit
per TM/HM) or `one_byte_indices` (Key Items: one byte per slot, decoded via
`scripts/data/key_item_indices.txt`).

## Pokémon record
`where` ("party 1", "box 3 slot 12", "Route 5 Day Care slot 1"), `uid` (PID-OTID hex; stable
identity used for diffs), `species`, `species_id` (internal), `dex` (national), `nickname`,
`is_egg`, `level`, `level_source` (stored / from EXP), `exp`, `exp_to_next`, `nature`,
`nature_effect`, `gender`, `shiny`, `ability`, `ability_slot`, `held_item`, `held_item_id`,
`moves[]` {name, id, pp, max_pp, pp_ups}, `ivs` / `evs` {HP, Atk, Def, SpA, SpD, Spe}, `ev_total`,
`hidden_power` ("Type Power"), `friendship`, `pokerus` (none / infected / cured), `stats` +
`stats_source`, `hp_current`, `status`; party only: `stats_state` (match / stale / mismatch) and
`stats_match`; optional: `unown_form`, `forme` (Deoxys), `contest_stats`, `ribbons`, `markings`,
`steps` (Day Care).

Boxed Pokémon also carry HP and status: FRLG+ stores both in the halfword that is padding in
vanilla, and writes them only while the No Free Heals key is on (`status_source` says so).

`origin`: ot_name, ot_tid, ot_sid, ot_gender, met_location, met_location_id, met_level, hatched,
ball, game, language, fateful_encounter, class, game_treats_as_outsider.
`checks`: checksum (ok / BAD), warnings[].

## Compact snapshot
`{schema: 2, extracted_at, save_counter, trainer{money, coins, play_minutes}, key_system{},
badges[], dex_owned[], dex_seen[], flags[], vars{}, game_stats{}, items{name: qty},
roamer{active, species, level}, master_trainers{title, cleared[]}, day_care{steps, egg},
mons{uid: {sp, lv, nick, loc, moves[], item, egg}}}`
