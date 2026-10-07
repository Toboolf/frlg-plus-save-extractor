#!/usr/bin/env python3
"""Build synthetic VANILLA FireRed saves, byte-exact, for the migrator's tests.

Nothing here comes from a real save: every trainer, Pokémon and item is invented.
The sectors are laid out, rotated and checksummed the way the game does it, so the
reader and the rules are exercised against the real format rather than a mock.

Every offset is read from the generated vanilla layout (L). None is written here:
a fixture that hard-coded offsets would keep passing if the real layout moved.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from gen3core import (SAVE_SIGNATURE, SECTOR_DATA_SIZE, SECTOR_SIZE, SECTORS_PER_SLOT,  # noqa: E402
                      SUB_ORDERS, Tables, calc_stats, encode_text, exp_for_level)
import vanilla_frlg  # noqa: E402

T = Tables(layout=vanilla_frlg.LAYOUT)
L = T.layout
KEY = 0x0BADC0DE
KEY16 = KEY & 0xFFFF
TID, SID = 31415, 26535      # a 16-bit secret ID: the brief's 92653 does not fit one
OTID = TID | (SID << 16)


def mon(species, level, *, pid=0x11223344, otid=OTID, ot="ASH", nick=None,
        ivs=(20, 21, 22, 23, 24, 25), evs=(0,) * 6, met=88, met_level=5, ball=4,
        game=4, is_egg=False, party=True, bad_checksum=False):
    """One vanilla Pokémon. met defaults to 88 = MAPSEC_PALLET_TOWN; game 4 = FireRed.

    The last halfword of substructure G stays ZERO: in vanilla it is padding.
    """
    rec = T.by_name[species]
    exp = exp_for_level(rec["growth"], level)
    g = struct.pack("<HHIBBH", rec["internal"], 0, exp, 0, 70, 0)   # trailing 0 = padding
    a = struct.pack("<4H4B", 0, 0, 0, 0, 0, 0, 0, 0)
    e = bytes(evs) + bytes(6)
    origins = met_level | (game << 7) | (ball << 11)
    ivw = sum(v << (5 * i) for i, v in enumerate(ivs)) | (int(is_egg) << 30)
    m = struct.pack("<BBHII", 0, met, origins, ivw, 0)
    subs = {"G": g, "A": a, "E": e, "M": m}
    plain = b"".join(subs[c] for c in SUB_ORDERS[pid % 24])
    checksum = sum(struct.unpack("<24H", plain)) & 0xFFFF
    if bad_checksum:
        checksum ^= 0xFFFF
    key = pid ^ otid
    enc = b"".join(struct.pack("<I", struct.unpack_from("<I", plain, i)[0] ^ key)
                   for i in range(0, 48, 4))
    flags = 0x02 | (0x04 if is_egg else 0)       # hasSpecies, isEgg
    head = (struct.pack("<II", pid, otid) + encode_text(nick or species.upper(), 10)
            + bytes([2, flags]) + encode_text(ot, 7) + bytes([0])
            + struct.pack("<HH", checksum, 0))
    box = head + enc
    assert len(box) == 80, len(box)
    if not party:
        return box
    stats = calc_stats(rec["base"], list(ivs), list(evs), level, pid % 25)
    return box + struct.pack("<IBBHHHHHHH", 0, level, 0, stats[0], stats[0],
                             stats[1], stats[2], stats[3], stats[4], stats[5])


def build_vanilla_save(*, save_counter=3, money=3000, coins=0, party=(), boxes=None,
                       items=(), key_items=(), poke_balls=(), tmhm=(), berries=(),
                       player="ASH", map_group=3, map_num=0, layout_id=1,
                       warp_id=7, warp_x=13, warp_y=21,
                       daycare_step=0, daycare_offspring=0, daycare_mons=b"",
                       route5_daycare_mon=b"", one_slot_only=False):
    """A 128 KB vanilla save. Pocket arguments are lists of (item id, quantity)."""
    sb1 = bytearray(L["sb1_size"])
    sb2 = bytearray(L["sb2_size"])
    pc = bytearray(L["pc_end"])

    struct.pack_into("<I", sb2, L["sb2_encryption_key"], KEY)
    sb2[L["sb2_player_name"]:L["sb2_player_name"] + 8] = encode_text(player, 8)
    struct.pack_into("<HH", sb2, L["sb2_trainer_id"], TID, SID)
    struct.pack_into("<H", sb2, L["sb2_play_time_hours"], 12)

    struct.pack_into("<I", sb1, L["sb1_money"], money ^ KEY)
    struct.pack_into("<H", sb1, L["sb1_coins"], coins ^ KEY16)
    # Every game-stat slot is stored XOR'd, zeros included; "times saved" equals the
    # sector counter, which is the cross-check the reader's test asserts.
    for i in range(L["num_game_stats"]):
        struct.pack_into("<I", sb1, L["sb1_game_stats"] + 4 * i, 0 ^ KEY)
    stat_idx = T.game_stat_id("GAME_STAT_SAVED_GAME")
    struct.pack_into("<I", sb1, L["sb1_game_stats"] + 4 * stat_idx, save_counter ^ KEY)

    # Where the player is standing, and the warps the migrator remaps. struct WarpData
    # is {s8 mapGroup, mapNum, warpId; s16 x, y} — 8 bytes, identical in both games.
    # warpId/x/y are non-zero on purpose: only mapGroup and mapNum are remapped, and
    # a rule that rewrote the whole struct would lose the player's position in the map.
    for key in ("sb1_location", "sb1_continue_game_warp", "sb1_dynamic_warp",
                "sb1_last_heal_location", "sb1_escape_warp"):
        struct.pack_into("<BBBxhh", sb1, L[key], map_group & 0xFF, map_num & 0xFF,
                         warp_id & 0xFF, warp_x, warp_y)
    struct.pack_into("<H", sb1, L["sb1_map_layout_id"], layout_id)
    sb1[L["sb1_daycare_step_counter"]] = daycare_step
    struct.pack_into("<H", sb1, L["sb1_daycare_offspring"], daycare_offspring)
    sb1[L["sb1_daycare"]:L["sb1_daycare"] + len(daycare_mons)] = daycare_mons
    # The Route 5 Day Care is a lone struct DaycareMon, so its stored Pokemon is the
    # 80-byte box form at offset 0 of it.
    sb1[L["sb1_route5_daycare_mon"]:
        L["sb1_route5_daycare_mon"] + len(route5_daycare_mon)] = route5_daycare_mon

    sb1[L["sb1_party_count"]] = len(party)
    for i, m in enumerate(party):
        o = L["sb1_party"] + 100 * i
        sb1[o:o + 100] = m

    for name, entries in (("items", items), ("key_items", key_items),
                          ("poke_balls", poke_balls), ("tmhm", tmhm),
                          ("berries", berries)):
        off = L[f"sb1_bag_{name}"]
        for k, (iid, qty) in enumerate(entries):
            struct.pack_into("<HH", sb1, off + 4 * k, iid, qty ^ KEY16)

    for bx, slots in enumerate(boxes or []):
        for sl, m in enumerate(slots):
            if m is None:
                continue
            o = L["pc_boxes"] + (bx * L["in_box_count"] + sl) * 80
            pc[o:o + 80] = m

    return _assemble(bytes(sb1), bytes(sb2), bytes(pc), save_counter, one_slot_only)


def _sections(sb1, sb2, pc):
    """Section id -> its 0xF80-byte data area: 0 is SaveBlock2, 1-4 SaveBlock1, 5-13 storage."""
    def padded(blob, n):
        assert len(blob) <= n * SECTOR_DATA_SIZE, (len(blob), n)
        return blob.ljust(n * SECTOR_DATA_SIZE, b"\0")
    sb1, pc = padded(sb1, 4), padded(pc, 9)
    secs = {0: sb2.ljust(SECTOR_DATA_SIZE, b"\0")}
    for i in range(4):
        secs[1 + i] = sb1[i * SECTOR_DATA_SIZE:(i + 1) * SECTOR_DATA_SIZE]
    for i in range(9):
        secs[5 + i] = pc[i * SECTOR_DATA_SIZE:(i + 1) * SECTOR_DATA_SIZE]
    return secs


def _write_slot(raw, slot, secs, counter, rot):
    """Lay one slot's 14 sectors out rotated by `rot`, each checksummed as the game does."""
    for phys in range(SECTORS_PER_SLOT):
        sid = (phys + rot) % SECTORS_PER_SLOT
        data = secs[sid]
        total = sum(struct.unpack(f"<{SECTOR_DATA_SIZE // 4}I", data)) & 0xFFFFFFFF
        chk = ((total >> 16) + (total & 0xFFFF)) & 0xFFFF
        sector = (data + bytes(SECTOR_SIZE - SECTOR_DATA_SIZE - 12)
                  + struct.pack("<HHII", sid, chk, SAVE_SIGNATURE, counter))
        assert len(sector) == SECTOR_SIZE
        base = (slot * SECTORS_PER_SLOT + phys) * SECTOR_SIZE
        raw[base:base + SECTOR_SIZE] = sector


def _assemble(sb1, sb2, pc, counter, one_slot_only):
    """Slot A is the live save. Unless one_slot_only, slot B holds the same blocks one
    save older, rotated differently, so choosing between the slots is really exercised."""
    raw = bytearray(b"\xFF" * (2 * SECTORS_PER_SLOT * SECTOR_SIZE))
    secs = _sections(sb1, sb2, pc)
    _write_slot(raw, 0, secs, counter, 9)
    if one_slot_only:
        base = SECTORS_PER_SLOT * SECTOR_SIZE
        raw[base:] = bytes(len(raw) - base)         # slot B: all zero, no signature
    else:
        _write_slot(raw, 1, secs, max(counter - 1, 0), 5)
    return bytes(raw)
