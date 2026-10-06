#!/usr/bin/env python3
"""End-to-end tests on synthetic saves. Run: python3 tests/test_extractor.py

Builds a 128 KB FRLG+ save from scratch — rotated sectors, real checksums,
encrypted Pokémon, XOR'd money/coins/game stats, and the FRLG+ bag written at
the offsets taken from include/global.h — then runs the extractor and checks
what came back. Finally it builds a "later" save and checks the diff.

Every offset under test comes from scripts/data/save_layout.txt, which
tools/generate_tables.py derives from the FRLG+ source, so a layout change in a
new FRLG+ version shows up here as a failure rather than as silent nonsense.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

from gen3core import (SUB_ORDERS, Tables, apply_calc_modes, calc_stats, encode_text,  # noqa: E402
                      exp_for_level)
import frlgplus as P  # noqa: E402

T = Tables()
L = T.layout
KEY = 0x1A2B3C4D
KEY16 = KEY & 0xFFFF
TID, SID = 24680, 13579
PLAYER_OTID = TID | (SID << 16)
OLD_OTID = 11111 | (22222 << 16)
SECTOR = 0xF80
# What the Key System settings written into the synthetic save mean for stat
# calculation — the game stores stats computed this way, so the test does too.
SAVED_CALC_MODES = {"iv": "all 31", "ev": "actual"}


def internal_id(name):
    return T.by_name[name]["internal"]


def move_id(name):
    return next(k for k, v in T.moves.items() if v[0] == name)


def item_id(name):
    return next(k for k, v in T.items.items() if v["name"] == name)


def make_mon(species, level, pid, otid=PLAYER_OTID, ot="RED", nick=None, moves=(), item=None,
             ivs=(31, 20, 15, 10, 25, 5), evs=(0, 0, 0, 0, 0, 0), met=101, met_level=5, ball=4,
             friendship=70, party=True, ability=0, pokerus=0, box_hp=None, box_status=0, forme=0):
    rec = T.by_name[species]
    exp = exp_for_level(rec["growth"], level)
    calc_ivs, calc_evs = apply_calc_modes(list(ivs), list(evs), SAVED_CALC_MODES)
    if box_hp is None:
        box_hp = calc_stats(rec["base"], calc_ivs, calc_evs, level, pid % 25)[0]
    packed = (box_hp & 0x3FF) | ((box_status & 0xF) << 10) | ((forme & 3) << 14)
    g = struct.pack("<HHIBBH", internal_id(species), item_id(item) if item else 0, exp,
                    0, friendship, packed)
    mids = [move_id(m) for m in moves] + [0] * (4 - len(moves))
    pps = [T.moves[m][1] if m else 0 for m in mids]
    a = struct.pack("<4H4B", *mids, *pps)
    e = bytes(evs) + bytes(6)
    origins = met_level | (4 << 7) | (ball << 11)
    ivw = sum(v << (5 * i) for i, v in enumerate(ivs)) | (ability << 31)
    m = struct.pack("<BBHII", pokerus, met, origins, ivw, 0)
    subs = {"G": g, "A": a, "E": e, "M": m}
    plain = b"".join(subs[c] for c in SUB_ORDERS[pid % 24])
    checksum = sum(struct.unpack("<24H", plain)) & 0xFFFF
    key = pid ^ otid
    enc = b"".join(struct.pack("<I", struct.unpack_from("<I", plain, i)[0] ^ key) for i in range(0, 48, 4))
    head = struct.pack("<II", pid, otid) + encode_text(nick or species.upper(), 10) + bytes([2, 0x02]) \
        + encode_text(ot, 7) + bytes([0]) + struct.pack("<HH", checksum, 0)
    box = head + enc
    assert len(box) == 80
    if not party:
        return box
    st = calc_stats(rec["base"], calc_ivs, calc_evs, level, pid % 25)
    return box + struct.pack("<IBBH6H", 0, level, 0, st[0], *st)


def set_flag(sb1, f):
    sb1[L["sb1_flags"] + (f >> 3)] |= 1 << (f & 7)


def write_slot_pocket(sb1, offset, entries, xor=True):
    """ItemSlot pocket: (itemId, quantity) pairs, quantity XOR'd like the game does."""
    for i, (iid, qty) in enumerate(entries):
        struct.pack_into("<HH", sb1, offset + 4 * i, iid, (qty ^ KEY16) if xor else qty)


def write_tmhm(sb1, tm_item_ids):
    for item in tm_item_ids:
        index = item - L["item_tm01"]
        sb1[L["sb1_bag_tmhm_bits"] + index // 8] |= 1 << (index % 8)


def write_key_items(sb1, item_ids):
    reverse = {v: k for k, v in T.key_item_indices.items()}
    for i, item in enumerate(item_ids):
        sb1[L["sb1_bag_key_items"] + i] = reverse[item]


# The bag as the tests expect to read it back, pocket name -> [(item name, qty)]
BAG = {
    "Items": [("Escape Rope", 7), ("Moon Stone", 4), ("Nugget", 7), ("Repel", 3)],
    "Medicine": [("Potion", 5), ("Super Potion", 3), ("Hyper Potion", 10), ("Rare Candy", 8),
                 ("PP Up", 5), ("Revive", 6)],
    "Poké Balls": [("Poké Ball", 53), ("Great Ball", 23), ("Ultra Ball", 16), ("Master Ball", 1)],
    "Held Items": [("Everstone", 1), ("Amulet Coin", 1), ("Exp. Share", 1), ("BlackGlasses", 1)],
    "Berry Pouch": [("Cheri Berry", 3), ("Oran Berry", 2), ("Sitrus Berry", 1)],
}
TM_CASE = ["TM04", "TM24", "TM29", "HM01", "HM02", "HM03", "HM04", "HM05", "HM08"]
KEY_ITEMS = ["Bicycle", "Town Map", "Tri-Pass", "VS Seeker", "Old Sea Map", "Link Bracelet"]
PC_ITEMS = [("Potion", 20), ("Full Heal", 4)]


def build_save(variant=0):
    sb2 = bytearray(SECTOR)
    sb1 = bytearray(4 * SECTOR)
    pc = bytearray(9 * SECTOR)

    sb2[L["sb2_player_name"]:L["sb2_player_name"] + 8] = encode_text("RED", 8)
    struct.pack_into("<HH", sb2, L["sb2_trainer_id"], TID, SID)
    struct.pack_into("<H", sb2, L["sb2_play_time_hours"], 44 + variant)
    sb2[L["sb2_play_time_minutes"]] = 53
    sb2[L["sb2_play_time_seconds"]] = 10
    struct.pack_into("<I", sb2, L["sb2_encryption_key"], KEY)

    # ---- party
    # Nicknames are invented placeholders. MIMIEN is the exception on purpose: it is the game's own
    # in-game-trade Mr. Mime nickname (src/data/ingame_trades.h), used to model an in-game trade.
    party = [
        make_mon("Parasect", 25, 0x00000007, nick="Sporey",
                 moves=["PoisonPowder", "Leech Life", "Flash", "Cut"],
                 item="TinyMushroom", met=127, met_level=8),
        make_mon("Raichu", 45 if variant else 42, 0x00000011, nick="Sparky",
                 moves=["Thunder" if variant else "ThunderShock", "Thunderbolt", "Agility",
                        "Quick Attack"], met=126, met_level=3),
        make_mon("Blastoise", 42, 0x0000000D, otid=OLD_OTID,
                 moves=["Bite", "Water Gun", "Rain Dance", "Withdraw"]),
        make_mon("Vaporeon", 43, 0x00000009, nick="Splashy",
                 moves=["AuroraBeam", "Haze", "Bite", "Quick Attack"], met=136, met_level=25),
        make_mon("Mr. Mime", 58, 0x0000000A, otid=0x0BADF00D, ot="BLUE", nick="Mimien",
                 moves=["Psychic", "Confusion", "Psybeam", "Magical Leaf"], met=254, met_level=30),
        make_mon("Charizard", 50, 0x0000000C, otid=OLD_OTID, nick="Scorchy",
                 moves=["Wing Attack", "Flamethrower", "Ember", "Fly"], met=88, met_level=5),
    ]
    sb1[L["sb1_party_count"]] = 6
    for i, m in enumerate(party):
        sb1[L["sb1_party"] + 100 * i:L["sb1_party"] + 100 * (i + 1)] = m

    # ---- money, coins, position
    struct.pack_into("<I", sb1, L["sb1_money"], (160576 + 12400 * variant) ^ KEY)
    struct.pack_into("<H", sb1, L["sb1_coins"], 9999 ^ KEY16)
    struct.pack_into("<H", sb1, L["sb1_registered_item"], item_id("VS Seeker"))
    struct.pack_into("<hh", sb1, L["sb1_pos"], 8, 8)
    sb1[L["sb1_location"]] = 11            # Fuchsia City Pokémon Center 1F is map 11.5
    sb1[L["sb1_location"] + 1] = 5
    sb1[L["sb1_last_heal_location"]] = 3   # Fuchsia City is map 3.7
    sb1[L["sb1_last_heal_location"] + 1] = 7

    # ---- flags and vars
    story = [T.flag_id("FLAG_SYS_POKEMON_GET"), T.flag_id("FLAG_SYS_POKEDEX_GET"),
             T.flag_id("FLAG_SYS_B_DASH"), T.flag_id("FLAG_GOT_SS_TICKET"), 0x010, 0x011, 0x501, 0x502]
    for i in range(7):
        story.append(L["flag_badge01"] + i)
    if variant:
        story += [T.flag_id("FLAG_SYS_GAME_CLEAR"), 0x2B4]
    for f in story:
        set_flag(sb1, f)
    struct.pack_into("<H", sb1, L["sb1_vars"] + 2 * 0x10, 3 + variant)

    # ---- game stats (every slot is stored XOR'd, zeros included)
    # "times saved" must equal the sector counter the save is assembled with — the
    # extractor cross-checks the two to prove the offset and the key are right.
    stats = {T.game_stat_id("GAME_STAT_SAVED_GAME"): 41 + variant,
             T.game_stat_id("GAME_STAT_STEPS"): 52000 + 900 * variant,
             T.game_stat_id("GAME_STAT_TOTAL_BATTLES"): 610 + 9 * variant,
             T.game_stat_id("GAME_STAT_POKEMON_CAPTURES"): 140 + variant,
             T.game_stat_id("GAME_STAT_USED_DAYCARE"): 1}
    for i in range(L["num_game_stats"]):
        struct.pack_into("<I", sb1, L["sb1_game_stats"] + 4 * i, stats.get(i, 0) ^ KEY)

    # ---- Key System settings
    key_flags = (1            # difficulty: Challenge
                 | (1 << 2)   # version: LeafGreen
                 | (0 << 3)   # nuzlocke off
                 | (1 << 4)   # ivCalcMode: all 31
                 | (0 << 6)   # evCalcMode: actual
                 | (1 << 7)   # noPMC on
                 | (3 << 8))  # expMod: 2x
    struct.pack_into("<H", sb1, L["sb1_key_flags"], key_flags)

    # ---- bag
    for name, offset in [("Items", "sb1_bag_items"), ("Medicine", "sb1_bag_medicine"),
                         ("Poké Balls", "sb1_bag_poke_balls"), ("Held Items", "sb1_bag_held_items"),
                         ("Berry Pouch", "sb1_bag_berries")]:
        write_slot_pocket(sb1, L[offset], [(item_id(n), q) for n, q in BAG[name]])
    write_tmhm(sb1, [item_id(n) for n in TM_CASE])
    write_key_items(sb1, [item_id(n) for n in KEY_ITEMS])
    write_slot_pocket(sb1, L["sb1_pc_items"], [(item_id(n), q) for n, q in PC_ITEMS], xor=False)

    # ---- Day Care (Four Island slot 1, Route 5) and its step counter
    sb1[L["sb1_daycare"]:L["sb1_daycare"] + 80] = make_mon(
        "Pidgey", 12, 0x00000015, party=False, met=101, met_level=2)
    struct.pack_into("<I", sb1, L["sb1_daycare"] + L["daycare_mon_size"] - 4, 4096)
    sb1[L["sb1_daycare_step_counter"]] = 221
    sb1[L["sb1_route5_daycare_mon"]:L["sb1_route5_daycare_mon"] + 80] = make_mon(
        "Haunter", 23, 0x00000016, party=False, met=104, met_level=14)
    struct.pack_into("<I", sb1, L["sb1_route5_daycare_mon"] + L["daycare_mon_size"] - 4, 73792)

    # ---- rival name, Master Trainer progress
    sb1[L["sb1_rival_name"]:L["sb1_rival_name"] + 8] = encode_text("BLUE", 8)
    sb1[L["sb1_master_trainer_title"]] = 25 if variant else 0      # Pikachu Master
    for species in ([25, 26] if variant else []):
        sb1[L["sb1_master_trainer_flags"] + species // 8] |= 1 << (species % 8)

    # ---- roaming legendary (only after the Hall of Fame, i.e. the variant save)
    if variant:
        ivs = [30, 20, 10, 5, 25, 15]
        roamer = struct.pack("<IIHHBB", sum(v << (5 * i) for i, v in enumerate(ivs)),
                             0x12345678, internal_id("Entei"), 155, 50, 0)
        roamer += bytes(5) + bytes([1]) + bytes(8)   # cool..tough, then `active`
        sb1[L["sb1_roamer"]:L["sb1_roamer"] + L["roamer_size"]] = roamer[:L["roamer_size"]]

    # ---- PC boxes
    box_mons = [(0, make_mon("Squirtle", 5, 0x00000020, party=False, met=88, met_level=5)),
                (1, make_mon("Dratini", 18, 0x00000021, party=False, met=136, met_level=18, ball=5)),
                (2, make_mon("Lickitung", 33, 0x00000022, otid=0x00ABCDEF, ot="DORIS", party=False,
                             met=254, met_level=33)),
                (3, make_mon("Moltres", 50, 0x00000023, party=False, met=175, met_level=50, ball=2)),
                (4, make_mon("Deoxys", 60, 0x00000025, party=False, met=201, met_level=60, ball=2,
                             forme=2, box_hp=7, box_status=9))]
    if variant:
        box_mons.append((62, make_mon("Raikou", 50, 0x00000024, party=False, met=103, met_level=50,
                                      ball=2)))
    for slot, data in box_mons:
        off = L["pc_boxes"] + slot * 80
        pc[off:off + 80] = data
    for bx in range(L["total_boxes"]):
        start = L["pc_box_names"] + (L["box_name_length"] + 1) * bx
        pc[start:start + L["box_name_length"] + 1] = encode_text(f"BOX {bx + 1}", L["box_name_length"] + 1)

    # ---- Pokédex
    held = {T.by_name[n]["national"] for n in
            ["Parasect", "Raichu", "Blastoise", "Vaporeon", "Mr. Mime", "Charizard", "Squirtle",
             "Dratini", "Lickitung", "Moltres", "Pidgey", "Haunter", "Deoxys"]}
    if variant:
        held |= {T.by_name["Raikou"]["national"], T.by_name["Entei"]["national"]}
    owned = set(held)
    n = 1
    while len(owned) < 86 + (2 if variant else 0):
        if n not in (T.by_name["Sandshrew"]["national"], T.by_name["Growlithe"]["national"]):
            owned.add(n)   # keep two easy-to-find Kanto species missing for the hint test
        n += 1
    seen = owned | set(range(1, 120))
    for num in owned:
        sb2[L["sb2_dex_owned"] + ((num - 1) >> 3)] |= 1 << ((num - 1) & 7)
    for num in seen:
        sb2[L["sb2_dex_seen"] + ((num - 1) >> 3)] |= 1 << ((num - 1) & 7)

    sections = {0: bytes(sb2)}
    for i in range(4):
        sections[1 + i] = bytes(sb1[i * SECTOR:(i + 1) * SECTOR])
    for i in range(9):
        sections[5 + i] = bytes(pc[i * SECTOR:(i + 1) * SECTOR])
    return sections


def assemble(sections_b, counter_b, sections_a=None, counter_a=0):
    raw = bytearray(b"\xFF" * 0x20000)
    for slot, secs, counter, rot in [(1, sections_b, counter_b, 5), (0, sections_a, counter_a, 9)]:
        if secs is None:
            continue
        for phys in range(L["sectors_per_slot"]):
            sid = (phys + rot) % L["sectors_per_slot"]
            data = secs[sid]
            total = sum(struct.unpack(f"<{SECTOR // 4}I", data)) & 0xFFFFFFFF
            chk = ((total >> 16) + (total & 0xFFFF)) & 0xFFFF
            sector = data + bytes(0x74) + struct.pack("<HHII", sid, chk, 0x08012025, counter)
            base = (slot * L["sectors_per_slot"] + phys) * 0x1000
            raw[base:base + 0x1000] = sector
    return bytes(raw)


def run(save_path, out, prev=None):
    cmd = [sys.executable, os.path.join(SCRIPTS, "extract.py"), save_path, "--out", out, "--quiet"]
    if prev:
        cmd += ["--prev", prev]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    with open(os.path.join(out, "extract_full.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- checks
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def check_tables():
    check(T.layout["sb1_bag_items"] == 0x310, f"bag Items offset {T.layout['sb1_bag_items']:#x}")
    check(T.layout["sb1_bag_poke_balls"] == 0x3CC, "bag Poké Balls offset")
    check(T.layout["sb1_bag_tmhm_bits"] == 0x400, "bag TM bitfield offset")
    check(T.layout["sb1_bag_key_items"] == 0x408, "bag Key Items offset")
    check(T.layout["sb1_bag_medicine"] == 0x42C, "bag Medicine offset")
    check(T.layout["sb1_bag_held_items"] == 0x4CC, "bag Held Items offset")
    check(T.layout["sb1_bag_berries"] == 0x3570, "bag Berry Pouch offset")
    check(T.layout["sb1_daycare"] == 0x2F7C, "Day Care offset")
    check(T.by_name["Bulbasaur"]["base"] == [45, 49, 49, 45, 65, 65], "Bulbasaur base stats")
    check(T.by_name["Treecko"]["internal"] == 277, "Treecko internal id")
    check(T.species[277]["national"] == 252, "internal 277 -> national 252")
    check(T.items[item_id("Old Sea Map")]["pocket"] == "key_items", "Old Sea Map pocket")
    check(len(T.extended_dex) == 246, f"extended dex size {len(T.extended_dex)}")
    check(T.by_name["Bulbasaur"]["national"] in T.extended_dex, "Bulbasaur in extended dex")
    check(T.map_label("11.5") == "Fuchsia City Pokémon Center 1F", f"map label {T.map_label('11.5')!r}")
    check(T.flag_name(L["flag_badge01"]) == "FLAG_BADGE01_GET", "badge flag name")
    sand = T.encounters_for(T.by_name["Sandshrew"]["national"])
    check(any(e["version"] == "LG" and e["method"] == "grass" for e in sand), "Sandshrew encounters")


def check_first_run(r):
    check(r["save_health"]["active_slot"] == "B" and r["save_health"]["save_counter"] == 41,
          "slot choice")
    check(r["save_health"]["valid"], f"slot validity {r['save_health']['problems']}")

    t = r["trainer"]
    check((t["name"], t["tid"], t["sid"], t["money"], t["coins"]) ==
          ("RED", TID, SID, 160576, 9999), f"trainer {t}")
    check(t["play_time"] == "44:53:10", t["play_time"])
    check(t["rival_name"] == "BLUE", f"rival name {t.get('rival_name')!r}")
    check(t["registered_item"] == "VS Seeker", "registered item")

    ks = r["key_system"]
    check(ks["version"] == "LeafGreen", f"key system version {ks.get('version')}")
    check(ks["difficulty"] == "Challenge", f"difficulty {ks.get('difficulty')}")
    check(ks["nuzlocke"] is False, "nuzlocke off")
    check(ks["iv_calculation"] == "all 31", f"iv calc {ks.get('iv_calculation')}")
    check(ks["ev_calculation"] == "actual", f"ev calc {ks.get('ev_calculation')}")
    check(ks["no_free_heals"] is True, "no free heals")
    check(ks["exp_modifier"] == "2x", f"exp modifier {ks.get('exp_modifier')}")
    check(r["section_status"]["key_system"] == "ok", "key system status")
    corr = ks["corroboration"]
    check(corr["implies_no_free_heals"] is True,
          f"boxes should imply No Free Heals is on: {corr}")
    check(corr["agrees_with_the_save"] is True, f"key system corroboration {corr}")

    loc = r["location"]
    check(loc["map_name"] == "Fuchsia City Pokémon Center 1F", f"map name {loc.get('map_name')!r}")
    check(loc["region"] == "Fuchsia City", f"map region {loc.get('region')!r}")
    check(loc["last_heal_map_name"] == "Fuchsia City", f"heal map {loc.get('last_heal_map_name')!r}")
    check(r["section_status"]["location"] == "ok", "location status")

    party = r["party"]
    check(len(party) == 6, "party size")
    p0 = party[0]
    check((p0["species"], p0["nickname"], p0["level"], p0["held_item"], p0["ability"]) ==
          ("Parasect", "Sporey", 25, "TinyMushroom", "Effect Spore"), f"parasect {p0}")
    check([m["name"] for m in p0["moves"]] == ["PoisonPowder", "Leech Life", "Flash", "Cut"],
          "parasect moves")
    check(p0["nature"] == "Relaxed" and p0["origin"]["met_location"] == "Mt. Moon", "nature/met")
    check(all(m["checks"]["checksum"] == "ok" for m in party), "party checksums")
    # the save says IV calc = all 31, so the stored stats must be checked that way
    check(all(m["stats_match"] for m in party),
          "stored stats vs stats recomputed under the save's IV/EV calc mode: "
          + str([(m["nickname"], m["checks"]["warnings"]) for m in party if not m["stats_match"]]))
    cls = {m["nickname"]: m["origin"]["class"] for m in party}
    check(cls["Scorchy"] == "your_name_other_id" and cls["Mimien"] == "in_game_trade"
          and cls["Sparky"] == "caught_here", f"origin classes {cls}")

    check(r["boxes"]["total"] == 5, f"box total {r['boxes']['total']}")
    check(r["boxes"]["boxes"][0]["name"] == "BOX 1", "box name")
    moltres = r["boxes"]["boxes"][0]["pokemon"][3]
    check(moltres["species"] == "Moltres" and moltres["origin"]["met_location"] == "Mt. Ember",
          "moltres")
    deoxys = r["boxes"]["boxes"][0]["pokemon"][4]
    check(deoxys["hp_current"] == 7, f"boxed HP {deoxys.get('hp_current')}")
    check(deoxys["status"] == "burned", f"boxed status {deoxys.get('status')}")
    check(deoxys["box_hp_recorded"] is True, "boxed HP recorded")
    check(deoxys["forme"] == "Defense", f"Deoxys forme {deoxys.get('forme')}")

    check(r["pokedex"]["owned_count"] == 86, f"dex owned {r['pokedex']['owned_count']}")
    pr = r["progress"]
    check(pr["badge_count"] == 7 and "Volcano (Blaine)" in pr["badges"], "badges")
    names = pr["named_flags_set"]
    check(names.get("0x828") == "FLAG_SYS_POKEMON_GET", f"named flags {list(names)[:4]}")
    check(any(v.startswith("FLAG_GOT_SS_TICKET") for v in names.values()), "story flag label")
    check(r["section_status"]["flags"] == "ok", "flags status")
    check(pr["vars_nonzero"]["0x4010"]["value"] == 3, f"vars {pr['vars_nonzero'].get('0x4010')}")

    gs = r["game_stats"]
    check(gs["Steps"] == 52000, f"game stat Steps {gs.get('Steps')}")
    check(gs["Pokémon Captures"] == 140, f"game stat captures {gs.get('Pokémon Captures')}")
    check(r["section_status"]["game_stats"] == "ok", "game stats status")
    check(r["game_stats_check"]["matches"] is True,
          f"'times saved' vs save counter {r['game_stats_check']}")

    it = r["items"]
    check(r["section_status"]["items"] == "ok", f"items status {r['section_status']['items']}")
    pockets = {p["name"]: p for p in it["pockets"]}
    check(set(pockets) == {"Items", "Medicine", "Key Items", "Held Items", "Poké Balls", "TM Case",
                           "Berry Pouch", "PC item storage"}, f"pockets {sorted(pockets)}")
    for name, expected in BAG.items():
        got = [(x["name"], x["qty"]) for x in pockets[name]["items"]]
        check(got == expected, f"{name} pocket: {got} != {expected}")
    check([x["name"] for x in pockets["TM Case"]["items"]] == TM_CASE,
          f"TM Case {[x['name'] for x in pockets['TM Case']['items']]}")
    check(all(x["qty"] == 1 for x in pockets["TM Case"]["items"]), "TM quantities")
    check([x["name"] for x in pockets["Key Items"]["items"]] == KEY_ITEMS,
          f"Key Items {[x['name'] for x in pockets['Key Items']['items']]}")
    check([(x["name"], x["qty"]) for x in pockets["PC item storage"]["items"]] ==
          [("Potion", 20), ("Full Heal", 4)], "PC item storage (quantities are not XOR'd)")
    check(pockets["Items"]["capacity"] == 47, f"Items capacity {pockets['Items']['capacity']}")
    check(pockets["TM Case"]["items"][0]["move"] == "Calm Mind", "TM move name")
    check(it["pocket_coherence_ok"] is True, f"items in the wrong pocket: {it['problems']}")

    dc = r["day_care"]
    check(dc["step_counter"] == 221, f"day care step counter {dc.get('step_counter')}")
    check([m["species"] for m in dc["four_island"]["pokemon"]] == ["Pidgey"], "Four Island day care")
    check(dc["four_island"]["pokemon"][0]["steps"] == 4096, "day care steps")
    check([m["species"] for m in dc["route_5"]["pokemon"]] == ["Haunter"], "Route 5 day care")
    check(r["section_status"]["day_care"] == "ok", "day care status")

    check(r["roamer"]["active"] is False, "no roamer before the Hall of Fame")
    check(r["master_trainers"]["title"] is None, "no Master Trainer title yet")
    check(r["master_trainers"]["cleared_count"] == 0, "no Master Trainers cleared")

    h = r["hints"]
    check(h["obedience_cap_for_outsiders"] == 70, "obedience cap")
    missing = {m["species"]: m for m in h["missing_extended_dex"]}
    check("Sandshrew" in missing, f"Sandshrew missing from the extended dex hint: {list(missing)[:6]}")
    where = missing["Sandshrew"]["where"]
    check(where and any("grass" in w for w in where),
          f"no catch locations for Sandshrew: {where}")
    check(any("LeafGreen" in w or "LG" in w for w in where),
          f"version not shown in catch locations: {where}")
    check(h["missing_extended_dex_count"] >= 2, "extended dex missing count")
    check(not any(e["species"] == "Squirtle" for e in h["evolutions_overdue"]), "no false evo hint")


def check_second_run(r2, doc_text):
    lines = r2["changes"]["lines"]
    joined = "\n".join(lines)
    for needle in ["Money ₽160,576 → ₽172,976", "NEW Pokémon: Raikou Lv50", "Lv42→45",
                   "learned Thunder", "forgot ThunderShock", "newly owned", "Play time +1h",
                   "FLAG_SYS_GAME_CLEAR"]:
        check(needle in joined, f"diff missing: {needle}")
    check(r2["roamer"]["active"] is True, "roamer active after the Hall of Fame")
    check(r2["roamer"]["species"] == "Entei", f"roamer species {r2['roamer'].get('species')}")
    check(r2["roamer"]["level"] == 50, "roamer level")
    check(r2["roamer"]["ivs"]["HP"] == 30, f"roamer IVs {r2['roamer'].get('ivs')}")
    check(r2["master_trainers"]["title"] == "Pikachu Master",
          f"master trainer title {r2['master_trainers'].get('title')}")
    check(r2["master_trainers"]["cleared_count"] == 2, "master trainers cleared")
    check("Pikachu" in r2["master_trainers"]["cleared"], "master trainer species list")
    # the bag is unchanged between the two saves, so no bag lines
    check(not any(line.startswith("Bag:") for line in lines), "unexpected bag diff")


def main():
    tmp = tempfile.mkdtemp()
    s1 = os.path.join(tmp, "v1.srm")
    s2 = os.path.join(tmp, "v2.srm")
    # slot A holds an OLDER save (counter 40) — the extractor must pick slot B (counter 41)
    with open(s1, "wb") as f:
        f.write(assemble(build_save(0), 41, build_save(0), 40))
    with open(s2, "wb") as f:
        f.write(assemble(build_save(1), 42, build_save(0), 41))

    check_tables()
    r = run(s1, os.path.join(tmp, "out1"))
    check_first_run(r)

    # store the baseline the way the Doc will hold it: the code line in a fence with a label
    with open(os.path.join(tmp, "out1", "snapshot_code.txt")) as f:
        code = f.read()
    doc_text = os.path.join(tmp, "doc_tab.txt")
    with open(doc_text, "w") as f:
        f.write("Save snapshot (do not edit)\n```\n" + code + "\n```\n")
    r2 = run(s2, os.path.join(tmp, "out2"), prev=doc_text)
    check_second_run(r2, doc_text)

    with open(os.path.join(tmp, "out2", "summary.md"), encoding="utf-8") as f:
        summary = f.read()
    print(summary)
    print("=" * 60)
    if FAILS:
        print(f"FAILURES ({len(FAILS)}):")
        for x in FAILS:
            print(" -", x)
        sys.exit(1)
    print(f"All checks passed. (synthetic files in {tmp})")


if __name__ == "__main__":
    main()
