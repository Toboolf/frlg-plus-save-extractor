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
| Everything about each Pokémon except its padding halfword | See below. |

## Converted

| Field | What happens |
| --- | --- |
| The bag | Re-sorted into FRLG+'s pockets: Items, Medicine, Held Items, Poké Balls, Berry Pouch. TMs and HMs become one bit each in the TM Case. Key items become one-byte indices. Quantities are re-encrypted with the save's key. |
| The Day Care | The offspring personality widens from 16 to 32 bits, which moves the struct four bytes earlier. The step counter is rewritten at its new offset and the old byte, now padding, is cleared. |
| Every Pokémon's padding halfword | FRLG+ stores boxed HP, boxed status and the Deoxys forme there. The tool computes them from the Pokémon's own stats. |
| Map group/number, map layout ID, map section IDs | Remapped through generated tables for every stored warp, the saved location, and every quest-log scene. |
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

Three other things can be dropped, and each is listed in the plan too: an item that
no longer fits its FRLG+ pocket (`pocket_full`), a key item FRLG+ has no index for
(`unmapped_key_item`), and an item the tool does not recognise (`unknown_item`).

## Residual risks the tool cannot fix

1. **A quest log that replays a script whose IDs changed.** The quest log holds up
   to four recorded scenes. Map IDs inside them are remapped, but a scene that
   replays a script by ID can point at a different script in FRLG+.
2. **Saved object-event state for a map FRLG+ relaid out.** The game saves which
   object events on the current map have moved or been removed. If FRLG+ changed
   that map's object list, the saved state may not line up. 135 maps differ in
   object-event count between the two trees, so the tool's warning about this will
   fire often.

Both risks come from where the player was standing when they saved. **Convert a
save made in a Pokémon Center or in the player's bedroom**, where neither applies.

## How a conversion is checked

After writing, the tool re-reads the file and compares it with its source. It does
not rely on "every bag item is in its own pocket with a quantity from 1 to 999":
that is also true of a vanilla bag read through the wrong layout. It checks that
the total of every item id and quantity is the same before and after (less the
losses above), that the Day Care step counter reads the same under each layout's own
offset, and that everything in the "carries" table is byte-identical. If any check
fails the tool exits non-zero and names it; the file it wrote should not be used,
and the backup is the way back.
