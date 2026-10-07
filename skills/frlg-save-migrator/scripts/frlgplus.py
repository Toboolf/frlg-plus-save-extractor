"""FRLG+ (Deokishisu, v1.5.1) save profile.

Every offset, capacity and name table used here comes from the FRLG+ source via
tools/generate_tables.py — see scripts/data/save_layout.txt. That includes the
parts FRLG+ rebuilt: the bag (bigger pockets, new Medicine and Held Items
pockets, a bitfield TM Case and one-byte Key Item slots), the Key System
settings, the roaming legendary and the Master Trainer progress.

Two layout notes worth knowing, both checked by the generator:
- `money`, `coins` and every game stat are stored XOR'd with
  SaveBlock2.encryptionKey; bag quantities are XOR'd with its low half, but PC
  item quantities are not (the game XORs those with 0).
- the `/*0x2F80*/` comment on SaveBlock1.daycare is a vanilla leftover; FRLG+
  shrank the preceding filler by 4 bytes, so the Day Care really starts at
  0x2F7C. The generator derives it and checks it lands on giftRibbons.

Every section is parsed independently: one failure never stops the rest.
"""
from __future__ import annotations

import sys
import traceback

import gen3core
from gen3core import (HM_MOVES, NATURES, SECTOR_DATA_SIZE, choose_slot, decode_pokemon, decode_text,
                      nature_effect, quick_valid_boxmon, to_display, u16, u32, s16)

PROFILE = "FRLG+ v1.5.1 (offsets generated from the FRLG+ source)"
PROFILE_MODULE = sys.modules[__name__]

NAME = "FRLG+ v1.5.1"
LAYOUT = "save_layout.txt"
MAX_SPECIES = 439          # NUM_SPECIES plus the 27 Unown letter slots

# FRLG+ reuses the last halfword of substructure G — padding in vanilla — for a
# boxed Pokémon's HP, ailment and forme (see struct PokemonSubstruct0 and
# StoreHPAndStatusInBoxMon in src/pokemon.c).
BOX_STATUS = {8: "poisoned", 9: "burned", 10: "frozen", 11: "paralyzed"}


def decode_box_padding(packed):
    return {"box_hp": packed & 0x3FF,
            "box_status": (packed >> 10) & 0xF,
            "forme": (packed >> 14) & 3}


def encode_box_padding(box_hp, box_status, forme):
    """The exact inverse of decode_box_padding: boxHP:10, boxStatus:4, forme:2.

    This profile owns the bit layout, so anything that *writes* that halfword
    (extras/fix_boxed_hp.py, and the migrator later) goes through here rather
    than carrying its own copy of the shifts. Values out of range are refused
    instead of being silently truncated into a neighbouring field.
    """
    for name, value, limit in (("box_hp", box_hp, 0x3FF), ("box_status", box_status, 0xF),
                               ("forme", forme, 3)):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
            raise ValueError(f"{name}={value!r} does not fit FRLG+'s field (0..{limit})")
    return box_hp | (box_status << 10) | (forme << 14)


def write_box_padding(mon80, *, box_hp, box_status=0, forme=0):
    """Set FRLG+'s boxHP, boxStatus and forme on one Pokemon.

    The bit layout is this profile's (encode_box_padding); the decrypt, checksum
    and re-encrypt are the core's. Nothing that writes that halfword should carry
    its own copy of either half.
    """
    return gen3core.rewrite_substructures(
        mon80, g_halfword=encode_box_padding(box_hp, box_status, forme))


def box_status_name(box_status, box_hp):
    """FRLG+ packs the ailment into 4 bits: 0 none, 1-7 asleep turns, 8-11 PSN/BRN/FRZ/PRZ."""
    if box_status == 0:
        return "fainted" if box_hp == 0 else "healthy"
    if box_status < 8:
        return f"asleep ({box_status} turns)"
    return BOX_STATUS.get(box_status, f"status #{box_status}")


def boxed_hp_fields(padding, calc_modes):
    """HP/status fields for a boxed Pokémon.

    FRLG+ writes them on deposit: with No Free Heals on it stores the live
    values, with it off it stores max HP and no status. In vanilla that
    halfword is padding, so a 0 while the key is off means the slot was simply
    never written, not a fainted Pokémon.
    """
    hp = padding["box_hp"]
    if (calc_modes or {}).get("no_free_heals"):
        return {"hp_current": hp,
                "status": box_status_name(padding["box_status"], hp),
                "box_hp_recorded": True,
                "status_source": "FRLG+ boxed HP/status, live (No Free Heals is on)"}
    if hp:
        return {"hp_current": hp, "status": "healthy", "box_hp_recorded": True,
                "status_source": ("FRLG+ boxed HP, written as max HP on deposit "
                                  "(No Free Heals is off)")}
    return {"hp_current": None, "status": "not recorded", "box_hp_recorded": False,
            "status_source": ("no boxed HP stored for this slot — nothing has written it "
                              "since this Pokémon was put in the box; a save editor or a "
                              "different ROM build will leave it at zero")}

BADGES = ["Boulder (Brock)", "Cascade (Misty)", "Thunder (Lt. Surge)", "Rainbow (Erika)",
          "Soul (Koga)", "Marsh (Sabrina)", "Volcano (Blaine)", "Earth (Giovanni)"]
DIFFICULTIES = {0: "Normal", 1: "Challenge", 2: "Easy", 3: "Unknown (3)"}
IV_CALC = {0: "actual", 1: "all 31", 2: "all 0", 3: "unknown (3)"}
EV_CALC = {0: "actual", 1: "all 0"}
EXP_MOD = {0: "0x", 1: "0.5x", 2: "1x", 3: "2x"}
# The obedience cap for traded Pokémon, keyed by the highest badge earned.
OBEDIENCE_CAPS = [(7, 100), (5, 70), (3, 50), (1, 30)]
MASTER_TRAINER_SPECIES_MAX = 151
MASTER_TRAINER_GRANDMASTER = 152


class Section:
    """Runs one parser; records ok/partial/unverified/failed instead of crashing."""

    def __init__(self, result, name):
        self.result, self.name = result, name

    def __enter__(self):
        self.result["section_status"][self.name] = "ok"
        return self

    def __exit__(self, et, ev, tb):
        if et is not None:
            self.result["section_status"][self.name] = "failed"
            self.result["diagnostics"]["errors"].append(
                f"{self.name}: {ev!r} — " + traceback.format_exc(limit=2).strip().splitlines()[-1])
            return True

    def partial(self, why):
        self.result["section_status"][self.name] = "partial"
        self.result["diagnostics"]["warnings"].append(f"{self.name}: {why}")

    def unverified(self, why):
        self.result["section_status"][self.name] = "unverified"
        self.result["diagnostics"]["warnings"].append(f"{self.name}: {why}")


def extract(raw, tables, source_name="save"):
    L = tables.layout
    res = {"schema_version": 2, "profile": PROFILE, "source_file": source_name, "file_size": len(raw),
           "section_status": {}, "diagnostics": {"warnings": [], "errors": [], "needs_input": [],
                                                 "known_gaps": []}}
    diag = res["diagnostics"]

    # ---- Save health / slot selection
    best, other = choose_slot(raw)
    res["save_health"] = {
        "active_slot": best["slot"], "save_counter": best["counter"], "valid": best["valid"],
        "problems": best["problems"],
        "other_slot": {"slot": other["slot"], "counter": other["counter"], "valid": other["valid"]},
        "sectors": best["sector_info"]}
    if not best["valid"]:
        diag["errors"].append("No save slot passed integrity checks — every result below is unreliable: "
                              + "; ".join(best["problems"][:5]))
    elif other["counter"] is not None and other["counter"] > best["counter"]:
        diag["errors"].append(f"The NEWEST save (slot {other['slot']}, #{other['counter']}) is corrupt — "
                              f"showing the previous save #{best['counter']} instead. "
                              f"Progress since then is missing.")
    secs = best["sections"]
    blank = bytes(SECTOR_DATA_SIZE)
    sb2 = secs.get(0, blank)
    sb1 = b"".join(secs.get(i, blank) for i in range(1, 5))
    pc = b"".join(secs.get(i, blank) for i in range(5, 14))
    pc_tail = next((s for s in best["sector_info"] if s.get("section_id") == 13), {})
    res["save_health"]["pc_tail_bytes_used_beyond_vanilla"] = max(
        0, pc_tail.get("data_end", 0) - (L["pc_end"] - 8 * SECTOR_DATA_SIZE))

    # ---- Key System (read first: it changes how stats are calculated)
    key_system = {}
    with Section(res, "key_system") as s:
        key_system = _parse_key_system(sb1, L)
        res["key_system"] = key_system

    # ---- Trainer
    key = 0
    player = None
    with Section(res, "trainer") as s:
        tid, sid = u16(sb2, L["sb2_trainer_id"]), u16(sb2, L["sb2_trainer_id"] + 2)
        key = u32(sb2, L["sb2_encryption_key"])
        player = {"name": decode_text(sb2[L["sb2_player_name"]:L["sb2_player_name"] + 8]),
                  "tid": tid, "sid": sid}
        money = u32(sb1, L["sb1_money"]) ^ key
        coins = u16(sb1, L["sb1_coins"]) ^ (key & 0xFFFF)
        hours = u16(sb2, L["sb2_play_time_hours"])
        minutes = sb2[L["sb2_play_time_minutes"]]
        title = sb1[L["sb1_master_trainer_title"]]
        res["trainer"] = {
            "name": player["name"], "gender": "Girl" if sb2[L["sb2_player_gender"]] else "Boy",
            "tid": tid, "sid": sid,
            "rival_name": decode_text(sb1[L["sb1_rival_name"]:L["sb1_rival_name"] + 8]),
            "play_time": f"{hours}:{minutes:02d}:{sb2[L['sb2_play_time_seconds']]:02d}",
            "play_minutes": hours * 60 + minutes,
            "money": money, "coins": coins,
            "registered_item": tables.item_name(u16(sb1, L["sb1_registered_item"])),
            "master_trainer_title": _master_trainer_title(title, tables),
            "options_raw": f"{sb2[L['sb2_options_button_mode']]:02X} {u16(sb2, L['sb2_options']):04X}",
            "security_key": f"{key:08X}"}
        bad = []
        if money > 999999:
            bad.append(f"money {money} > 999,999")
        if coins > 9999:
            bad.append(f"coins {coins} > 9,999")
        if not player["name"] or "[" in player["name"]:
            bad.append(f"odd player name {player['name']!r}")
        if bad:
            s.partial("implausible values: " + "; ".join(bad))

    # ---- Location
    with Section(res, "location") as s:
        here = f"{sb1[L['sb1_location']]}.{sb1[L['sb1_location'] + 1]}"
        heal = f"{sb1[L['sb1_last_heal_location']]}.{sb1[L['sb1_last_heal_location'] + 1]}"
        res["location"] = {
            "map": here, "x": s16(sb1, L["sb1_pos"]), "y": s16(sb1, L["sb1_pos"] + 2),
            "map_name": tables.map_label(here), "region": tables.map_region(here),
            "last_heal_map": heal, "last_heal_map_name": tables.map_label(heal)}
        if not res["location"]["map_name"]:
            s.partial(f"map {here} is not in the generated map table")

    # ---- Party
    with Section(res, "party") as s:
        res["party"] = _parse_party(sb1, tables, player, key_system, s, res)

    # ---- PC boxes
    with Section(res, "boxes") as s:
        res["boxes"] = _parse_boxes(pc, tables, player, key_system, s)

    # ---- Key System cross-check (needs the boxes, so it runs after them)
    if res.get("key_system") and res.get("boxes"):
        res["key_system"]["corroboration"] = _corroborate_key_system(res["key_system"], res["boxes"])
        if res["key_system"]["corroboration"].get("agrees_with_the_save") is False:
            res["section_status"]["key_system"] = "partial"
            diag["warnings"].append(
                "key_system: the No Free Heals setting disagrees with what the boxes hold — "
                + res["key_system"]["corroboration"]["because"])

    # ---- Pokédex
    with Section(res, "pokedex") as s:
        top = max(tables.species_by_national)
        owned = _bits(sb2[L["sb2_dex_owned"]:L["sb2_dex_owned"] + L["dex_flag_bytes"]], top)
        seen = _bits(sb2[L["sb2_dex_seen"]:L["sb2_dex_seen"] + L["dex_flag_bytes"]], top)
        res["pokedex"] = {
            "owned_count": len(owned), "seen_count": len(seen), "owned": owned, "seen": seen,
            "extended_owned_count": len([n for n in owned if n in tables.extended_dex]),
            "extended_total": len(tables.extended_dex),
            "header_raw": sb2[L["sb2_pokedex"]:L["sb2_pokedex"] + 4].hex(" ")}
        if not set(owned) <= set(seen):
            s.partial("some species are owned but not seen")

    # ---- Flags, badges, vars
    with Section(res, "flags") as s:
        res["progress"] = _parse_flags(sb1, tables, s)

    # ---- Game stats
    with Section(res, "game_stats") as s:
        stats, raw_stats = {}, {}
        for i in range(L["num_game_stats"]):
            v = u32(sb1, L["sb1_game_stats"] + 4 * i) ^ key
            raw_stats[i] = v
            label = tables.game_stat_label(i)
            if label:
                stats[label] = v
        res["game_stats"] = stats
        # the game clamps every stat to 0xFFFFFF, so anything above that is a decode error
        over = {k: v for k, v in stats.items() if v > 0xFFFFFF}
        if over:
            s.partial(f"values above the game's 0xFFFFFF cap: {over}")
        # Strong cross-check: the "times saved" stat is bumped on every save, so it
        # must match the sector counter. If it does, the offset and the key are right.
        saved = raw_stats.get(tables.game_stat_id("GAME_STAT_SAVED_GAME"))
        counter = res["save_health"]["save_counter"]
        res["game_stats_check"] = {"saved_game_stat": saved, "save_counter": counter,
                                   "matches": saved == counter}
        if saved != counter and not over:
            s.partial(f"the 'times saved' stat is {saved} but the save counter is {counter} — "
                      f"game stats may be read at the wrong offset or with the wrong key")

    # ---- Bag
    with Section(res, "items") as s:
        res["items"] = _parse_items(sb1, key, tables, s, diag)

    # ---- Day Care
    with Section(res, "day_care") as s:
        res["day_care"] = _parse_day_care(sb1, tables, player, key_system, s)

    # ---- Roaming legendary
    with Section(res, "roamer") as s:
        res["roamer"] = _parse_roamer(sb1, tables, player)

    # ---- Master Trainers
    with Section(res, "master_trainers") as s:
        res["master_trainers"] = _parse_master_trainers(sb1, tables)

    # ---- Any other Pokémon structure left in the save
    with Section(res, "other_pokemon") as s:
        res["other_pokemon"] = _scan_other_pokemon(sb1, pc, tables, player, key_system)
        if res["other_pokemon"]:
            s.unverified("found by scanning memory the known layout does not cover")

    # ---- Hall of Fame
    with Section(res, "hall_of_fame") as s:
        res["hall_of_fame"] = _parse_hof(raw, tables, L)

    # ---- Cross-checks & hints
    with Section(res, "hints") as s:
        res["hints"] = _hints(res, tables)

    if not best["valid"]:
        for k, v in res["section_status"].items():
            if v == "ok":
                res["section_status"][k] = "partial"

    # How far each section is trusted: its decode status plus whether the player has
    # confirmed it against the running game (scripts/data/verification.json, personalised
    # by the optional local.json overlay — see gen3core.Tables).
    vsections = tables.verification.get("sections", {})
    res["verification"] = {
        "personal": tables.has_local,
        "tracker": tables.verification.get("tracker"),
        "checked_against_save": tables.verification.get("checked_against_save"),
        "sections": {name: {"status": status,
                            "evidence": vsections.get(name, {}).get("evidence", "source"),
                            "how": vsections.get(name, {}).get("how"),
                            "confirm_by": vsections.get(name, {}).get("confirm_by")}
                     for name, status in res["section_status"].items()}}
    unconfirmed = [n for n, v in res["verification"]["sections"].items()
                   if v["evidence"] != "confirmed" and v.get("confirm_by")]
    if unconfirmed:
        diag["needs_input"].append(
            "Decoded from the FRLG+ source and self-checked, but not yet confirmed against your "
            "game: " + ", ".join(unconfirmed) + ". See `verification.sections[...].confirm_by` for "
            "what would settle each one.")
    diag["known_gaps"] = [
        "The roaming legendary's current map is kept in RAM, not in the save — only its species, "
        "level, HP, IVs and personality are stored.",
        "Quest Log scenes, Trainer Tower records and Battle Tower streaks are not decoded.",
        "Wild-encounter hints assume the Key System version the save currently has.",
    ]
    return res


# ----------------------------------------------------------------------------
def _bits(b, count):
    return [i + 1 for i in range(count) if (b[i >> 3] >> (i & 7)) & 1]


def _parse_key_system(sb1, L):
    w = u16(sb1, L["sb1_key_flags"])
    return {"difficulty": DIFFICULTIES[w & 3],
            "version": "LeafGreen" if (w >> 2) & 1 else "FireRed",
            "nuzlocke": bool((w >> 3) & 1),
            "iv_calculation": IV_CALC[(w >> 4) & 3],
            "ev_calculation": EV_CALC.get((w >> 6) & 1, "unknown"),
            "no_free_heals": bool((w >> 7) & 1),
            "exp_modifier": EXP_MOD[(w >> 8) & 3],
            "in_key_system_menu": bool((w >> 15) & 1),
            "raw": f"0x{w:04X}"}


def _calc_modes(key_system):
    """The Key System settings that change how a Pokémon's own data is read."""
    return {"iv": key_system.get("iv_calculation", "actual"),
            "ev": key_system.get("ev_calculation", "actual"),
            "no_free_heals": key_system.get("no_free_heals", False)}


def _corroborate_key_system(key_system, boxes):
    """Check the No Free Heals bit against what the boxes actually hold.

    On deposit the game writes live HP and status while the key is on, and max HP
    with no status while it is off. So partial HP or a stored ailment can only come
    from the key being on, and a clean "every recorded slot is exactly max HP" can
    only come from it being off. Either way it is independent evidence for bit 7.
    """
    zero = full = partial = ailing = 0
    for bx in boxes.get("boxes", []):
        for m in bx["pokemon"]:
            if m["is_egg"] or m["checks"]["checksum"] != "ok":
                continue
            hp, mx = m.get("hp_current"), (m.get("stats") or {}).get("HP")
            if not m.get("box_hp_recorded"):
                zero += 1
            elif mx is not None and hp == mx:
                full += 1
            else:
                partial += 1
            if m.get("status") not in ("healthy", "not recorded", None):
                ailing += 1
    out = {"boxed_slots_not_recorded": zero, "boxed_slots_at_max_hp": full,
           "boxed_slots_at_partial_hp": partial, "boxed_slots_with_an_ailment": ailing}
    if partial or ailing:
        out["implies_no_free_heals"] = True
        out["because"] = (f"{partial} boxed Pokémon are stored below max HP and {ailing} with an "
                          f"ailment, which only the No Free Heals path writes")
    elif full:
        out["implies_no_free_heals"] = False
        out["because"] = (f"{full} boxed Pokémon are stored at exactly max HP with no ailment and "
                          f"none in between, the signature of No Free Heals being off"
                          + (f" ({zero} slots predate FRLG+ recording it at all)" if zero else ""))
    else:
        out["implies_no_free_heals"] = None
        out["because"] = "no boxed Pokémon carry a recorded HP, so there is nothing to compare"
    if out["implies_no_free_heals"] is not None:
        out["agrees_with_the_save"] = out["implies_no_free_heals"] == key_system.get("no_free_heals")
    return out


def _master_trainer_title(value, tables):
    if value == 0:
        return None
    if value == MASTER_TRAINER_GRANDMASTER:
        return "Grandmaster"
    rec = tables.species_by_national.get(value)
    return f"{rec['name']} Master" if rec else f"Master trainer title #{value}"


def _parse_party(sb1, tables, player, key_system, s, res):
    L = tables.layout
    count = sb1[L["sb1_party_count"]]
    mons = []
    for i in range(6):
        off = L["sb1_party"] + 100 * i
        m = decode_pokemon(sb1[off:off + 100], tables, player, party=True, where=f"party {i + 1}",
                           calc_modes=_calc_modes(key_system), profile=PROFILE_MODULE)
        if m:
            mons.append(m)
    good = [m for m in mons if m["checks"]["checksum"] == "ok"]
    if not (1 <= count <= 6) or len(good) != count:
        s.partial(f"party count byte says {count}, decoded {len(good)} valid Pokémon")
    stale = [m["nickname"] or m["species"] for m in mons if m.get("stats_state") == "stale"]
    if stale:
        res["diagnostics"]["warnings"].append(
            "party: stored stats are behind the EVs for " + ", ".join(stale)
            + " — normal in Gen 3 (stats refresh on the next level-up), not a decode problem")
    wrong = [m["nickname"] or m["species"] for m in mons if m.get("stats_state") == "mismatch"]
    if wrong:
        s.partial("stored stats do not match recomputed stats for " + ", ".join(wrong))
    return mons


def _parse_boxes(pc, tables, player, key_system, s):
    L = tables.layout
    name_len = L["box_name_length"] + 1
    names = [decode_text(pc[L["pc_box_names"] + name_len * i:L["pc_box_names"] + name_len * (i + 1)])
             for i in range(L["total_boxes"])]
    boxes, bad = [], 0
    for bx in range(L["total_boxes"]):
        mons = []
        for slot in range(L["in_box_count"]):
            off = L["pc_boxes"] + (bx * L["in_box_count"] + slot) * 80
            m = decode_pokemon(pc[off:off + 80], tables, player,
                               where=f"box {bx + 1} slot {slot + 1}",
                               calc_modes=_calc_modes(key_system), profile=PROFILE_MODULE)
            if m:
                mons.append(m)
                if m["checks"]["checksum"] != "ok":
                    bad += 1
        boxes.append({"box": bx + 1, "name": names[bx],
                      "wallpaper": pc[L["pc_box_wallpapers"] + bx],
                      "count": len(mons), "pokemon": mons})
    if bad:
        s.partial(f"{bad} box slot(s) failed checksum")
    return {"current_box": pc[0] + 1, "total": sum(b["count"] for b in boxes), "boxes": boxes}


def _parse_flags(sb1, tables, s):
    L = tables.layout
    flag_bytes = sb1[L["sb1_flags"]:L["sb1_flags"] + L["num_flag_bytes"]]
    set_flags = [i for i in range(L["flags_count"]) if (flag_bytes[i >> 3] >> (i & 7)) & 1]
    badge0 = L["flag_badge01"]
    badges = [BADGES[i] for i in range(8) if (badge0 + i) in set_flags]
    named = {}
    for f in set_flags:
        name = tables.flag_name(f)
        if name:
            named[f"0x{f:03X}"] = name
    for k, v in tables.overrides.get("flag_labels", {}).items():
        if int(k, 16) in set_flags:
            named[f"0x{int(k, 16):03X}"] = v
    vars_nonzero = {}
    for i in range(L["vars_count"]):
        v = u16(sb1, L["sb1_vars"] + 2 * i)
        if v:
            vid = L["vars_base"] + i
            vars_nonzero[f"0x{vid:04X}"] = {"value": v, "name": tables.var_name(vid)}
    lo, hi = L["trainer_flags_start"], L["trainer_flags_end"]
    trainer_flags = [f for f in set_flags if lo <= f <= hi]
    if tables.flag_id("FLAG_SYS_POKEMON_GET") not in set_flags:
        s.partial("FLAG_SYS_POKEMON_GET is not set — the flag block may be misread")
    return {"badges": badges, "badge_count": len(badges), "named_flags_set": named,
            "flags_set": [f"0x{f:03X}" for f in set_flags], "flags_set_count": len(set_flags),
            "trainers_defeated_flags": len(trainer_flags),
            "story_flags_set_count": len([f for f in set_flags if f < lo]),
            "system_flags_set_count": len([f for f in set_flags if f >= L["sys_flags"]]),
            "vars_nonzero": vars_nonzero}


# ---------------------------------------------------------------- bag
def _slot_pocket(buf, off, slots, tables, key16, xor_quantity):
    items, empty, problems = [], 0, []
    for k in range(slots):
        iid, q = u16(buf, off + 4 * k), u16(buf, off + 4 * k + 2)
        qty = (q ^ key16) if xor_quantity else q
        if iid == 0:
            empty += 1
            continue
        name = tables.item_name(iid)
        items.append({"id": iid, "name": name, "qty": qty, "pocket": tables.item_pocket(iid)})
        if iid >= tables.layout["items_count"] or iid not in tables.items:
            problems.append(f"item id {iid} is not a known FRLG+ item")
        if not 1 <= qty <= 999:
            problems.append(f"{name}: quantity {qty} outside 1-999")
    return items, empty, problems


def _tmhm_pocket(buf, off, tables):
    items, problems = [], []
    nbytes = tables.layout["bag_tmhm_bytes"]
    bits = buf[off:off + nbytes]
    for index, (item_id, _move_id, move_name) in enumerate(tables.tmhm):
        if (bits[index // 8] >> (index % 8)) & 1:
            items.append({"id": item_id, "name": tables.item_name(item_id), "qty": 1,
                          "pocket": "tm_case", "move": move_name})
    spare = tables.layout["tmhm_count"]
    for index in range(spare, nbytes * 8):
        if (bits[index // 8] >> (index % 8)) & 1:
            problems.append(f"bit {index} is set but there are only {spare} TMs/HMs")
    return items, len(tables.tmhm) - len(items), problems


def _key_item_pocket(buf, off, tables):
    """Key Items, decoded the way DeserializeKeyItemSlots builds the pocket.

    SerializeKeyItemSlots never zeroes a slot it stops using, so after the pocket
    compacts, stale bytes repeat an item further down. The game feeds every slot
    through AddBagItem, which merges a repeat into the existing slot, so the bag
    shows it once — this does the same and counts the stale slots.
    """
    items, empty, problems, seen, stale = [], 0, [], {}, 0
    for k in range(tables.layout["bag_key_items_count"]):
        stored = buf[off + k]
        if stored == 0:
            empty += 1
            continue
        item_id = tables.key_item_indices.get(stored)
        if item_id is None:
            problems.append(f"slot {k} holds {stored}, which is not a key-item index")
            continue
        if item_id in seen:
            stale += 1
            continue
        seen[item_id] = k
        items.append({"id": item_id, "name": tables.item_name(item_id), "qty": 1,
                      "pocket": "key_items", "stored_index": stored})
    return items, empty + stale, problems, stale


def _parse_items(sb1, key, tables, s, diag):
    L = tables.layout
    key16 = key & 0xFFFF
    pockets, problems = [], []

    def add(name, offset, capacity, fmt, items, empty, probs):
        pockets.append({"name": name, "region": "sb1", "offset": offset,
                        "offset_hex": f"0x{offset:04X}", "capacity": capacity, "format": fmt,
                        "items": items, "used": len(items), "empty_slots": empty})
        problems.extend(f"{name}: {p}" for p in probs)

    for name, off_key, cap_key in [("Items", "sb1_bag_items", "bag_items_count"),
                                   ("Medicine", "sb1_bag_medicine", "bag_medicine_count"),
                                   ("Held Items", "sb1_bag_held_items", "bag_held_items_count"),
                                   ("Poké Balls", "sb1_bag_poke_balls", "bag_poke_balls_count"),
                                   ("Berry Pouch", "sb1_bag_berries", "bag_berries_count")]:
        items, empty, probs = _slot_pocket(sb1, L[off_key], L[cap_key], tables, key16, True)
        add(name, L[off_key], L[cap_key], "item_slots_xor_quantity", items, empty, probs)

    items, empty, probs = _tmhm_pocket(sb1, L["sb1_bag_tmhm_bits"], tables)
    add("TM Case", L["sb1_bag_tmhm_bits"], L["tmhm_count"], "bitfield", items, empty, probs)

    items, empty, probs, stale = _key_item_pocket(sb1, L["sb1_bag_key_items"], tables)
    add("Key Items", L["sb1_bag_key_items"], L["bag_key_items_count"], "one_byte_indices",
        items, empty, probs)
    if stale:
        pockets[-1]["note"] = (f"{stale} slot(s) still hold a byte for an item that already appears "
                               f"earlier — FRLG+ never clears a slot it stops using, and the game "
                               f"merges the repeat, so the bag shows each item once")

    items, empty, probs = _slot_pocket(sb1, L["sb1_pc_items"], L["pc_items_count"], tables, key16, False)
    add("PC item storage", L["sb1_pc_items"], L["pc_items_count"], "item_slots_raw_quantity",
        items, empty, probs)

    # Strong cross-check: the game only ever puts an item in its own pocket, so every
    # item's pocket field must match the pocket it was decoded from.
    expected = {"Items": "items", "Medicine": "medicine", "Held Items": "held_items",
                "Poké Balls": "poke_balls", "Berry Pouch": "berry_pouch", "TM Case": "tm_case",
                "Key Items": "key_items"}
    misplaced = []
    for p in pockets:
        want = expected.get(p["name"])
        if not want:
            continue        # PC item storage can hold anything
        for it in p["items"]:
            if it["pocket"] != want:
                misplaced.append(f"{it['name']} is a {it['pocket']} item but sits in {p['name']}")
    problems.extend(misplaced)

    unknown = sorted({it["id"] for p in pockets for it in p["items"] if it["id"] not in tables.items})
    if unknown:
        diag["needs_input"].append(
            f"Item ids {unknown} are in the bag but not in the generated item table — if the ROM is a "
            "newer FRLG+ version than the source checkout, re-run tools/generate_tables.py.")
    if problems:
        s.partial("; ".join(problems[:6]) + (" …" if len(problems) > 6 else ""))
    return {"source": "generated from include/global.h + src/item.c",
            "pockets": pockets,
            "pocket_coherence_ok": not misplaced,
            "totals": {p["name"]: p["used"] for p in pockets},
            "unknown_item_ids": unknown,
            "problems": problems}


# ---------------------------------------------------------------- day care, roamer, masters
def _parse_day_care(sb1, tables, player, key_system, s):
    L = tables.layout
    modes = _calc_modes(key_system)

    def one(base, count, label):
        mons = []
        for i in range(count):
            off = base + i * L["daycare_mon_size"]
            m = decode_pokemon(sb1[off:off + 80], tables, player, where=f"{label} slot {i + 1}",
                               calc_modes=modes, profile=PROFILE_MODULE)
            if m:
                m["steps"] = u32(sb1, off + L["daycare_mon_size"] - 4)
                mons.append(m)
        return {"offset_hex": f"0x{base:04X}", "pokemon": mons, "count": len(mons)}

    # Route 5 holds one Pokémon and does not breed; Four Island holds two and does,
    # so the egg step counter and offspring personality belong to Four Island.
    out = {"four_island": one(L["sb1_daycare"], L["daycare_mon_count"], "Four Island Day Care"),
           "route_5": one(L["sb1_route5_daycare_mon"], 1, "Route 5 Day Care"),
           "step_counter": sb1[L["sb1_daycare_step_counter"]],
           "step_counter_belongs_to": "Four Island (breeding) Day Care",
           "offspring_personality": u32(sb1, L["sb1_daycare_offspring"])}
    out["egg_waiting"] = bool(out["offspring_personality"]) and out["four_island"]["count"] == 2
    bad = [m for grp in ("four_island", "route_5") for m in out[grp]["pokemon"]
           if m["checks"]["checksum"] != "ok"]
    if bad:
        s.partial(f"{len(bad)} Day Care slot(s) failed checksum")
    return out


def _parse_roamer(sb1, tables, player):
    L = tables.layout
    base = L["sb1_roamer"]
    active = bool(sb1[base + 0x13])
    species_id = u16(sb1, base + 8)
    if not active or not species_id:
        return {"active": False,
                "note": "no legendary is roaming (FRLG+ starts one when you first enter the Hall of Fame)"}
    iv_word = u32(sb1, base + 0)
    personality = u32(sb1, base + 4)
    ivs = [(iv_word >> (5 * i)) & 31 for i in range(6)]
    name, national, _rec = tables.species_info(species_id)
    tid, sid = (player or {}).get("tid", 0), (player or {}).get("sid", 0)
    shiny = (tid ^ sid ^ (personality & 0xFFFF) ^ (personality >> 16)) < 8
    return {"active": True, "species": name, "species_id": species_id, "dex": national,
            "level": sb1[base + 0xC], "hp": u16(sb1, base + 0xA), "status_raw": sb1[base + 0xD],
            "ivs": to_display(ivs), "personality": f"{personality:08X}",
            "nature": NATURES[personality % 25], "nature_effect": nature_effect(personality % 25),
            "shiny": shiny,
            "note": "its current map lives in RAM, not in the save"}


def _parse_master_trainers(sb1, tables):
    L = tables.layout
    cleared = []
    for national in range(1, MASTER_TRAINER_SPECIES_MAX + 1):
        byte = sb1[L["sb1_master_trainer_flags"] + national // 8]
        if (byte >> (national % 8)) & 1:
            rec = tables.species_by_national.get(national)
            cleared.append(rec["name"] if rec else f"#{national}")
    return {"title": _master_trainer_title(sb1[L["sb1_master_trainer_title"]], tables),
            "cleared": cleared, "cleared_count": len(cleared),
            "total": MASTER_TRAINER_SPECIES_MAX}


def _scan_other_pokemon(sb1, pc, tables, player, key_system):
    """Valid Pokémon structures outside everything the known layout accounts for."""
    L = tables.layout
    known = [(L["sb1_party"], L["sb1_party"] + 600),
             (L["sb1_daycare"], L["sb1_daycare"] + L["daycare_mon_count"] * L["daycare_mon_size"]),
             (L["sb1_route5_daycare_mon"], L["sb1_route5_daycare_mon"] + L["daycare_mon_size"])]
    hits = []
    for rname, buf, skips in [("sb1", sb1, known), ("pc_tail", pc[L["pc_end"]:], [])]:
        off = 0
        while off + 80 <= len(buf):
            skipped = next((hi for lo, hi in skips if lo <= off < hi), None)
            if skipped is not None:
                off = skipped
                continue
            if quick_valid_boxmon(buf[off:off + 80], MAX_SPECIES):
                m = decode_pokemon(buf[off:off + 80], tables, player, where=f"{rname} @0x{off:04X}",
                                   calc_modes=_calc_modes(key_system), profile=PROFILE_MODULE)
                m["storage_guess"] = "unknown storage (Quest Log snapshot?)"
                hits.append(m)
                off += 80
            else:
                off += 4
    return hits


def _parse_hof(raw, tables, L):
    data = b""
    base0 = L["sector_id_hof_1"] * 0x1000
    for base in (base0, base0 + 0x1000):
        chunk = raw[base:base + 0x1000]
        if len(chunk) < 0x1000 or u32(chunk, 0xFF8) != 0x08012025:
            return {"entries": [], "count": 0, "note": "no Hall of Fame data"}
        data += chunk[:SECTOR_DATA_SIZE]
    entries = []
    for t in range(50):
        team = []
        for i in range(6):
            off = (t * 6 + i) * 20
            if off + 20 > len(data):
                break
            sp_lvl = u16(data, off + 8)
            species, level = sp_lvl & 0x1FF, sp_lvl >> 9
            if species == 0:
                continue
            name, _, _ = tables.species_info(species)
            team.append({"species": name, "level": level,
                         "nickname": decode_text(data[off + 10:off + 20])})
        if not team:
            break
        entries.append(team)
    return {"entries": entries, "count": len(entries)}


# ---------------------------------------------------------------- hints
def _all_mons(res):
    mons = list(res.get("party", []))
    for bx in res.get("boxes", {}).get("boxes", []):
        mons.extend(bx["pokemon"])
    for grp in ("four_island", "route_5"):
        mons.extend(res.get("day_care", {}).get(grp, {}).get("pokemon", []))
    return mons


def _catch_locations(tables, national, version, limit=3):
    """Short human strings saying where this species can be caught in FRLG+."""
    rows = tables.encounters_for(national)
    if not rows:
        return []
    short = {"FR": "FireRed", "LG": "LeafGreen"}
    mine = [r for r in rows if r["version"] == version] or rows
    by_place = {}
    for r in mine:
        # Group by region-map area, not by individual map: "Cerulean Cave" reads better
        # than six floors of it, and it is the name shown on the Town Map.
        label = tables.map_region(r["map"]) or tables.map_label(r["map"]) or f"map {r['map']}"
        entry = by_place.setdefault((label, r["method"]),
                                    {"lo": r["min_level"], "hi": r["max_level"], "rate": r["rate"],
                                     "versions": set()})
        entry["lo"] = min(entry["lo"], r["min_level"])
        entry["hi"] = max(entry["hi"], r["max_level"])
        entry["rate"] = max(entry["rate"], r["rate"])
        entry["versions"].add(short[r["version"]])
    ordered = sorted(by_place.items(), key=lambda kv: (-kv[1]["rate"], kv[0][0]))
    out = []
    for (label, method), info in ordered[:limit]:
        levels = f"Lv{info['lo']}" if info["lo"] == info["hi"] else f"Lv{info['lo']}-{info['hi']}"
        versions = "" if len(info["versions"]) == 2 else f", {next(iter(info['versions']))} only"
        out.append(f"{label} ({method}, {levels}{versions})")
    if len(ordered) > limit:
        out.append(f"+{len(ordered) - limit} more places")
    return out


def _hints(res, tables):
    hints = {}
    mons = [m for m in _all_mons(res) if m["checks"]["checksum"] == "ok" and not m["is_egg"]]
    flags = set(res.get("progress", {}).get("flags_set", []))
    badge0 = tables.layout["flag_badge01"]
    cap = 10
    for index, value in OBEDIENCE_CAPS:
        if f"0x{badge0 + index:03X}" in flags:
            cap = value
            break
    hints["obedience_cap_for_outsiders"] = cap
    hints["outsiders"] = [{"where": m["where"], "species": m["species"], "nickname": m["nickname"],
                           "level": m["level"], "class": m["origin"].get("class"),
                           "over_cap": (m["level"] or 0) > cap}
                          for m in mons if m["origin"].get("game_treats_as_outsider")]
    overdue = []
    for m in mons:
        evo = tables.level_evolution(m["species_id"])
        if evo and m["level"] and m["level"] >= evo[0] and m.get("held_item") != "Everstone":
            overdue.append({"where": m["where"], "species": m["species"], "nickname": m["nickname"],
                            "level": m["level"], "evolves_at": evo[0], "into": evo[1]})
    hints["evolutions_overdue"] = overdue
    hints["party_field_moves"] = sorted({f"{mv['name']} ({m['nickname'] or m['species']})"
                                         for m in res.get("party", []) for mv in m["moves"]
                                         if mv["name"] in HM_MOVES})
    hints["shinies"] = [f"{m['species']} ({m['where']})" for m in mons if m["shiny"]]

    # Boxed slots with no recorded HP are a trap if either key is ever switched on:
    # PopulateBoxHpAndStatusToPartyMon copies the stored 0 straight into HP, so the
    # Pokémon comes out of the box fainted (see src/pokemon_storage_system_data.c).
    unrecorded = [m for m in _all_mons(res)
                  if m["where"].startswith("box") and m.get("box_hp_recorded") is False]
    ks = res.get("key_system", {})
    if unrecorded:
        hints["boxed_hp_not_recorded"] = {
            "count": len(unrecorded),
            "where": sorted({m["where"].split(" slot")[0] for m in unrecorded}),
            "risk": ("Turning on No Free Heals or Nuzlocke would make these withdraw at 0 HP, "
                     "because the game copies the stored boxed HP straight into HP and these "
                     "slots store zero. Both keys are off right now, so they come out at full HP."
                     if not (ks.get("no_free_heals") or ks.get("nuzlocke")) else
                     "A key that reads boxed HP is ON, so these withdraw at 0 HP — revive them "
                     "before relying on them."),
            "not_a_decode_problem": True}

    # Two Pokémon carrying the starter's met data (Pallet Town at the starter's level)
    # cannot both have been obtained normally — the signature of a transplanted save.
    starters = [m for m in mons
                if m["origin"].get("met_location") == "Pallet Town" and m["origin"].get("met_level") == 5]
    if len(starters) > 1:
        hints["multiple_starter_origins"] = {
            "pokemon": [f"{m['nickname'] or m['species']} ({m['where']})" for m in starters],
            "note": ("More than one Pokémon carries the starter's origin (Pallet Town, Lv5). Only one "
                     "can have been chosen in this playthrough, so the others were put into this save "
                     "from outside it. Their OT name, trainer ID and met data were made to match, so "
                     "`origin.class` cannot pick them out.")}
    hints["pokerus"] = [f"{m['species']} ({m['where']}): {m['pokerus']}"
                        for m in mons if m["pokerus"] != "none"]

    dex = res.get("pokedex", {})
    owned = set(dex.get("owned", []))
    have = {m["dex"] for m in mons if m["dex"]}
    hints["species_held_but_not_marked_owned"] = sorted(have - owned)
    version = res.get("key_system", {}).get("version", "FireRed")
    version_code = "LG" if version == "LeafGreen" else "FR"
    missing = []
    for national in tables.extended_dex:
        if national in owned:
            continue
        rec = tables.species_by_national.get(national)
        if not rec:
            continue
        missing.append({"dex": national, "species": rec["name"],
                        "where": _catch_locations(tables, national, version_code)})
    hints["missing_extended_dex"] = missing
    hints["missing_extended_dex_count"] = len(missing)
    hints["missing_kanto"] = [f"#{n:03d} {tables.species_by_national[n]['name']}"
                              for n in range(1, 152)
                              if n not in owned and n in tables.species_by_national]
    hints["missing_johto_count"] = len([n for n in range(152, 252) if n not in owned])
    hints["missing_hoenn_count"] = len([n for n in range(252, 387) if n not in owned])
    hints["owned_species_not_currently_held"] = len(owned - have)
    hints["dex_version_note"] = (
        f"Catch locations are for the {version} side of the Key System version toggle.")
    return hints
