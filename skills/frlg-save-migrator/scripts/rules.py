"""The seven conversion rules. Each is a pure function over bytes and tables.

Nothing here opens a file or knows what a save slot is; savewrite.py owns that.
Every rule returns the bytes to write plus whatever it could not carry, so the
caller can report losses instead of discovering them later.
"""
from __future__ import annotations

import struct

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

    Quantities are copied as stored. SaveBlock2 is never touched by the migration,
    so the encryption key is the same on both sides and an already-XOR'd quantity
    stays correct without being decrypted and re-encrypted.
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
                    losses.append({"kind": "unknown_item", "item": name,
                                   "detail": "routed to the TM Case but not a TM or HM "
                                             "FRLG+ knows"})
                    continue
                tm_bits[bit // 8] |= 1 << (bit % 8)
                if qty > 1:
                    losses.append({"kind": "tm_quantity", "item": name,
                                   "detail": f"had {qty}; the TM Case stores one bit "
                                             f"per TM, so {qty - 1} could not carry"})
                continue

            if dest == "key_items":
                stored = index_of_item.get(iid)
                if stored is None:
                    losses.append({"kind": "unmapped_key_item", "item": name,
                                   "detail": "no FRLG+ key-item index for this item"})
                    continue
                if key_next >= len(key_bytes):
                    losses.append({"kind": "pocket_full", "item": name,
                                   "detail": f"Key Items is full; it holds "
                                             f"{plus_L['bag_key_items_count']}"})
                    continue
                key_bytes[key_next] = stored
                key_next += 1
                continue

            if dest not in SLOT_POCKETS:
                losses.append({"kind": "unknown_item", "item": name,
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
                           "detail": f"this stack of {qty} did not fit: FRLG+'s "
                                     f"{dest} pocket holds {slots}"})
        writes[off_key] = bytes(buf)
    return writes, losses
