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

from gen3core import (SECTOR_DATA_SIZE, choose_slot, decode_pokemon, decode_text,
                      u16, u32)
import vanilla_frlg

VANILLA_POCKETS = ("items", "key_items", "poke_balls", "tmhm", "berries")
PARTY_MON_SIZE = 100
BOX_MON_SIZE = 80
MAX_PARTY = 6


def read_vanilla(raw, tables):
    L = tables.layout
    best, _other = choose_slot(raw)
    slot = dict(best)
    slot["index"] = 0 if best["slot"] == "A" else 1
    secs = best["sections"]
    blank = b"\0" * SECTOR_DATA_SIZE
    sb2 = secs.get(0, blank)
    sb1 = b"".join(secs.get(i, blank) for i in range(1, 5))
    pc = b"".join(secs.get(i, blank) for i in range(5, 14))
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
        if m:
            party.append(m)
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
            "party_raw": party_raw, "boxes_raw": boxes_raw,
            "remaps": vanilla_frlg.remaps(tables.data_dir)}
