# What carries, what changes, what is lost

A conversion copies the whole of the source save's SaveBlock1, SaveBlock2 and
PokemonStorage, then rewrites only the fields below. Anything not listed here is
copied byte for byte.

## Carries byte for byte

| Field | Notes |
| --- | --- |
| Event flags (including trainer flags, badges, system flags) | Same array, same offset meaning in both games. |
| Script variables | Same. |
| Game stats | Includes "times saved", which the tool checks equals the save counter. |
| Pokédex (seen and owned) | Both flag arrays live in SaveBlock2, which is not rewritten. |
| Play time | Hours, minutes, seconds. |
| Player name, gender, trainer and secret IDs, options, encryption key | SaveBlock2 is untouched. |
| Money and coins | Stored XOR the encryption key; the key is unchanged, so the bytes are too. |
| PC item storage | 30 slots, quantity stored raw in both games. |
| Mail, the Fame Checker, the Trainer Tower | None of the three is a field in the generated layout, so no conversion rule can address those offsets; they stay as copied. (This is by construction, not the result of a byte-level comparison.) |
| Everything about each Pokémon except its padding halfword and its met location | See below. |

## Converted

| Field | What happens |
| --- | --- |
| The bag | Re-sorted into FRLG+'s pockets: Items, Medicine, Held Items, Poké Balls, Berry Pouch. TMs and HMs become one bit each in the TM Case. Key items become one-byte indices. Quantities are re-encrypted with the save's key. |
| The Day Care | The offspring personality widens from 16 to 32 bits, which moves the struct four bytes earlier. The step counter is rewritten at its new offset and the old byte, now padding, is cleared. The Pokémon stored in each Day Care are converted like any other (next row): FRLG+ reads their boxed HP back when they are withdrawn. |
| Every Pokémon's padding halfword | FRLG+ stores boxed HP, boxed status and the Deoxys forme there. The tool computes them from the Pokémon's own stats. "Every" means all 429 slots a save holds: 6 party, 420 PC, the Route 5 Day Care's one and the Four Island Day Care's two. |
| Map group/number, map layout ID, map section IDs | Remapped through generated tables for every stored warp, the saved location, every quest-log scene, and each of the 429 Pokémon's met location. |
| The Key System flags | Set to the standard rules with the source game (FireRed or LeafGreen) recorded. |

## Initialised

FRLG+ reads these from space a vanilla save leaves as filler, so the tool zeroes
them rather than inherit whatever vanilla left there: the Master Trainer title and
flags, the Nuzlocke duplicate flags, the last viewed Pokédex entry, the easy-chat
filler, and the vanilla item slots FRLG+ no longer uses.

## Cannot carry

**Duplicate TM quantities.** The TM Case stores one bit per TM, so a stack of three
TM05 becomes one. The plan lists every such loss before anything is written, and
the verification step accounts for exactly these and nothing else.

Key items have no quantity either: a key item held more than once is kept once (`key_item_quantity`). Three other things can be dropped, and each is listed in the plan too: an item that
no longer fits its FRLG+ pocket (`pocket_full`), a key item FRLG+ has no index for
(`unmapped_key_item`), and an item the tool does not recognise (`unknown_item`).

## Residual risks the tool cannot fix

1. **A quest log that replays a script whose IDs changed.** The quest log holds up
   to four recorded scenes. Map IDs inside them are remapped, but a scene that
   replays a script by ID can point at a different script in FRLG+.
2. **Saved object-event state for a map FRLG+ relaid out.** The game saves which
   object events on the current map have moved or been removed. If FRLG+ changed
   that map's object list, the saved state may not line up. 135 maps differ in
   object-event count between the two trees (generated from the two trees into
   `remap_objcount.txt`), so the tool's warning about this fires when the save was
   made on one of those maps.

Both risks come from where the player was standing when they saved. **Convert a
save made in a Pokémon Center or in the player's bedroom**, where neither applies.

## How a conversion is checked

After writing, the tool re-reads the file and compares it with its source. It does
not rely on "every bag item is in its own pocket with a quantity from 1 to 999":
that is also true of a vanilla bag read through the wrong layout. It checks that
the total of every item id and quantity is the same before and after (less the
losses above), that the Day Care step counter reads the same under each layout's own
offset. It also compares these regions byte for byte between source and result:
flags, script variables, game stats, PC item storage, the Pokédex and play time
(`UNTOUCHED` in `verify.py`), plus money, coins and the encryption key.

The rest of the "carries" table is **carried by construction and not re-checked**: player
name, gender, trainer and secret IDs, options, mail, the Fame Checker, the Trainer
Tower, and every Pokémon's bytes other than its padding halfword. Those blocks are never
rewritten, so there is nothing for a conversion to change, but nothing compares them
afterwards either.

If a check fails the tool exits non-zero and names it, **and the file it wrote is left on
disk**. Do not load it; delete it. What is left to go back to depends on how you ran it:
with `--apply` in place, the original was copied to `<save>.bak-<timestamp>` before it
was overwritten, so restore from that backup. With `--apply --out NEW` there is no backup,
because the source was never touched: the source is still intact, and `NEW` is the only
thing to discard. (If `--out` named an existing file, that file was backed up first.)
