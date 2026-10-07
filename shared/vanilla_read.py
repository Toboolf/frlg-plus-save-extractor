"""Read a vanilla FireRed/LeafGreen save — only what a migration needs.

frlgplus.extract produces the FRLG+ report: Key System, Master Trainers, the
roaming legendary, fifteen narrative sections. A conversion needs none of it,
so this is the smaller thing: the active slot's blocks, the player, the money,
the bag, the party and boxes, and the one cross-check that proves the layout and
the key are both right.

Every offset comes from the generated vanilla layout (tables.layout); none is
written here.
"""
from __future__ import annotations

from gen3core import (choose_slot, decode_pokemon, decode_text,
                      u16, u32)
import vanilla_frlg

VANILLA_POCKETS = ("items", "key_items", "poke_balls", "tmhm", "berries")
PARTY_MON_SIZE = 100
BOX_MON_SIZE = 80
MAX_PARTY = 6


def daycare_mon_slots(L):
    """The Day Care's `struct BoxPokemon`s: (identity, label, SaveBlock1 offset).

    `struct DaycareMon` puts `struct BoxPokemon mon` at offset 0, so each stored
    Pokémon is an 80-byte box form at a generated offset: the Route 5 Day Care's one
    mon, then the Four Island Day Care's `daycare_mon_count`. Everything after those
    80 bytes (the mail and the step count) belongs to the Day Care, not the Pokémon.
    """
    slots = [("route_5", "Route 5 Day Care", L["sb1_route5_daycare_mon"])]
    for k in range(L["daycare_mon_count"]):
        slots.append((f"four_island_{k + 1}", f"Four Island Day Care slot {k + 1}",
                      L["sb1_daycare"] + L["daycare_mon_size"] * k))
    return slots


def box_mon_slots(L):
    """Every `struct BoxPokemon` a FireRed/LeafGreen save can hold, as
    (identity, label, block, offset within that block).

    429 of them: MAX_PARTY party slots (whose first 80 bytes are the box form),
    `total_boxes` x `in_box_count` PC slots, and the three Day Care slots. The list
    is derived from the generated layout so that "every Pokémon in the save" is a
    property of the table rather than a sentence in a comment — a count that changed
    or a region that moved shows up here instead of leaving Pokémon unconverted.
    """
    slots = [(f"party_{i + 1}", f"party {i + 1}", "sb1",
              L["sb1_party"] + PARTY_MON_SIZE * i) for i in range(MAX_PARTY)]
    for bx in range(L["total_boxes"]):
        for sl in range(L["in_box_count"]):
            slots.append((f"box_{bx + 1}_{sl + 1}", f"box {bx + 1} slot {sl + 1}", "pc",
                          L["pc_boxes"] + (bx * L["in_box_count"] + sl) * BOX_MON_SIZE))
    slots += [(ident, label, "sb1", off) for ident, label, off in daycare_mon_slots(L)]
    return slots


def read_vanilla(raw, tables):
    """Parse the active slot. Returns the dict described in the migrator's plan.

    Indexing: `party` and `party_raw` are both exactly `partyCount` long and indexed by
    PHYSICAL party slot. `party_raw[i]` is always the 100 bytes at that slot, and
    `party[i]` is its decoded Pokémon, or None if the slot is inside partyCount but decodes
    empty (an all-zero first 80 bytes). Nothing is compacted, so a writer that does
    `sb1[sb1_party + 100*i]` over either list lands on the right slot. `boxes`/`boxes_raw`
    work the same way: 14 x 30, None for an empty slot, raw bytes always kept.
    `day_care` is the remaining three slots (box_mon_slots explains why 429, not 426):
    one dict per `struct DaycareMon`, carrying its identity, label, SaveBlock1 offset,
    raw 80 bytes and decoded Pokémon (None when the slot is empty).

    Refuses (ValueError) when any of the 14 sections is missing from the chosen slot: the
    encryption key lives in section 0 and the money, bag and party in 1-4, so zero-filling
    a gap would silently hand back key 0 and values decoded from zeros. Checksum or
    signature problems on sections that are present are NOT refused; they are reported in
    slot["valid"] and slot["problems"], and callers decide whether to proceed.
    """
    L = tables.layout
    best, _other = choose_slot(raw)
    absent = [i for i in range(14) if i not in best["sections"]]
    if absent:
        raise ValueError(f"save slot {best['slot']} is missing sections {absent}: "
                         f"{'; '.join(best['problems'])}")
    slot = dict(best)
    slot["index"] = 0 if best["slot"] == "A" else 1
    secs = best["sections"]
    sb2 = secs[0]
    sb1 = b"".join(secs[i] for i in range(1, 5))
    pc = b"".join(secs[i] for i in range(5, 14))
    key = u32(sb2, L["sb2_encryption_key"])
    key16 = key & 0xFFFF

    name_off = L["sb2_player_name"]
    player = {"name": decode_text(sb2[name_off:name_off + 8]),
              "gender": "F" if sb2[L["sb2_player_gender"]] else "M",
              "tid": u16(sb2, L["sb2_trainer_id"]),
              "sid": u16(sb2, L["sb2_trainer_id"] + 2),
              "play_time": (u16(sb2, L["sb2_play_time_hours"]),
                            sb2[L["sb2_play_time_minutes"]],
                            sb2[L["sb2_play_time_seconds"]])}

    party, party_raw = [], []
    count = min(sb1[L["sb1_party_count"]], MAX_PARTY)
    for i in range(count):
        o = L["sb1_party"] + PARTY_MON_SIZE * i
        chunk = sb1[o:o + PARTY_MON_SIZE]
        m = decode_pokemon(chunk, tables, player, party=True, where=f"party {i + 1}",
                           profile=vanilla_frlg)
        party.append(m)                   # None for a hole: keep physical slot indexing
        party_raw.append(bytes(chunk))

    boxes, boxes_raw = [], []
    for bx in range(L["total_boxes"]):
        slots, raws = [], []
        for sl in range(L["in_box_count"]):
            o = L["pc_boxes"] + (bx * L["in_box_count"] + sl) * BOX_MON_SIZE
            chunk = pc[o:o + BOX_MON_SIZE]
            raws.append(bytes(chunk))
            slots.append(decode_pokemon(chunk, tables, player,
                                        where=f"box {bx + 1} slot {sl + 1}",
                                        profile=vanilla_frlg))
        boxes.append(slots)
        boxes_raw.append(raws)

    # The three Day Care Pokémon. Indexed by IDENTITY rather than position, because
    # the two Day Cares are two separate regions; `offset` is where the 80-byte box
    # form lives in SaveBlock1, which is the same in both layouts for Route 5 and
    # four bytes later in FRLG+ for Four Island.
    day_care = []
    for ident, label, off in daycare_mon_slots(L):
        chunk = sb1[off:off + BOX_MON_SIZE]
        day_care.append({"id": ident, "where": label, "offset": off,
                         "raw": bytes(chunk),
                         "mon": decode_pokemon(chunk, tables, player, where=label,
                                               profile=vanilla_frlg)})

    pockets = {}
    for name in VANILLA_POCKETS:
        off, cnt = L[f"sb1_bag_{name}"], L[f"bag_{name}_count"]
        entries = []
        for k in range(cnt):
            iid = u16(sb1, off + 4 * k)
            if iid == 0:
                continue
            entries.append({"slot": k, "id": iid,
                            "qty": u16(sb1, off + 4 * k + 2) ^ key16,
                            "name": tables.item_name(iid),
                            "frlgplus_pocket": tables.item_pocket(iid)})
        pockets[name] = entries

    stat_idx = tables.game_stat_id("GAME_STAT_SAVED_GAME")
    return {"slot": slot, "sb1": sb1, "sb2": sb2, "pc": pc, "key": key,
            "player": player, "party": party, "boxes": boxes, "pockets": pockets,
            "money": u32(sb1, L["sb1_money"]) ^ key,
            "coins": u16(sb1, L["sb1_coins"]) ^ key16,
            "saved_game_stat": u32(sb1, L["sb1_game_stats"] + 4 * stat_idx) ^ key,
            "party_raw": party_raw, "boxes_raw": boxes_raw, "day_care": day_care,
            "remaps": vanilla_frlg.remaps(tables.data_dir)}
