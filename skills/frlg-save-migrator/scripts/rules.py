"""The seven conversion rules. Each is a pure function over bytes and tables.

Nothing here opens a file or knows what a save slot is; savewrite.py owns that.
Every rule returns the bytes to write plus whatever it could not carry, so the
caller can report losses instead of discovering them later.
"""
from __future__ import annotations

import struct

import frlgplus
import gen3core
from gen3core import u16

# Which FRLG+ ItemSlot pocket each item-data pocket name maps to, and the layout
# keys naming its offset and slot count. tm_case and key_items are not ItemSlot
# arrays and are handled on their own.
SLOT_POCKETS = {
    "items":       ("sb1_bag_items", "bag_items_count"),
    "medicine":    ("sb1_bag_medicine", "bag_medicine_count"),
    "held_items":  ("sb1_bag_held_items", "bag_held_items_count"),
    "poke_balls":  ("sb1_bag_poke_balls", "bag_poke_balls_count"),
    "berry_pouch": ("sb1_bag_berries", "bag_berries_count"),
}


def convert_bag(src_pockets, plus_L, tables):
    """Re-pocket a vanilla bag into FRLG+'s rebuilt one.

    Quantities in and out are PLAINTEXT: read_vanilla has already XOR'd them with
    the key, and the bytes returned here are not yet encrypted. encrypt_quantities
    applies the key (SaveBlock2 is untouched, so it is the same on both sides).
    """
    losses = []
    buckets = {name: [] for name in SLOT_POCKETS}
    tm_bits = bytearray(plus_L["bag_tmhm_bytes"])
    key_bytes = bytearray(plus_L["bag_key_items_count"])
    key_next = 0
    index_of_item = {iid: stored for stored, iid in tables.key_item_indices.items()}
    tm_index = {item_id: i for i, (item_id, _m, _n) in enumerate(tables.tmhm)}

    for pocket_name in ("items", "key_items", "poke_balls", "tmhm", "berries"):
        for entry in src_pockets.get(pocket_name, []):
            iid, qty, dest = entry["id"], entry["qty"], entry["frlgplus_pocket"]
            name = entry.get("name") or tables.item_name(iid)

            if dest == "tm_case":
                bit = tm_index.get(iid)
                if bit is None:
                    losses.append({"kind": "unknown_item", "item": name, "id": iid, "lost": qty,
                                   "detail": "routed to the TM Case but not a TM or HM "
                                             "FRLG+ knows"})
                    continue
                tm_bits[bit // 8] |= 1 << (bit % 8)
                if qty > 1:
                    losses.append({"kind": "tm_quantity", "item": name, "id": iid, "lost": qty - 1,
                                   "detail": f"had {qty}; the TM Case stores one bit "
                                             f"per TM, so {qty - 1} could not carry"})
                continue

            if dest == "key_items":
                stored = index_of_item.get(iid)
                if stored is None:
                    losses.append({"kind": "unmapped_key_item", "item": name, "id": iid, "lost": qty,
                                   "detail": "no FRLG+ key-item index for this item"})
                    continue
                if key_next >= len(key_bytes):
                    losses.append({"kind": "pocket_full", "item": name, "id": iid, "lost": qty,
                                   "detail": f"Key Items is full; it holds "
                                             f"{plus_L['bag_key_items_count']}"})
                    continue
                key_bytes[key_next] = stored
                key_next += 1
                if qty > 1:
                    # One index byte per key item: the pocket has no quantity.
                    losses.append({"kind": "key_item_quantity", "item": name,
                                   "id": iid, "lost": qty - 1,
                                   "detail": f"had {qty}; Key Items stores one byte per "
                                             f"item, so {qty - 1} could not carry"})
                continue

            if dest not in SLOT_POCKETS:
                losses.append({"kind": "unknown_item", "item": name, "id": iid, "lost": qty,
                               "detail": f"FRLG+ has no pocket for it (item data "
                                         f"says {dest!r})"})
                continue
            buckets[dest].append((iid, qty))

    writes = {"sb1_bag_tmhm_bits": bytes(tm_bits),
              "sb1_bag_key_items": bytes(key_bytes)}
    for dest, (off_key, cnt_key) in SLOT_POCKETS.items():
        slots = plus_L[cnt_key]
        buf = bytearray(4 * slots)
        for k, (iid, qty) in enumerate(buckets[dest][:slots]):
            struct.pack_into("<HH", buf, 4 * k, iid, qty)
        for iid, qty in buckets[dest][slots:]:
            losses.append({"kind": "pocket_full", "item": tables.item_name(iid),
                          "id": iid, "lost": qty,
                           "detail": f"this stack of {qty} did not fit: FRLG+'s "
                                     f"{dest} pocket holds {slots}"})
        writes[off_key] = bytes(buf)
    return writes, losses


def encrypt_quantities(writes, plus_L, key16):
    """XOR every occupied slot's quantity in the ItemSlot pockets with the low half of
    the save's encryption key, as the game stores them. Empty slots (id 0) stay zero.
    convert_bag returns plaintext because read_vanilla hands it plaintext; writing that
    straight into the save would make FRLG+ decode every stack as quantity ^ key."""
    out = dict(writes)
    for off_key, _cnt in SLOT_POCKETS.values():
        buf = bytearray(out[off_key])
        for k in range(0, len(buf), 4):
            iid, qty = struct.unpack_from("<HH", buf, k)
            if iid:
                struct.pack_into("<H", buf, k + 2, qty ^ key16)
        out[off_key] = bytes(buf)
    return out


# ---------------------------------------------------------------- rule 2: daycare
def convert_daycare(src_sb1, vanilla_L, plus_L, converted_mons):
    """Re-pack the Four Island Day Care. The DaycareMon structures are byte-identical
    between the games; only offspringPersonality widens from u16 to u32, which is
    what moved the whole struct 4 bytes earlier in FRLG+.

    `converted_mons` is one entry per Four Island slot, in slot order, each either the
    converted 80-byte box form from convert_mons or None for a slot that must keep the
    source's bytes (empty, an egg, or a Pokemon whose checksum did not verify). They
    are spliced into the copied block rather than written separately, so the Day Care
    produces exactly ONE span in the write set and nothing double-writes those bytes.
    """
    stride, count = vanilla_L["daycare_mon_size"], vanilla_L["daycare_mon_count"]
    if len(converted_mons) != count:
        raise ValueError(f"the Four Island Day Care has {count} slots but "
                         f"{len(converted_mons)} converted Pokemon were supplied")
    mons = bytearray(src_sb1[vanilla_L["sb1_daycare"]:
                             vanilla_L["sb1_daycare"] + stride * count])
    for k, mon80 in enumerate(converted_mons):
        if mon80 is None:
            continue
        mons[stride * k:stride * k + len(mon80)] = mon80
    offspring = u16(src_sb1, vanilla_L["sb1_daycare_offspring"])
    step = src_sb1[vanilla_L["sb1_daycare_step_counter"]]
    return {"sb1_daycare": bytes(mons),
            "sb1_daycare_offspring": struct.pack("<I", offspring),
            "sb1_daycare_step_counter": bytes([step])}


# ------------------------------------------- rules 3 and 4 (per Pokemon), together
# The width of FRLG+'s boxHP bitfield, NOT the game's own 714 HP cap (which
# extras/fix_boxed_hp.py calls MAX_LEGAL_BOX_HP). No legal Pokemon reaches either,
# so this only stops a hand-edited or glitched stat from overflowing into boxStatus.
BOX_HP_FIELD_MAX = 0x3FF
DEOXYS_FORME_BY_VERSION = {"fr": 1, "lg": 2}      # Attack in FireRed, Defense in LeafGreen


def convert_mons(src, plus_L, tables, *, source_version):
    """Write boxHP/boxStatus/forme and remap metLocation, one decrypt per Pokemon.

    EVERY Pokemon in the save, which is 429 and not 426: `out["party"]` and
    `out["boxes"]` mirror the source's shape, and `out["day_care"]` carries the three
    struct BoxPokemon the two Day Cares hold, keyed by the identity
    vanilla_read.box_mon_slots gives them. None marks an empty slot and the ORIGINAL
    bytes any slot that was skipped, so a writer can index all of them by PHYSICAL
    slot. Nothing is ever compacted: shifting a party slot would hand the game a
    different party, and a Day Care slot is addressed by its own offset.
    """
    if source_version not in DEOXYS_FORME_BY_VERSION:
        raise ValueError(f"source_version must be 'fr' or 'lg', got {source_version!r}")
    remaps = src["remaps"]
    skipped, decisions = [], []

    def one(mon, raw80):
        if mon is None:
            return None
        where = mon.get("where", "?")
        label = f"{where}: {mon.get('nickname') or mon.get('species')}"
        if mon.get("is_egg"):
            skipped.append(f"{label} — egg, left untouched")
            return raw80
        if mon["checks"]["checksum"] != "ok":
            skipped.append(f"{label} — substructure checksum failed, left untouched")
            return raw80
        max_hp = (mon.get("stats") or {}).get("HP")
        if not max_hp:
            skipped.append(f"{label} — no max HP could be computed, left untouched")
            return raw80
        forme = 0
        if mon.get("species") == "Deoxys":
            forme = DEOXYS_FORME_BY_VERSION[source_version]
            decisions.append(f"{label} — Deoxys forme set to "
                             f"{gen3core.DEOXYS_FORMES[forme]} for a "
                             f"{'FireRed' if source_version == 'fr' else 'LeafGreen'} "
                             f"source")
        old_loc = mon["origin"]["met_location_id"]
        new_loc = remaps["mapsec"].get(old_loc)
        if new_loc is None:
            new_loc = old_loc          # METLOC specials and anything with no row
        return gen3core.rewrite_substructures(
            raw80,
            g_halfword=frlgplus.encode_box_padding(
                min(max_hp, BOX_HP_FIELD_MAX), 0, forme),
            met_location=new_loc)

    party = [one(m, src["party_raw"][i]) for i, m in enumerate(src["party"])]
    boxes = [[one(m, src["boxes_raw"][bx][sl]) for sl, m in enumerate(row)]
             for bx, row in enumerate(src["boxes"])]
    # The remaining three: the Route 5 Day Care's one Pokemon and the Four Island Day
    # Care's two, each a struct BoxPokemon at offset 0 of a struct DaycareMon. Keyed
    # by the identity vanilla_read.box_mon_slots gives them, so the writer places each
    # back by generated offset rather than by position in a list.
    day_care = {entry["id"]: one(entry["mon"], entry["raw"])
                for entry in src["day_care"]}
    return ({"party": party, "boxes": boxes, "day_care": day_care},
            skipped, decisions)


# ------------------------------------------------------- rule 4 (SaveBlock1 fields)
WARP_KEYS = ("sb1_location", "sb1_continue_game_warp", "sb1_dynamic_warp",
             "sb1_last_heal_location", "sb1_escape_warp")


def remap_ids(src_sb1, vanilla_L, plus_L, remaps):
    """Rewrite every stored map, layout and quest-log id. Offsets are identical in
    both layouts for all of these, so only the VALUES change."""
    writes, notes = {}, []
    for key in WARP_KEYS:
        off = vanilla_L[key]
        group, num = src_sb1[off], src_sb1[off + 1]
        rest = src_sb1[off + 2:off + 8]
        new = remaps["maps"].get((group, num))
        if new is None:
            notes.append(f"{key}: map ({group}, {num}) has no remap row; left as is")
            new = (group, num)
        writes[key] = bytes([new[0] & 0xFF, new[1] & 0xFF]) + rest

    old_layout = u16(src_sb1, vanilla_L["sb1_map_layout_id"])
    new_layout = remaps["layouts"].get(old_layout)
    if new_layout is None:
        notes.append(f"mapLayoutId {old_layout} has no remap row "
                     f"(vanilla's empty layout slots have none); left as is")
        new_layout = old_layout
    writes["sb1_map_layout_id"] = struct.pack("<H", new_layout)

    stride, count = vanilla_L["quest_log_scene_size"], vanilla_L["quest_log_scene_count"]
    base = vanilla_L["sb1_quest_log"]
    scenes = bytearray(src_sb1[base:base + stride * count])
    for i in range(count):
        o = i * stride
        group, num = scenes[o + 1], scenes[o + 2]
        if (group, num) == (0, 0):
            continue
        new = remaps["maps"].get((group, num))
        if new is None:
            notes.append(f"quest log scene {i}: map ({group}, {num}) has no remap row")
            continue
        scenes[o + 1], scenes[o + 2] = new[0] & 0xFF, new[1] & 0xFF
    writes["sb1_quest_log"] = bytes(scenes)

    # Spec 7: saved object-event state belongs to the map the player is standing
    # on. If FRLG+ changed that map's object list, say so — it is the residual
    # risk the skill cannot fix, only report.
    here = (src_sb1[vanilla_L["sb1_location"]], src_sb1[vanilla_L["sb1_location"] + 1])
    name = next((n for n, (a, _b) in remaps.get("maps_by_name", {}).items()
                 if a == here), None)
    changed = remaps.get("object_counts", {})
    if name and name in changed:
        a, b = changed[name]
        notes.append(f"the saved map {name} has {a} object events in vanilla and "
                     f"{b} in FRLG+, so saved object state for it may not line up "
                     f"(converting from a Pokemon Center or the player's bedroom "
                     f"avoids this)")
    return writes, notes


# ------------------------------------------------------------- rule 5: key flags
def build_key_flags(source_version):
    """struct KeySystemFlags: difficulty:2, version:1, nuzlocke:1, ivCalcMode:2,
    evCalcMode:1, noPMC:1, expMod:2, padding:4, changedCalcMode:1, inKeySystemMenu:1.

    Everything defaults to 0 except expMod, which new_game.c:157 sets to 2. A
    zero-filled word means expMod == 0, which battle_script_commands.c:3249 reads
    as no EXP, ever — a save where nothing ever levels.
    """
    if source_version not in ("fr", "lg"):
        raise ValueError(f"source_version must be 'fr' or 'lg', got {source_version!r}")
    return (2 << 8) | ((1 if source_version == "lg" else 0) << 2)


# -------------------------------------------------- rule 6: zero the carved regions
def zeroed_regions(plus_L):
    """FRLG+ reads these from space vanilla leaves as filler, so a vanilla save's
    bytes there must not be inherited as if they were FRLG+ data.

    The Master Trainer bitfield's length is generated (master_trainer_flags_bytes,
    the distance from unused_3A94 + 44 to registeredTexts). The other two array
    lengths (52 and 36) are literals because the layout emits those regions' OFFSETS
    but not their lengths; tests/test_rules.py pins each against the distance to the
    field that follows it in the generated layout, so a size that drifted out of step
    with the source fails there rather than silently zeroing the wrong span.
    """
    leftover = plus_L["sb1_item_block_end"] - (plus_L["sb1_bag_held_items"]
                                               + 4 * plus_L["bag_held_items_count"])
    return {"sb1_master_trainer_title": bytes(1),
            "sb1_last_viewed_pokedex_entry": bytes(2),
            "sb1_nuzlocke_dupe_flags": bytes(52),
            "sb1_master_trainer_flags": bytes(plus_L["master_trainer_flags_bytes"]),
            "sb1_filler_easy_chat": bytes(36),
            "__leftover_item_slots": bytes(leftover)}
