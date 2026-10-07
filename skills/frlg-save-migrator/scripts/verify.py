"""Check a converted save against its source.

Spec 6.2 originally leaned on "every bag item in its own pocket at quantity
1-999". Phase A disproved that as a discriminator: read a REAL vanilla bag
through the FRLG+ layout and it still yields 80 filled slots with every id valid
and every quantity in range, because both layouts carve the same 0x310-0x5F8
region into different 4-byte-aligned pockets. Only the boundaries move.

So the checks here are the ones that do fail when something is wrong:
conservation of the item multiset, the Day Care step counter (a byte whose offset
differs between the layouts), and equality of everything the conversion is not
supposed to touch.

DOMAINS. Pocket quantities exist in two forms and mixing them makes every
comparison fail (or, worse, pass by accident):
  - PLAINTEXT: what read_vanilla returns (it already XORs with key16), and what
    the conversion rules compute with.
  - ENCRYPTED: what is stored in a save's bytes (qty ^ key16).
Everything in this module that returns or compares a multiset is in PLAINTEXT.
item_multiset_frlgplus decrypts as it reads, so both sides of a comparison are
in the same domain.
"""
from __future__ import annotations

from gen3core import (SECTOR_DATA_SIZE, choose_slot, decrypt_substructures, u16,
                      u32)
from vanilla_read import box_mon_slots

# Fields the specification says must carry unchanged: (key, how to size it).
# The two sb2 fields are read from SaveBlock2; the rest from SaveBlock1.
UNTOUCHED = ("sb1_flags", "sb1_vars", "sb1_game_stats", "sb1_pc_items",
             "sb2_pokedex", "sb2_play_time_hours")


def _region(L, key):
    """(offset, size) of an UNTOUCHED field inside its block, from layout L."""
    sizes = {
        "sb1_flags": L["num_flag_bytes"],
        "sb1_vars": 2 * L["vars_count"],
        "sb1_game_stats": 4 * L["num_game_stats"],
        # Pocket slots are 4 bytes. PC item quantities are stored RAW (not XOR'd)
        # in both games, so a byte comparison is correct here.
        "sb1_pc_items": 4 * L["pc_items_count"],
        # Both DEX_FLAGS_NO arrays, from the header through the end of `seen`.
        "sb2_pokedex": L["sb2_dex_seen"] + L["dex_flag_bytes"] - L["sb2_pokedex"],
        # hours (u16), minutes, seconds: the three fields are contiguous, 0xE-0x11.
        "sb2_play_time_hours": L["sb2_play_time_seconds"] + 1 - L["sb2_play_time_hours"],
    }
    return L[key], sizes[key]


# Every loss kind rules.convert_bag can record. Only these may be subtracted from
# conservation; any other kind is reported rather than silently excused.
DOCUMENTED_LOSS_KINDS = frozenset({"tm_quantity", "key_item_quantity", "pocket_full",
                                   "unmapped_key_item", "unknown_item"})


def item_multiset(pockets):
    """{item id: total quantity} summed across every pocket (PLAINTEXT quantities,
    as read_vanilla returns them). A moved pocket boundary changes this even when
    every individual entry still looks sane."""
    out = {}
    for entries in pockets.values():
        for e in entries:
            out[e["id"]] = out.get(e["id"], 0) + e["qty"]
    return out


def read_frlgplus_sb1(raw):
    """The active slot's SaveBlock1 from a converted image."""
    best, _ = choose_slot(raw)
    return b"".join(best["sections"].get(i, b"\0" * SECTOR_DATA_SIZE) for i in range(1, 5))


def read_frlgplus_pc(raw):
    """The active slot's PokemonStorage from a converted image."""
    best, _ = choose_slot(raw)
    return b"".join(best["sections"].get(i, b"\0" * SECTOR_DATA_SIZE) for i in range(5, 14))


# The only two fields rules 3 and 4 are allowed to change inside a Pokémon: the last
# halfword of substructure G (FRLG+'s boxHP:10, boxStatus:4, forme:2) and byte 1 of
# substructure M (metLocation). Byte 0x1C of the header is the substructure checksum,
# which rule 3 must recompute; everything else, in all 48 plaintext substructure bytes
# and the rest of the header and the party tail, has to come across untouched.
def _pokemon_differences(before, after):
    """How one Pokémon's converted bytes differ from its source's, outside what the
    conversion may write.

    This is the check that can see a fault nothing else can. rewrite_substructures
    recomputes the internal checksum over whatever plaintext it produced and savewrite
    recomputes the sector checksum over whatever bytes it was handed, so a wrong
    substructure order, a wrong offset within G or M, or a truncated slice yields a
    save where every checksum reproduces, items are conserved, the Day Care step probe
    passes and every UNTOUCHED region matches.

    Comparing the PLAINTEXT rather than the stored bytes is deliberate: the ciphertext
    of an unchanged substructure is unchanged only because the key is unchanged, and
    that is a separate fact this would otherwise be silently relying on.
    """
    if len(before) != len(after):
        return [f"its stored size changed from {len(before)} to {len(after)}"]
    out = []
    if before[:0x1C] != after[:0x1C] or before[0x1E:0x20] != after[0x1E:0x20]:
        out.append("its header changed (PID, OT, nickname, language, flags or markings)")
    if before[0x50:] != after[0x50:]:
        out.append("its party tail changed (level, stored HP, status or stats)")
    a_subs, _ = decrypt_substructures(before)
    b_subs, _ = decrypt_substructures(after)
    for name in ("G", "A", "E", "M"):
        x, y = bytearray(a_subs[name]), bytearray(b_subs[name])
        if name == "G":
            x[10:12] = y[10:12] = b"\0\0"     # rule 3: boxHP, boxStatus, forme
        if name == "M":
            x[1] = y[1] = 0                   # rule 4: metLocation
        if bytes(x) != bytes(y):
            out.append(f"substructure {name} changed")
    return out


def compare_every_pokemon(src, sb1, pc, vanilla_L, plus_L):
    """Spec 6.2: every one of the 429 physical slots, source against result.

    The slot inventory comes from the generated layout (vanilla_read.box_mon_slots),
    so a Pokémon the conversion forgot or wrote to the wrong place is a difference
    here rather than something no check addresses. Source and result are paired by
    slot IDENTITY, because the Four Island Day Care sits four bytes earlier in FRLG+.
    """
    problems = []
    before_blocks = {"sb1": src["sb1"], "pc": src["pc"]}
    after_blocks = {"sb1": sb1, "pc": pc}
    after_slots = {ident: (blk, off, size)
                   for ident, _label, blk, off, size in box_mon_slots(plus_L)}
    for ident, label, blk, off, size in box_mon_slots(vanilla_L):
        if ident not in after_slots:
            problems.append(f"{label}: the FRLG+ layout has no such slot")
            continue
        a_blk, a_off, a_size = after_slots[ident]
        before = before_blocks[blk][off:off + size]
        after = after_blocks[a_blk][a_off:a_off + a_size]
        problems += [f"{label}: {why}" for why in _pokemon_differences(before, after)]
    return problems


def item_multiset_frlgplus(raw, plus_L, tf):
    """The same multiset, read out of a converted image through the FRLG+ layout.

    Slot quantities are stored ENCRYPTED (qty ^ key16); they are decrypted here so
    the result is PLAINTEXT, comparable with item_multiset(read_vanilla(...)).
    The TM Case contributes one of each set bit and Key Items one of each distinct
    index (a repeated byte is stale, as the game itself merges it).
    """
    best, _ = choose_slot(raw)
    sb1 = read_frlgplus_sb1(raw)
    sb2 = best["sections"].get(0, b"")
    key16 = u32(sb2, plus_L["sb2_encryption_key"]) & 0xFFFF
    out = {}

    def add(iid, qty):
        out[iid] = out.get(iid, 0) + qty

    for off_key, cnt_key in (("sb1_bag_items", "bag_items_count"),
                             ("sb1_bag_medicine", "bag_medicine_count"),
                             ("sb1_bag_held_items", "bag_held_items_count"),
                             ("sb1_bag_poke_balls", "bag_poke_balls_count"),
                             ("sb1_bag_berries", "bag_berries_count")):
        for k in range(plus_L[cnt_key]):
            iid = u16(sb1, plus_L[off_key] + 4 * k)
            if iid:
                add(iid, u16(sb1, plus_L[off_key] + 4 * k + 2) ^ key16)   # decrypt

    bits = sb1[plus_L["sb1_bag_tmhm_bits"]:plus_L["sb1_bag_tmhm_bits"] + plus_L["bag_tmhm_bytes"]]
    for index, (iid, _move, _name) in enumerate(tf.tmhm):
        if (bits[index // 8] >> (index % 8)) & 1:
            add(iid, 1)

    seen = set()
    for k in range(plus_L["bag_key_items_count"]):
        stored = sb1[plus_L["sb1_bag_key_items"] + k]
        iid = tf.key_item_indices.get(stored) if stored else None
        if iid is not None and iid not in seen:
            seen.add(iid)
            add(iid, 1)
    return out


def verify_conversion(src, out_raw, vanilla_L, plus_L, tv, tf, losses=()):
    # `tv` is reserved: unused today, kept so the signature stays as specified.
    """Every way the result disagrees with the source. Empty means it checks out.

    `losses` are the plan's documented losses; each carries the item id and how
    much of the multiset it removes ("lost"), so the one allowed exception
    (duplicate TM quantities) and the other three kinds are subtracted from what
    the source is expected to yield. An undocumented difference is a problem.
    """
    problems = []
    best, _ = choose_slot(out_raw)
    if not best["valid"]:
        problems.append("the converted save's own slot does not verify: "
                        + "; ".join(best["problems"][:3]))
        return problems
    sb1 = read_frlgplus_sb1(out_raw)
    sb2 = best["sections"].get(0, b"")
    key = u32(sb2, plus_L["sb2_encryption_key"])
    if key != src["key"]:
        problems.append("the encryption key changed")

    if u32(sb1, plus_L["sb1_money"]) ^ key != src["money"]:
        problems.append("money changed")
    if u16(sb1, plus_L["sb1_coins"]) ^ (key & 0xFFFF) != src["coins"]:
        problems.append("coins changed")

    stat = tf.game_stat_id("GAME_STAT_SAVED_GAME")
    if u32(sb1, plus_L["sb1_game_stats"] + 4 * stat) ^ key != best["counter"]:
        problems.append("GAME_STAT_SAVED_GAME no longer matches the sector counter")

    # Conservation: both sides PLAINTEXT.
    expected = item_multiset(src["pockets"])
    for loss in losses:
        iid = loss.get("id")
        if loss.get("kind") not in DOCUMENTED_LOSS_KINDS:
            problems.append(f"unrecognised loss kind {loss.get('kind')!r} for "
                            f"{loss.get('item')}; it is not subtracted from the item count")
            continue
        if iid is not None:
            expected[iid] = expected.get(iid, 0) - loss.get("lost", 0)
    expected = {i: q for i, q in expected.items() if q}
    got = item_multiset_frlgplus(out_raw, plus_L, tf)
    if expected != got:
        diff = sorted(i for i in set(expected) | set(got) if expected.get(i, 0) != got.get(i, 0))
        problems.append("items are not conserved: " + ", ".join(
            f"{tf.item_name(i)} expected {expected.get(i, 0)} got {got.get(i, 0)}"
            for i in diff[:8]) + (" ..." if len(diff) > 8 else ""))

    problems += compare_every_pokemon(src, sb1, read_frlgplus_pc(out_raw),
                                      vanilla_L, plus_L)

    step_src = src["sb1"][vanilla_L["sb1_daycare_step_counter"]]
    step_out = sb1[plus_L["sb1_daycare_step_counter"]]
    if step_out != step_src:
        problems.append(f"the Day Care step counter moved from {step_src} to {step_out}")

    blocks = {"sb1": (src["sb1"], sb1), "sb2": (src["sb2"], sb2)}
    for name in UNTOUCHED:
        which = name[:3]
        a_blk, b_blk = blocks[which]
        a_off, size = _region(vanilla_L, name)
        b_off, size_b = _region(plus_L, name)
        if size != size_b:
            problems.append(f"{name} is a different size in the two layouts")
            continue
        if a_blk[a_off:a_off + size] != b_blk[b_off:b_off + size]:
            problems.append(f"{name} should have carried unchanged and did not")
    return problems
