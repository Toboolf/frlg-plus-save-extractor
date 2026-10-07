"""Put edited blocks back into sectors and re-checksum them.

Knows sectors and checksums; knows nothing about fields. The section sizes come
from the generated layout — SaveBlock1 and SaveBlock2's sizes are generated keys,
not literals, because a hand-written 0x3D68 is exactly the kind of number this
repo refuses to hand-write.
"""
from __future__ import annotations

import struct

from gen3core import (SAVE_SIGNATURE, SECTOR_DATA_SIZE, SECTOR_SIZE, SECTORS_PER_SLOT)


def _chunks(total, count):
    """How the game splits one struct across `count` sectors."""
    out, left = [], total
    for _ in range(count):
        take = min(SECTOR_DATA_SIZE, left)
        out.append(take)
        left -= take
    return out


def section_sizes(plus_L):
    sizes = {0: plus_L["sb2_size"]}
    for i, size in enumerate(_chunks(plus_L["sb1_size"], 4)):
        sizes[1 + i] = size
    for i, size in enumerate(_chunks(plus_L["pc_end"], 9)):
        sizes[5 + i] = size
    return sizes


def sector_checksum(data, size):
    total = 0
    for i in range(0, size, 4):
        total = (total + struct.unpack_from("<I", data, i)[0]) & 0xFFFFFFFF
    return ((total >> 16) + (total & 0xFFFF)) & 0xFFFF


def verify_all_checksums(raw, sizes):
    bad = []
    for slot in range(2):
        for phys in range(SECTORS_PER_SLOT):
            off = (slot * SECTORS_PER_SLOT + phys) * SECTOR_SIZE
            chunk = raw[off:off + SECTOR_SIZE]
            sid, stored, sig, _ = struct.unpack_from("<HHII", chunk, 0xFF4)
            if sig != SAVE_SIGNATURE or sid not in sizes:
                continue
            if sector_checksum(chunk, sizes[sid]) != stored:
                bad.append(f"slot {slot} sector {phys} (section {sid})")
    return bad


def apply_writes(raw, slot, plus_L, sb1, pc):
    """Write sb1 and pc back into the active slot's sectors and re-checksum them."""
    out = bytearray(raw)
    sizes = section_sizes(plus_L)
    phys_of_section = {info["section_id"]: info["physical_sector"]
                       for info in slot["sector_info"] if info.get("signature_ok")}
    for i, sid in enumerate(range(1, 5)):
        _put(out, phys_of_section, sid, sizes,
             sb1[i * SECTOR_DATA_SIZE:(i + 1) * SECTOR_DATA_SIZE])
    for i, sid in enumerate(range(5, 14)):
        _put(out, phys_of_section, sid, sizes,
             pc[i * SECTOR_DATA_SIZE:(i + 1) * SECTOR_DATA_SIZE])
    return bytes(out)


def _put(out, phys_of_section, sid, sizes, data):
    if sid not in phys_of_section:
        raise ValueError(f"section {sid} is not present in the active slot")
    base = phys_of_section[sid] * SECTOR_SIZE
    chunk = bytearray(out[base:base + SECTOR_SIZE])
    chunk[:SECTOR_DATA_SIZE] = data.ljust(SECTOR_DATA_SIZE, b"\0")[:SECTOR_DATA_SIZE]
    struct.pack_into("<H", chunk, 0xFF6, sector_checksum(chunk, sizes[sid]))
    out[base:base + SECTOR_SIZE] = chunk
