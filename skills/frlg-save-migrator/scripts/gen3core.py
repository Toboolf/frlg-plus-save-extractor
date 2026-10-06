"""Generic Generation III save + Pokémon decoding.

Shared by every Gen 3 profile (FRLG+, vanilla FR/LG, and others later). Nothing
in here knows where a particular game keeps its bag or flags, or what a ROM hack
stores in padding; that lives in the profile module (e.g. frlgplus.py).
"""
from __future__ import annotations

import copy
import json
import os
import struct

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

SECTOR_SIZE = 0x1000
SECTOR_DATA_SIZE = 0xF80
SECTORS_PER_SLOT = 14
SAVE_SIGNATURE = 0x08012025


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def s16(b, o):
    return struct.unpack_from("<h", b, o)[0]


# --------------------------------------------------------------------------
# Text (Gen 3 western character set)
# --------------------------------------------------------------------------
def _build_charmap():
    m = {0x00: " "}
    m.update({0x01: "À", 0x02: "Á", 0x03: "Â", 0x04: "Ç", 0x05: "È", 0x06: "É", 0x07: "Ê", 0x08: "Ë",
              0x09: "Ì", 0x0B: "Î", 0x0C: "Ï", 0x0D: "Ò", 0x0E: "Ó", 0x0F: "Ô", 0x10: "Œ", 0x11: "Ù",
              0x12: "Ú", 0x13: "Û", 0x14: "Ñ", 0x15: "ß", 0x16: "à", 0x17: "á", 0x19: "ç", 0x1A: "è",
              0x1B: "é", 0x1C: "ê", 0x1D: "ë", 0x1E: "ì", 0x20: "î", 0x21: "ï", 0x22: "ò", 0x23: "ó",
              0x24: "ô", 0x25: "œ", 0x26: "ù", 0x27: "ú", 0x28: "û", 0x29: "ñ", 0x2A: "º", 0x2B: "ª",
              0x2D: "&", 0x2E: "+", 0x35: "=", 0x36: ";", 0x51: "¿", 0x52: "¡", 0x5A: "Í", 0x5B: "%",
              0x5C: "(", 0x5D: ")", 0x68: "â", 0x6F: "í", 0x85: "<", 0x86: ">"})
    for i in range(10):
        m[0xA1 + i] = str(i)
    m.update({0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-", 0xAF: "·", 0xB0: "…", 0xB1: "“", 0xB2: "”",
              0xB3: "‘", 0xB4: "’", 0xB5: "♂", 0xB6: "♀", 0xB7: "$", 0xB8: ",", 0xB9: "×", 0xBA: "/",
              0xEF: "▶", 0xF0: ":", 0xF1: "Ä", 0xF2: "Ö", 0xF3: "Ü", 0xF4: "ä", 0xF5: "ö", 0xF6: "ü"})
    for i in range(26):
        m[0xBB + i] = chr(ord("A") + i)
        m[0xD5 + i] = chr(ord("a") + i)
    return m


CHARMAP = _build_charmap()
REVERSE_CHARMAP = {v: k for k, v in CHARMAP.items()}


def decode_text(b):
    out = []
    for c in b:
        if c == 0xFF:
            break
        out.append(CHARMAP.get(c, f"[{c:02X}]"))
    return "".join(out).rstrip()


def encode_text(s, length):
    """Inverse of decode_text (used by tests and calibration tooling)."""
    data = [REVERSE_CHARMAP[ch] for ch in s][:length]
    if len(data) < length:
        data.append(0xFF)
    data += [0xFF] * (length - len(data))
    return bytes(data)


# --------------------------------------------------------------------------
# Save slots and sectors
# --------------------------------------------------------------------------
def _checksum_match(data, stored):
    """Gen 3 sector checksum: sum of u32 words, folded to 16 bits.

    Hacks may change how many bytes of a sector a section uses, so instead of
    trusting a fixed length we look for any length (multiple of 4) whose
    checksum matches. Zero padding never changes the sum, so the match range
    tells us where the real data ends.
    """
    total = 0
    first = last = None
    last_nonzero = 0
    for i in range(0, SECTOR_DATA_SIZE, 4):
        w = u32(data, i)
        if w:
            last_nonzero = i + 4
        total = (total + w) & 0xFFFFFFFF
        folded = ((total >> 16) + (total & 0xFFFF)) & 0xFFFF
        if folded == stored:
            if first is None:
                first = i + 4
            last = i + 4
    return first, last, last_nonzero


def read_slot(raw, slot_index):
    base = slot_index * SECTORS_PER_SLOT * SECTOR_SIZE
    sections, sector_info, problems = {}, [], []
    counters = set()
    for n in range(SECTORS_PER_SLOT):
        off = base + n * SECTOR_SIZE
        chunk = raw[off:off + SECTOR_SIZE]
        if len(chunk) < SECTOR_SIZE:
            problems.append(f"sector {n}: file too short")
            continue
        sid, chk, sig, cnt = u16(chunk, 0xFF4), u16(chunk, 0xFF6), u32(chunk, 0xFF8), u32(chunk, 0xFFC)
        info = {"physical_sector": base // SECTOR_SIZE + n, "section_id": sid, "counter": cnt,
                "signature_ok": sig == SAVE_SIGNATURE}
        if sig != SAVE_SIGNATURE:
            problems.append(f"sector {n}: bad signature")
            sector_info.append(info)
            continue
        first, last, data_end = _checksum_match(chunk, chk)
        info.update({"checksum_ok": first is not None, "data_end": data_end,
                     "checksum_len_range": [first, last] if first else None})
        if first is None:
            problems.append(f"sector {n} (section {sid}): checksum mismatch")
        if sid in sections:
            problems.append(f"section {sid} appears twice")
        sections[sid] = chunk[:SECTOR_DATA_SIZE]
        counters.add(cnt)
        sector_info.append(info)
    missing = [i for i in range(SECTORS_PER_SLOT) if i not in sections]
    if missing:
        problems.append(f"missing sections {missing}")
    if len(counters) > 1:
        problems.append(f"mixed save counters {sorted(counters)}")
    return {"slot": "A" if slot_index == 0 else "B", "valid": not problems,
            "counter": max(counters) if counters else None, "sections": sections,
            "sector_info": sector_info, "problems": problems}


def choose_slot(raw):
    slots = [read_slot(raw, 0), read_slot(raw, 1)]
    valid = [s for s in slots if s["valid"]]
    if valid:
        best = max(valid, key=lambda s: s["counter"])
    else:  # fall back to the most complete slot so we can still salvage data
        best = max(slots, key=lambda s: (len(s["sections"]), s["counter"] or 0))
    other = slots[1] if best is slots[0] else slots[0]
    return best, other


# --------------------------------------------------------------------------
# Static tables
# --------------------------------------------------------------------------
NATURES = ["Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile", "Relaxed", "Impish", "Lax",
           "Timid", "Hasty", "Serious", "Jolly", "Naive", "Modest", "Mild", "Quiet", "Bashful", "Rash",
           "Calm", "Gentle", "Sassy", "Careful", "Quirky"]
NATURE_STATS = ["Atk", "Def", "Spe", "SpA", "SpD"]
HP_TYPES = ["Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost", "Steel", "Fire", "Water",
            "Grass", "Electric", "Psychic", "Ice", "Dragon", "Dark"]
BALLS = {0: "(none)", 1: "Master Ball", 2: "Ultra Ball", 3: "Great Ball", 4: "Poké Ball", 5: "Safari Ball",
         6: "Net Ball", 7: "Dive Ball", 8: "Nest Ball", 9: "Repeat Ball", 10: "Timer Ball",
         11: "Luxury Ball", 12: "Premier Ball"}
GAMES = {0: "Colosseum bonus disc", 1: "Sapphire", 2: "Ruby", 3: "Emerald", 4: "FireRed",
         5: "LeafGreen", 15: "Colosseum/XD"}
LANGUAGES = {1: "Japanese", 2: "English", 3: "French", 4: "Italian", 5: "German", 7: "Spanish"}
RIBBON_SINGLE = ["Champion", "Winning", "Victory", "Artist", "Effort", "Marine", "Land", "Sky",
                 "Country", "National", "Earth", "World"]
CONTEST_RANKS = ["", "Normal", "Super", "Hyper", "Master"]
STAT_KEYS_INTERNAL = ["HP", "Atk", "Def", "Spe", "SpA", "SpD"]   # order used inside the save
STAT_KEYS_DISPLAY = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]     # order shown to humans
HM_MOVES = {"Cut", "Fly", "Surf", "Strength", "Flash", "Rock Smash", "Waterfall", "Dive"}


def deep_merge(base, overlay):
    """Merge `overlay` over `base`, returning a new dict that shares no mutable
    structure with either input — every nested dict in the result is its own
    object, so mutating the result later cannot reach back into base or overlay.

    Objects merge key by key so an overlay can change one field without
    restating its siblings. A `null` in the overlay DELETES the key rather
    than setting it to null — that is how a personal overlay retires a
    question (`"confirm_by": null`) instead of answering it with nothing.
    """
    out = {key: copy.deepcopy(value) for key, value in base.items()}
    for key, value in overlay.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Tables:
    """Every name, stat and offset table, loaded from scripts/data.

    Those files are generated from a game's source by tools/generate_tables.py —
    nothing here is hand-written, so a new game version only needs a re-run.

    `layout` names the save-layout table of the profile in use and is required
    and keyword-only: pass `layout=<profile>.LAYOUT`. There is deliberately no
    default, because a default would silently hand one game's offsets to another
    game's save — the Day Care alone sits four bytes apart between FRLG+ and
    vanilla, and the bag is a different region entirely.
    """

    def __init__(self, data_dir=DATA_DIR, *, layout):
        self.data_dir = data_dir
        self.layout_file = layout
        layout_path = os.path.join(data_dir, layout)
        if not os.path.isfile(layout_path):
            raise FileNotFoundError(
                f"layout table {layout!r} not found at {layout_path} — "
                f"run tools/generate_tables.py for that game first")
        self.layout = {name: int(cols[0]) for name, cols in self._rows(layout, 1)}

        self.species = {}            # internal id -> record
        self.species_by_national = {}
        self.by_name = {}
        for key, cols in self._rows("species.txt", 1):
            internal = int(key)
            abilities = cols[6].split("/")
            rec = {"internal": internal, "national": int(cols[0]), "name": cols[1],
                   "base": [int(x) for x in cols[2].split(",")], "types": cols[3].split("/"),
                   "gender": int(cols[4]), "growth": cols[5],
                   "abilities": abilities + [abilities[0]] * (2 - len(abilities))}
            self.species[internal] = rec
            self.species_by_national[rec["national"]] = rec
            self.by_name[rec["name"]] = rec
        self.internal_to_national = {k: v["national"] for k, v in self.species.items()}

        self.moves = {int(k): (c[0], int(c[1])) for k, c in self._rows("moves.txt", 1)}
        self.items = {int(k): {"name": c[0], "pocket": c[1]} for k, c in self._rows("items.txt", 1)}
        self.mapsec = {int(k): c[0] for k, c in self._rows("mapsec.txt", 1)}
        self.maps = {k: {"const": c[0], "mapsec": int(c[1]), "folder": c[2], "label": c[3]}
                     for k, c in self._rows("maps.txt", 1)}
        self.flag_names = {int(k, 16): c[0] for k, c in self._rows("flags.txt", 1)}
        self.flag_ids = {v: k for k, v in self.flag_names.items()}
        self.var_names = {int(k, 16): c[0] for k, c in self._rows("vars.txt", 1)}
        self.game_stats = {int(k): (c[0], c[1]) for k, c in self._rows("game_stats.txt", 1)}
        self.game_stat_ids = {v[0]: k for k, v in self.game_stats.items()}
        self.key_item_indices = {int(k): int(c[0]) for k, c in self._rows("key_item_indices.txt", 1)}
        self.tmhm = [(int(c[0]), int(c[1]), c[2]) for _, c in self._rows("tmhm.txt", 1)]

        self.evolutions = {}
        for key, cols in self._rows("evolutions.txt", 1):
            self.evolutions.setdefault(int(key), []).append(
                {"method": cols[0], "param": int(cols[1]), "into": int(cols[2])})

        self.extended_dex = []
        for _, cols in self._rows("extended_dex.txt", 1):
            rec = self.by_name.get(_species_name_from_suffix(cols[0], self))
            if rec:
                self.extended_dex.append(rec["national"])

        self.encounters = {}
        for key, cols in self._rows("encounters.txt", 1):
            rec = self.species.get(int(key))
            if not rec:
                continue
            self.encounters.setdefault(rec["national"], []).append(
                {"version": cols[0], "map": cols[1], "method": cols[2],
                 "min_level": int(cols[3]), "max_level": int(cols[4]), "rate": int(cols[5])})

        with open(os.path.join(data_dir, "verification.json"), encoding="utf-8") as f:
            self.verification = json.load(f)
        # An optional, gitignored local.json personalises the public record:
        # what this player has confirmed in game, and where their tracker lives.
        local_path = os.path.join(data_dir, "local.json")
        self.has_local = os.path.exists(local_path)
        if self.has_local:
            with open(local_path, encoding="utf-8") as f:
                try:
                    local = json.load(f)
                except json.JSONDecodeError as e:
                    raise ValueError(f"local.json is not valid JSON: {e}") from e
            if not isinstance(local, dict):
                raise ValueError(
                    f"local.json must contain a JSON object, got {type(local).__name__}")
            self.verification = deep_merge(self.verification, local)
        self.overrides = self.verification.get("overrides", {})
        for k, v in self.overrides.get("item_names_extra", {}).items():
            self.items.setdefault(int(k, 0), {"name": v, "pocket": "unknown"})["name"] = v

    def _rows(self, filename, key_cols=1):
        """Yield (first column, [remaining columns]) for one generated table."""
        out = []
        with open(os.path.join(self.data_dir, filename), encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.rstrip("\n").split("|")
                out.append((parts[0], parts[key_cols:]))
        return out

    # ---- lookups ---------------------------------------------------------
    def species_info(self, internal_id):
        """Returns (display name, national number or None, data record or None)."""
        if internal_id == self.layout["num_species"]:
            return "Egg", None, None
        unown = self.by_name.get("Unown")
        if unown and self.layout["num_species"] < internal_id <= self.layout["num_species"] + 27:
            return "Unown", unown["national"], unown          # Unown letter slots (graphics only)
        rec = self.species.get(internal_id)
        if rec is None:
            return f"Unknown species #{internal_id}", None, None
        return rec["name"], rec["national"], rec

    def item_name(self, item_id):
        if item_id == 0:
            return None
        rec = self.items.get(item_id)
        return rec["name"] if rec else f"Unknown item #{item_id}"

    def item_pocket(self, item_id):
        rec = self.items.get(item_id)
        return rec["pocket"] if rec else "unknown"

    def move(self, move_id):
        return self.moves.get(move_id, (f"Unknown move #{move_id}", None))

    def location(self, loc_id):
        return self.mapsec.get(loc_id, f"Location #{loc_id}")

    def map_label(self, group_num):
        rec = self.maps.get(group_num)
        return rec["label"] if rec else None

    def map_region(self, group_num):
        rec = self.maps.get(group_num)
        return self.mapsec.get(rec["mapsec"]) if rec else None

    def flag_name(self, flag_id):
        return self.flag_names.get(flag_id)

    def flag_id(self, name):
        return self.flag_ids[name]

    def var_name(self, var_id):
        return self.var_names.get(var_id)

    def game_stat_id(self, name):
        return self.game_stat_ids[name]

    def game_stat_label(self, index):
        return self.game_stats.get(index, (None, None))[1]

    def encounters_for(self, national):
        return self.encounters.get(national, [])

    def level_evolution(self, internal_id):
        """(level, target name) for a plain level-up evolution, else None."""
        for evo in self.evolutions.get(internal_id, []):
            if evo["method"] == "level":
                target = self.species.get(evo["into"])
                if target:
                    return evo["param"], target["name"]
        return None


def _species_name_from_suffix(suffix, tables):
    """EXTENDED_DEX_* suffix -> the species name in the generated species table."""
    key = suffix.replace("_", "").lower()
    if not hasattr(tables, "_suffix_index"):
        tables._suffix_index = {}
        for rec in tables.species.values():
            norm = "".join(ch for ch in rec["name"].lower() if ch.isalnum())
            tables._suffix_index.setdefault(norm, rec["name"])
        tables._suffix_index.setdefault("nidoranf", "Nidoran\u2640")
        tables._suffix_index.setdefault("nidoranm", "Nidoran\u2642")
        tables._suffix_index.setdefault("farfetchd", "Farfetch'd")
        tables._suffix_index.setdefault("mrmime", "Mr. Mime")
        tables._suffix_index.setdefault("hooh", "Ho-Oh")
    return tables._suffix_index.get(key, suffix.title())


# --------------------------------------------------------------------------
# Experience, stats and other formulas
# --------------------------------------------------------------------------
def exp_for_level(rate, n):
    if n <= 1:
        return 0
    if rate == "MF":
        return n ** 3
    if rate == "ER":
        if n <= 50:
            return n ** 3 * (100 - n) // 50
        if n <= 68:
            return n ** 3 * (150 - n) // 100
        if n <= 98:
            return n ** 3 * ((1911 - 10 * n) // 3) // 500
        return n ** 3 * (160 - n) // 100
    if rate == "FL":
        if n <= 15:
            return n ** 3 * ((n + 1) // 3 + 24) // 50
        if n <= 36:
            return n ** 3 * (n + 14) // 50
        return n ** 3 * (n // 2 + 32) // 50
    if rate == "MS":
        return 6 * n ** 3 // 5 - 15 * n ** 2 + 100 * n - 140
    if rate == "F":
        return 4 * n ** 3 // 5
    if rate == "S":
        return 5 * n ** 3 // 4
    raise ValueError(rate)


def level_from_exp(rate, exp):
    level = 1
    for n in range(2, 101):
        if exp_for_level(rate, n) <= exp:
            level = n
        else:
            break
    return level


DEOXYS_FORMES = {0: "Normal", 1: "Attack", 2: "Defense", 3: "Speed"}


def apply_calc_modes(ivs, evs, modes):
    """Some games compute stats as if IVs/EVs were fixed values.

    Mirrors CalculateMonStats: the stored IVs and EVs never change, only the
    numbers the stat formula is fed. A profile decides whether any substitution
    applies and passes the modes in; `None` means "use the stored values".
    """
    if not modes:
        return ivs, evs
    iv_mode, ev_mode = modes.get("iv", "actual"), modes.get("ev", "actual")
    if iv_mode == "all 31":
        ivs = [31] * 6
    elif iv_mode == "all 0":
        ivs = [0] * 6
    if ev_mode == "all 0":
        evs = [0] * 6
    return ivs, evs


def calc_stats(base, ivs, evs, level, nature, shedinja=False):
    """All lists in internal order HP, Atk, Def, Spe, SpA, SpD."""
    if shedinja:
        hp = 1
    else:
        hp = (2 * base[0] + ivs[0] + evs[0] // 4) * level // 100 + level + 10
    out = [hp]
    inc, dec = nature // 5, nature % 5
    for i in range(1, 6):
        v = (2 * base[i] + ivs[i] + evs[i] // 4) * level // 100 + 5
        nat_idx = [None, 0, 1, 2, 3, 4][i]  # Atk, Def, Spe, SpA, SpD
        if inc != dec:
            if nat_idx == inc:
                v = v * 110 // 100
            elif nat_idx == dec:
                v = v * 90 // 100
        out.append(v)
    return out


def hidden_power(ivs):
    """ivs in internal order HP, Atk, Def, Spe, SpA, SpD."""
    t = sum(((iv & 1) << i) for i, iv in enumerate(ivs))
    p = sum((((iv >> 1) & 1) << i) for i, iv in enumerate(ivs))
    return HP_TYPES[t * 15 // 63], p * 40 // 63 + 30


def nature_effect(nature):
    inc, dec = nature // 5, nature % 5
    if inc == dec:
        return "neutral"
    return f"+{NATURE_STATS[inc]} -{NATURE_STATS[dec]}"


def gender_of(threshold, pid):
    if threshold == 255:
        return "genderless"
    if threshold == 254:
        return "F"
    if threshold == 0:
        return "M"
    return "F" if (pid & 0xFF) < threshold else "M"


def unown_letter(pid):
    v = (((pid >> 24) & 3) << 6) | (((pid >> 16) & 3) << 4) | (((pid >> 8) & 3) << 2) | (pid & 3)
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ!?"[v % 28]


def to_display(values_internal):
    d = dict(zip(STAT_KEYS_INTERNAL, values_internal))
    return {k: d[k] for k in STAT_KEYS_DISPLAY}


# --------------------------------------------------------------------------
# Pokémon structure (80-byte box form, 100-byte party form)
# --------------------------------------------------------------------------
SUB_ORDERS = ["GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA", "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
              "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG", "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG"]


def decrypt_substructures(b):
    pid, otid = u32(b, 0), u32(b, 4)
    key = pid ^ otid
    dec = bytearray(48)
    for i in range(0, 48, 4):
        struct.pack_into("<I", dec, i, u32(b, 32 + i) ^ key)
    order = SUB_ORDERS[pid % 24]
    subs = {order[k]: bytes(dec[k * 12:(k + 1) * 12]) for k in range(4)}
    checksum = sum(struct.unpack_from("<24H", dec)) & 0xFFFF
    return subs, checksum


def quick_valid_boxmon(b, max_species):
    """Cheap test used when scanning unknown memory for stray Pokémon."""
    if len(b) < 80 or not any(b[:80]):
        return False
    if b[0x12] not in LANGUAGES or not (b[0x13] & 0x02):
        return False
    subs, checksum = decrypt_substructures(b)
    if checksum != u16(b, 0x1C):
        return False
    species = u16(subs["G"], 0)
    return 1 <= species <= max_species


def decode_pokemon(raw, tables, player=None, party=False, where=None, calc_modes=None, *, profile):
    """Decode one Pokémon. Returns None for an empty slot.

    `calc_modes` is the profile's IV/EV calculation mode, used only to check
    stored stats the same way the game computed them. `profile` is required: it
    supplies decode_box_padding and boxed_hp_fields (a forgotten profile is a
    TypeError, not a silently wrong answer).
    """
    b = bytes(raw)
    if not any(b[:80]):
        return None
    pid, otid = u32(b, 0), u32(b, 4)
    subs, calc = decrypt_substructures(b)
    stored_checksum = u16(b, 0x1C)
    g, a, e, m = subs["G"], subs["A"], subs["E"], subs["M"]
    flags_byte = b[0x13]
    warnings = []

    species_id = u16(g, 0)
    name, national, rec = tables.species_info(species_id)
    held = u16(g, 2)
    exp = u32(g, 4)
    pp_bonuses = g[8]
    friendship = g[9]
    # The last halfword of substructure G is padding in vanilla FR/LG; a profile
    # whose game reuses it says so by returning its fields here.
    padding = profile.decode_box_padding(u16(g, 10))
    forme = padding.get("forme")      # None when the game stores no forme

    pokerus = m[0]
    met_loc = m[1]
    origins = u16(m, 2)
    iv_word = u32(m, 4)
    ribbon_word = u32(m, 8)
    ivs = [(iv_word >> (5 * i)) & 31 for i in range(6)]
    is_egg = bool((iv_word >> 30) & 1)
    ability_slot = (iv_word >> 31) & 1
    evs = list(e[0:6])
    contest = dict(zip(["Cool", "Beauty", "Cute", "Smart", "Tough", "Sheen"], e[6:12]))

    checksum_ok = calc == stored_checksum
    if not checksum_ok:
        warnings.append("checksum mismatch (data may be corrupt or this is not really a Pokémon)")
    if flags_byte & 0x01:
        warnings.append("game marks this as a Bad Egg")

    nature = pid % 25
    tid, sid = otid & 0xFFFF, otid >> 16
    shiny = (tid ^ sid ^ (pid & 0xFFFF) ^ (pid >> 16)) < 8

    moves = []
    for i in range(4):
        mid = u16(a, 2 * i)
        if not mid:
            continue
        mname, base_pp = tables.move(mid)
        ups = (pp_bonuses >> (2 * i)) & 3
        max_pp = base_pp + (base_pp // 5) * ups if base_pp else None
        cur = a[8 + i]
        if max_pp is not None and cur > max_pp:
            warnings.append(f"{mname}: PP {cur} > max {max_pp} (move table mismatch?)")
        moves.append({"name": mname, "id": mid, "pp": cur, "max_pp": max_pp, "pp_ups": ups})

    out = {"where": where, "uid": f"{pid:08X}-{otid:08X}",
           "species": name, "species_id": species_id, "dex": national,
           "nickname": decode_text(b[8:18]) if not is_egg else "Egg",
           "is_egg": is_egg}

    # Level & EXP
    level_stored = b[0x54] if party and len(b) >= 100 else None
    level_exp = level_from_exp(rec["growth"], exp) if rec else None
    level = level_stored if level_stored else level_exp
    out["level"] = level
    out["level_source"] = "stored" if level_stored else ("from EXP" if level_exp else None)
    out["exp"] = exp
    if rec and level and level < 100:
        out["exp_to_next"] = exp_for_level(rec["growth"], level + 1) - exp
    if level_stored and level_exp and level_stored != level_exp:
        warnings.append(f"stored level {level_stored} vs level from EXP {level_exp} (growth table?)")

    out["nature"] = NATURES[nature]
    out["nature_effect"] = nature_effect(nature)
    out["gender"] = gender_of(rec["gender"], pid) if rec else None
    out["shiny"] = shiny
    out["ability"] = rec["abilities"][ability_slot] if rec else None
    out["ability_slot"] = ability_slot
    out["held_item"] = tables.item_name(held)
    out["held_item_id"] = held or None
    out["moves"] = moves
    out["ivs"] = to_display(ivs)
    out["evs"] = to_display(evs)
    out["ev_total"] = sum(evs)
    hp_type, hp_power = hidden_power(ivs)
    out["hidden_power"] = f"{hp_type} {hp_power}"
    out["friendship"] = friendship
    strain, days = pokerus >> 4, pokerus & 0xF
    out["pokerus"] = "none" if not pokerus else ("infected" if days else "cured")
    if rec and rec["name"] == "Unown":
        out["unown_form"] = unown_letter(pid)
    if rec and rec["name"] == "Deoxys":
        if forme is not None:
            out["forme"] = DEOXYS_FORMES[forme]
    elif forme:
        out["forme_raw"] = forme
    if any(contest.values()):
        out["contest_stats"] = contest
    ribbons = []
    for i, cat in enumerate(["Cool", "Beauty", "Cute", "Smart", "Tough"]):
        rank = (ribbon_word >> (3 * i)) & 7
        if rank:
            ribbons.append(f"{cat} ({CONTEST_RANKS[min(rank, 4)]})")
    for i, rname in enumerate(RIBBON_SINGLE):
        if (ribbon_word >> (15 + i)) & 1:
            ribbons.append(rname)
    if ribbons:
        out["ribbons"] = ribbons
    marks = [s for i, s in enumerate(["●", "■", "▲", "♥"]) if (b[0x1B] >> i) & 1]
    if marks:
        out["markings"] = marks

    # Stats. Some games feed the stat formula substituted IVs/EVs (the profile
    # passes calc_modes), so check stored stats against the same numbers the game used.
    calc_ivs, calc_evs = apply_calc_modes(list(ivs), list(evs), calc_modes)
    shedinja = bool(rec and rec["name"] == "Shedinja")
    stats_computed = None
    if rec and level and not is_egg:
        stats_computed = calc_stats(rec["base"], calc_ivs, calc_evs, level, nature, shedinja=shedinja)
    if party and len(b) >= 100:
        stored = [u16(b, 0x58), u16(b, 0x5A), u16(b, 0x5C), u16(b, 0x5E), u16(b, 0x60), u16(b, 0x62)]
        out["stats"] = to_display(stored)
        out["stats_source"] = "stored"
        out["hp_current"] = u16(b, 0x56)
        out["status"] = _status_name(u32(b, 0x50))
        if stats_computed:
            out["stats_state"], note = _compare_stats(stored, stats_computed, rec, calc_ivs,
                                                      calc_evs, level, nature, shedinja)
            out["stats_match"] = out["stats_state"] == "match"
            if note:
                warnings.append(note)
    elif stats_computed:
        out["stats"] = to_display(stats_computed)
        out["stats_source"] = "computed"
        # What a boxed Pokémon's HP and status mean is the profile's call: some
        # games record them in the padding halfword, vanilla does not.
        out.update(profile.boxed_hp_fields(padding, calc_modes))

    # Origin
    met_level = origins & 0x7F
    game = (origins >> 7) & 0xF
    ball = (origins >> 11) & 0xF
    ot_name = decode_text(b[0x14:0x1B])
    origin = {"ot_name": ot_name, "ot_tid": tid, "ot_sid": sid,
              "ot_gender": "F" if origins >> 15 else "M",
              "met_location": tables.location(met_loc), "met_location_id": met_loc,
              "met_level": met_level, "hatched": met_level == 0 and not is_egg,
              "ball": BALLS.get(ball, f"Ball #{ball}"), "game": GAMES.get(game, f"Game #{game}"),
              "language": LANGUAGES.get(b[0x12], f"#{b[0x12]}"),
              "fateful_encounter": bool(ribbon_word >> 31)}
    if player:
        same_id = (tid, sid) == (player["tid"], player["sid"])
        same_name = ot_name == player["name"]
        if met_loc == 254:
            cls = "in_game_trade"
        elif same_id and same_name:
            cls = "caught_here"
        elif same_name:
            cls = "your_name_other_id"   # most likely migrated from the old vanilla save
        else:
            cls = "traded_in"
        origin["class"] = cls
        origin["game_treats_as_outsider"] = not (same_id and same_name)
    out["origin"] = origin
    out["checks"] = {"checksum": "ok" if checksum_ok else "BAD", "warnings": warnings}
    return out


def _compare_stats(stored, computed, rec, ivs, evs, level, nature, shedinja):
    """Classify a stored-vs-recomputed stat difference.

    Gen 3 only recalculates stats on level-up, evolution and a few item uses, so
    EVs gained since then leave the stored stats *below* the recomputed ones.
    That is normal; anything else points at a wrong table.
    """
    if stored == computed:
        return "match", None
    diff = [k for k, x, y in zip(STAT_KEYS_INTERNAL, stored, computed) if x != y]
    lower_only = all(x <= y for x, y in zip(stored, computed))
    if lower_only:
        for drop in range(4, 256, 4):
            older = [max(0, e - drop) for e in evs]
            if calc_stats(rec["base"], ivs, older, level, nature, shedinja=shedinja) == stored:
                return "stale", (f"stored stats are behind the current EVs on {','.join(diff)} "
                                 f"— normal: Gen 3 refreshes stats on the next level-up")
        return "stale", (f"stored stats are below the recomputed ones on {','.join(diff)} "
                         f"— most likely EVs gained since the last level-up")
    return "mismatch", (f"stored stats differ from recomputed on {','.join(diff)} "
                        f"(base-stat table or hack change?)")


def _status_name(status):
    if status & 0x7:
        return f"asleep ({status & 7} turns)"
    for bit, name in [(3, "poisoned"), (4, "burned"), (5, "frozen"), (6, "paralyzed"), (7, "badly poisoned")]:
        if status >> bit & 1:
            return name
    return "healthy"
