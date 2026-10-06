"""Turns an extraction result into: summary.md, a compact diff snapshot, and a diff."""
from __future__ import annotations

import base64
import json
import zlib

SNAPSHOT_PREFIX = "FRLGSNAP1:"
BADGE_ORDER = ["Boulder (Brock)", "Cascade (Misty)", "Thunder (Lt. Surge)", "Rainbow (Erika)",
               "Soul (Koga)", "Marsh (Sabrina)", "Volcano (Blaine)", "Earth (Giovanni)"]
MISSING_SHOWN = 30
KEY_SYSTEM_FIELDS = [("version", "Version"), ("difficulty", "Difficulty"), ("nuzlocke", "Nuzlocke"),
                     ("iv_calculation", "IV calculation"), ("ev_calculation", "EV calculation"),
                     ("no_free_heals", "No free heals"), ("exp_modifier", "Exp. modifier")]


def encode_snapshot(compact):
    """Compact snapshot -> short text code that is cheap to store in the tracker."""
    raw = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return SNAPSHOT_PREFIX + base64.b64encode(zlib.compress(raw, 9)).decode("ascii")


# ---------------------------------------------------------------- snapshot
def make_compact(res, extracted_at):
    mons = {}
    for m in _snapshot_mons(res):
        if m["checks"]["checksum"] != "ok":
            continue
        mons[m["uid"]] = {"sp": m["species"], "lv": m["level"], "nick": m["nickname"],
                          "loc": m["where"], "moves": [mv["name"] for mv in m["moves"]],
                          "item": m["held_item"], "egg": m["is_egg"]}
    items = {}
    for p in res.get("items", {}).get("pockets", []):
        for it in p["items"]:
            items[it["name"]] = items.get(it["name"], 0) + it["qty"]
    t = res.get("trainer", {})
    prog = res.get("progress", {})
    dex = res.get("pokedex", {})
    roamer = res.get("roamer", {})
    mt = res.get("master_trainers", {})
    dc = res.get("day_care", {})
    return {"schema": 2, "extracted_at": extracted_at,
            "save_counter": res.get("save_health", {}).get("save_counter"),
            "trainer": {"money": t.get("money"), "coins": t.get("coins"),
                        "play_minutes": t.get("play_minutes")},
            "key_system": res.get("key_system", {}),
            "badges": prog.get("badges", []), "dex_owned": dex.get("owned", []),
            "dex_seen": dex.get("seen", []),
            # Flag names are not stored: they come from the generated table at diff
            # time, which keeps the baseline that lives in the Doc tab small.
            "flags": prog.get("flags_set", []),
            "vars": {k: v["value"] for k, v in prog.get("vars_nonzero", {}).items()},
            "game_stats": res.get("game_stats", {}),
            "items": items,
            "roamer": {"active": roamer.get("active"), "species": roamer.get("species"),
                       "level": roamer.get("level")},
            "master_trainers": {"title": mt.get("title"), "cleared": mt.get("cleared", [])},
            "day_care": {"steps": dc.get("step_counter"), "egg": dc.get("egg_waiting")},
            "mons": mons}


def _snapshot_mons(res):
    mons = list(res.get("party", []))
    for b in res.get("boxes", {}).get("boxes", []):
        mons.extend(b["pokemon"])
    for grp in ("four_island", "route_5"):
        mons.extend(res.get("day_care", {}).get(grp, {}).get("pokemon", []))
    return mons


def load_snapshot(path):
    """Accepts the FRLGSNAP1 code, a bare JSON file, or text copied out of a Doc (fences, labels)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if SNAPSHOT_PREFIX in text:
        code = text[text.index(SNAPSHOT_PREFIX) + len(SNAPSHOT_PREFIX):]
        code = "".join(ch for ch in code.split("```")[0] if ch.isalnum() or ch in "+/=")
        return json.loads(zlib.decompress(base64.b64decode(code)).decode("utf-8"))
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found in previous snapshot")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- diff
def diff(prev, cur, tables):
    d = {"previous_extracted_at": prev.get("extracted_at"),
         "previous_save_counter": prev.get("save_counter"), "lines": []}
    L = d["lines"]
    if prev.get("save_counter") == cur.get("save_counter"):
        L.append("Same save counter as last run — the game hasn't been saved since.")
    pt, ct = prev.get("trainer", {}), cur.get("trainer", {})
    for k, label, fmt in [("money", "Money", "₽{:,}"), ("coins", "Coins", "{:,}"),
                          ("play_minutes", "Play time", None)]:
        a, b = pt.get(k), ct.get(k)
        if a is not None and b is not None and a != b:
            if k == "play_minutes":
                L.append(f"Play time +{(b - a) // 60}h {(b - a) % 60:02d}m")
            else:
                L.append(f"{label} {fmt.format(a)} → {fmt.format(b)} ({b - a:+,})")
    for bdg in set(cur.get("badges", [])) - set(prev.get("badges", [])):
        L.append(f"NEW BADGE: {bdg}")

    pk, ck = prev.get("key_system", {}), cur.get("key_system", {})
    for field, label in KEY_SYSTEM_FIELDS:
        a, b = pk.get(field), ck.get(field)
        if a is not None and b is not None and a != b:
            L.append(f"Key System — {label}: {_fmt(a)} → {_fmt(b)}")

    new_owned = sorted(set(cur.get("dex_owned", [])) - set(prev.get("dex_owned", [])))
    new_seen = sorted(set(cur.get("dex_seen", [])) - set(prev.get("dex_seen", [])) - set(new_owned))
    if new_owned:
        L.append("Pokédex — newly owned: " + ", ".join(_dex_name(tables, n) for n in new_owned))
    if new_seen:
        L.append("Pokédex — newly seen: " + ", ".join(_dex_name(tables, n) for n in new_seen))

    pm, cm = prev.get("mons", {}), cur.get("mons", {})
    for uid in cm.keys() - pm.keys():
        m = cm[uid]
        L.append(f"NEW Pokémon: {_mname(m)} Lv{m['lv']} → {m['loc']}")
    for uid in pm.keys() - cm.keys():
        m = pm[uid]
        L.append(f"GONE (released/traded?): {_mname(m)} Lv{m['lv']} (was {m['loc']})")
    for uid in cm.keys() & pm.keys():
        a, b = pm[uid], cm[uid]
        bits = []
        if a["sp"] != b["sp"]:
            bits.append(f"evolved {a['sp']} → {b['sp']}")
        if a["lv"] != b["lv"]:
            bits.append(f"Lv{a['lv']}→{b['lv']}")
        learned = [x for x in b["moves"] if x not in a["moves"]]
        forgot = [x for x in a["moves"] if x not in b["moves"]]
        if learned:
            bits.append("learned " + ", ".join(learned))
        if forgot:
            bits.append("forgot " + ", ".join(forgot))
        if a["item"] != b["item"]:
            bits.append(f"item {a['item'] or '—'} → {b['item'] or '—'}")
        if a["nick"] != b["nick"]:
            bits.append(f"renamed {a['nick']} → {b['nick']}")
        if a["loc"] != b["loc"] and (a["loc"].startswith("party") or b["loc"].startswith("party")):
            bits.append(f"moved {a['loc']} → {b['loc']}")
        if a.get("egg") and not b.get("egg"):
            bits.append("HATCHED")
        if bits:
            L.append(f"{_mname(b)}: " + "; ".join(bits))

    nf = sorted(set(cur.get("flags", [])) - set(prev.get("flags", [])))
    cf = sorted(set(prev.get("flags", [])) - set(cur.get("flags", [])))
    if nf:
        L.append(f"Event flags newly set ({len(nf)}): "
                 + ", ".join(_flag(f, tables) for f in nf[:30]) + (" …" if len(nf) > 30 else ""))
    if cf:
        L.append(f"Event flags cleared ({len(cf)}): "
                 + ", ".join(_flag(f, tables) for f in cf[:20]) + (" …" if len(cf) > 20 else ""))
    pv, cv = prev.get("vars", {}), cur.get("vars", {})
    vch = [f"{k}: {pv.get(k, 0)}→{cv.get(k, 0)}"
           for k in sorted(set(pv) | set(cv)) if pv.get(k, 0) != cv.get(k, 0)]
    if vch:
        L.append(f"Story variables changed ({len(vch)}): " + ", ".join(vch[:20])
                 + (" …" if len(vch) > 20 else ""))
    gs = [f"{k} +{cur['game_stats'][k] - prev.get('game_stats', {}).get(k, 0):,}"
          for k in cur.get("game_stats", {})
          if cur["game_stats"][k] != prev.get("game_stats", {}).get(k, cur["game_stats"][k])]
    if gs:
        L.append("Game stats: " + ", ".join(gs))

    # A schema-1 baseline has no usable bag (the old skill could not read it), so
    # comparing against it would report the whole bag as new.
    pi, ci = prev.get("items", {}), cur.get("items", {})
    if prev.get("schema", 1) >= 2 and pi:
        ich = [f"{k} {pi.get(k, 0)}→{ci.get(k, 0)}"
               for k in sorted(set(pi) | set(ci)) if pi.get(k, 0) != ci.get(k, 0)]
        if ich:
            L.append("Bag: " + ", ".join(ich))
    elif ci:
        L.append(f"Bag: now read in full ({len(ci)} kinds of item) — the previous baseline "
                 f"predates that, so there is nothing to compare yet")

    pr, cr = prev.get("roamer", {}), cur.get("roamer", {})
    if cr.get("active") and not pr.get("active"):
        L.append(f"A legendary beast started roaming: {cr.get('species')} Lv{cr.get('level')}")
    elif pr.get("active") and not cr.get("active"):
        L.append(f"The roaming {pr.get('species')} is gone (caught or defeated)")
    elif cr.get("active") and pr.get("species") != cr.get("species"):
        L.append(f"Roaming legendary changed: {pr.get('species')} → {cr.get('species')}")

    pmt, cmt = prev.get("master_trainers", {}), cur.get("master_trainers", {})
    new_mt = [x for x in cmt.get("cleared", []) if x not in pmt.get("cleared", [])]
    if new_mt:
        L.append("Master Trainers beaten: " + ", ".join(new_mt))
    if pmt.get("title") != cmt.get("title") and cmt.get("title"):
        L.append(f"Master Trainer title now: {cmt['title']}")

    pdc, cdc = prev.get("day_care", {}), cur.get("day_care", {})
    if cdc.get("egg") and not pdc.get("egg"):
        L.append("An Egg is waiting at the Day Care")

    if not L:
        L.append("No changes detected.")
    return d


def _flag(hexid, tables):
    name = tables.flag_name(int(hexid, 16))
    return f"{hexid} {name}" if name else hexid


def _dex_name(tables, national):
    rec = tables.species_by_national.get(national)
    return f"#{national} {rec['name']}" if rec else f"#{national}"


def _fmt(v):
    return {True: "on", False: "off"}.get(v, v)


def _mname(m):
    return f"{m['nick']} ({m['sp']})" if m["nick"] and m["nick"].upper() != m["sp"].upper() else m["sp"]


# ---------------------------------------------------------------- summary
def _mon_line(m):
    nick = m["nickname"]
    label = f"{nick} ({m['species']})" if nick and nick.upper() != m["species"].upper() else m["species"]
    flags = []
    if m["shiny"]:
        flags.append("★shiny")
    if m["is_egg"]:
        flags.append("egg")
    if m.get("forme") and m["forme"] != "Normal":
        flags.append(m["forme"])
    if m["checks"]["checksum"] != "ok":
        flags.append("⚠BAD CHECKSUM")
    return label + (f" [{' '.join(flags)}]" if flags else "")


def render_summary(res, changes, had_prev, extracted_at):
    out = []
    w = out.append
    sh = res.get("save_health", {})
    st = res.get("section_status", {})
    ok = sum(1 for v in st.values() if v == "ok")
    w("# FRLG+ save extract")
    w(f"Extracted {extracted_at} · file `{res['source_file']}` ({res['file_size']:,} bytes) · "
      f"slot {sh.get('active_slot')} · save #{sh.get('save_counter')} · schema {res['schema_version']}")
    w("Section status: " + " · ".join(f"{k} {v}" for k, v in st.items()) + f"  ({ok}/{len(st)} ok)")
    ver = res.get("verification", {}).get("sections", {})
    if ver:
        confirmed = [k for k, v in ver.items() if v["evidence"] == "confirmed"]
        names = f" ({', '.join(confirmed)})" if confirmed else ""
        w(f"Confirmed against the running game: {len(confirmed)}/{len(ver)} sections{names}. "
          f"The rest are decoded from the FRLG+ source and pass their own cross-checks.")
    w("")
    w("## Changes since last run")
    if not had_prev:
        w("- No previous snapshot — this run is the new baseline.")
    else:
        w(f"_Compared with snapshot from {changes.get('previous_extracted_at')} "
          f"(save #{changes.get('previous_save_counter')})._")
        for line in changes["lines"]:
            w(f"- {line}")
    w("")
    d = res["diagnostics"]
    w("## Needs your input")
    items = [f"⚠ ERROR — {e}" for e in d["errors"]] + d["needs_input"]
    w("\n".join(f"- {x}" for x in items) if items else "- Nothing right now.")
    w("")

    t = res.get("trainer")
    if t:
        w("## Trainer")
        w(f"{t['name']} ({t['gender']}) · TID {t['tid']:05d} · SID {t['sid']:05d} · "
          f"Rival {t.get('rival_name') or '?'} · Play time {t['play_time']} · "
          f"Money ₽{t['money']:,} · Coins {t['coins']:,}"
          + (f" · Registered item: {t['registered_item']}" if t.get("registered_item") else "")
          + (f" · Title: {t['master_trainer_title']}" if t.get("master_trainer_title") else ""))
        w("")
    ks = res.get("key_system")
    if ks:
        w("## Key System settings")
        w(" · ".join(f"{label} {_fmt(ks.get(field))}" for field, label in KEY_SYSTEM_FIELDS))
        w("")

    loc = res.get("location")
    if loc:
        w("## Status")
        where = loc.get("map_name") or f"map {loc['map']}"
        region = f" ({loc['region']})" if loc.get("region") and loc["region"] != loc.get("map_name") else ""
        w(f"Location: {where}{region} at ({loc['x']},{loc['y']}) · "
          f"last healed at {loc.get('last_heal_map_name') or loc['last_heal_map']}")
    dex = res.get("pokedex")
    if dex:
        w(f"Pokédex: owned {dex['owned_count']} · seen {dex['seen_count']} · "
          f"Extended Dex {dex.get('extended_owned_count')}/{dex.get('extended_total')}")
    prog = res.get("progress")
    if prog:
        w(f"Event flags set: {prog['flags_set_count']} (story {prog['story_flags_set_count']}, "
          f"trainer {prog['trainers_defeated_flags']}, system {prog.get('system_flags_set_count', 0)}) "
          f"· non-zero story variables: {len(prog['vars_nonzero'])}")
        w("")
        w(f"## Badges ({prog['badge_count']}/8)")
        w(" · ".join(("✓ " if b in prog["badges"] else "✗ ") + b for b in BADGE_ORDER))
    w("")

    party = res.get("party", [])
    if party:
        w("## Team")
        w("| # | Pokémon | Lv | Moves (PP) | Ability | Item | Nature | Origin |")
        w("|---|---|---|---|---|---|---|---|")
        for i, m in enumerate(party, 1):
            moves = ", ".join(f"{mv['name']} {mv['pp']}/{mv['max_pp']}" for mv in m["moves"])
            o = m["origin"]
            origin = (f"{o.get('class', '?')}; met {o['met_location']} Lv{o['met_level']}; "
                      f"{o['ball']}; OT {o['ot_name']}")
            w(f"| {i} | {_mon_line(m)} | {m['level']} | {moves} | {m['ability']} | "
              f"{m['held_item'] or '—'} | {m['nature']} ({m['nature_effect']}) | {origin} |")
        w("")
        w("Team details (IV/EV order HP/Atk/Def/SpA/SpD/Spe):")
        for m in party:
            s = m.get("stats", {})
            iv, ev = m["ivs"], m["evs"]
            w(f"- {m['nickname'] or m['species']}: HP {m.get('hp_current')}/{s.get('HP')}, "
              f"{m.get('status')} · "
              f"Atk/Def/SpA/SpD/Spe {'/'.join(str(s.get(k)) for k in ['Atk', 'Def', 'SpA', 'SpD', 'Spe'])} · "
              f"IVs {'/'.join(str(iv[k]) for k in iv)} · EVs {'/'.join(str(ev[k]) for k in ev)} "
              f"({m['ev_total']}) · HP {m['hidden_power']} · Gender {m['gender']} · "
              f"Friendship {m['friendship']} · EXP to next {m.get('exp_to_next', '—')}"
              + (f" · ⚠ {'; '.join(m['checks']['warnings'])}" if m["checks"]["warnings"] else ""))
        w("")

    bx = res.get("boxes")
    if bx:
        w(f"## PC boxes ({bx['total']} Pokémon, current box {bx['current_box']})")
        for b in bx["boxes"]:
            if not b["count"]:
                continue
            w(f"- Box {b['box']} “{b['name']}” ({b['count']}/30): "
              + ", ".join(f"{_mon_line(m)} {m['level']}" for m in b["pokemon"]))
        w("")

    dc = res.get("day_care")
    if dc and (dc["four_island"]["count"] or dc["route_5"]["count"] or dc.get("egg_waiting")):
        w("## Day Care")
        for label, grp in [("Four Island", "four_island"), ("Route 5", "route_5")]:
            for m in dc[grp]["pokemon"]:
                w(f"- {label}: {_mon_line(m)} Lv{m['level']} · {m.get('steps', 0):,} steps there")
        if dc.get("egg_waiting"):
            w("- An Egg is waiting to be collected at Four Island.")
        if dc["four_island"]["count"]:
            w(f"- Four Island breeding step counter: {dc.get('step_counter')}")
        w("")

    roamer = res.get("roamer", {})
    if roamer.get("active"):
        iv = roamer["ivs"]
        w("## Roaming legendary")
        w(f"- {roamer['species']} Lv{roamer['level']} · HP {roamer['hp']} · {roamer['nature']} "
          f"({roamer['nature_effect']}) · IVs {'/'.join(str(iv[k]) for k in iv)}"
          + (" · ★shiny" if roamer.get("shiny") else ""))
        w(f"- {roamer['note']}")
        w("")

    mt = res.get("master_trainers", {})
    if mt.get("cleared_count"):
        w(f"## Master Trainers ({mt['cleared_count']}/{mt['total']})")
        w("- Beaten: " + ", ".join(mt["cleared"]))
        w("")

    other = res.get("other_pokemon")
    if other:
        w("## Pokémon found elsewhere in the save")
        for m in other:
            w(f"- {_mon_line(m)} Lv{m['level']} — {m['where']} ({m['storage_guess']})")
        w("")

    it = res.get("items")
    if it:
        w("## Bag")
        for p in it["pockets"]:
            body = ", ".join(_item_text(x) for x in p["items"]) or "empty"
            w(f"- **{p['name']}** ({p['used']}/{p['capacity']}): {body}")
            if p.get("note"):
                w(f"  - note: {p['note']}")
        if it.get("problems"):
            w("- ⚠ " + "; ".join(it["problems"][:4]))
        w("")

    gs = res.get("game_stats")
    if gs:
        w("## Game stats")
        w(" · ".join(f"{k} {v:,}" for k, v in gs.items() if v))
        w("")
    hof = res.get("hall_of_fame", {})
    if hof.get("entries"):
        w(f"## Hall of Fame ({hof['count']} entries)")
        for i, team in enumerate(hof["entries"], 1):
            w(f"- #{i}: " + ", ".join(f"{p['nickname']} ({p['species']}) Lv{p['level']}"
                                      for p in team))
        w("")

    h = res.get("hints")
    if h:
        w("## Hints")
        w(f"- Outsider obedience cap with current badges: Lv{h['obedience_cap_for_outsiders']}")
        outs = h["outsiders"]
        if outs:
            over = [o for o in outs if o["over_cap"]]
            party_outs = [o for o in outs if o["where"].startswith("party")]
            if party_outs:
                w("- Party Pokémon the game treats as traded (1.5× EXP, obey only up to the cap): "
                  + ", ".join(f"{o['nickname'] or o['species']} Lv{o['level']} ({o['class']})"
                              for o in party_outs))
            if over:
                w("- ⚠ Over the obedience cap: "
                  + ", ".join(f"{o['species']} Lv{o['level']} ({o['where']})" for o in over))
        if h["evolutions_overdue"]:
            w("- Past evolution level but not evolved: " + ", ".join(
                f"{e['nickname'] or e['species']} Lv{e['level']} → {e['into']} @{e['evolves_at']} "
                f"({e['where']})" for e in h["evolutions_overdue"]))
        w("- Field moves known in party: " + (", ".join(h["party_field_moves"]) or "none"))
        if h["shinies"]:
            w("- Shiny: " + ", ".join(h["shinies"]))
        if h["pokerus"]:
            w("- Pokérus: " + ", ".join(h["pokerus"]))
        if h.get("boxed_hp_not_recorded"):
            b = h["boxed_hp_not_recorded"]
            w(f"- {b['count']} boxed Pokémon have no stored HP ({', '.join(b['where'])}). {b['risk']}")
        if h.get("multiple_starter_origins"):
            ms = h["multiple_starter_origins"]
            w(f"- Starter origin on more than one Pokémon ({', '.join(ms['pokemon'])}). {ms['note']}")
        if h["species_held_but_not_marked_owned"]:
            w(f"- ⚠ Species you hold but the Dex doesn't mark owned: "
              f"{h['species_held_but_not_marked_owned']}")
        w("")
        w(f"## Still to catch — Extended Dex ({h['missing_extended_dex_count']} left)")
        w(f"_{h['dex_version_note']} FRLG+'s Extended Dex is everything obtainable without trading._")
        catchable = [m for m in h["missing_extended_dex"] if m["where"]]
        other = [m for m in h["missing_extended_dex"] if not m["where"]]
        for m in sorted(catchable, key=lambda x: x["dex"])[:MISSING_SHOWN]:
            w(f"- #{m['dex']:03d} {m['species']}: {'; '.join(m['where'])}")
        if len(catchable) > MISSING_SHOWN:
            w(f"- …and {len(catchable) - MISSING_SHOWN} more findable in the wild "
              f"(full list in `extract_full.json` → `hints.missing_extended_dex`)")
        if other:
            w(f"- Not wild encounters ({len(other)} — evolve, trade, gift or static): "
              + ", ".join(f"{m['species']}" for m in sorted(other, key=lambda x: x["dex"])[:40])
              + (" …" if len(other) > 40 else ""))
        w("")

    w("## Diagnostics")
    w(f"- Save slots: active {sh.get('active_slot')} (#{sh.get('save_counter')}, "
      f"valid={sh.get('valid')}); other {sh.get('other_slot', {}).get('slot')} "
      f"(#{sh.get('other_slot', {}).get('counter')}, valid={sh.get('other_slot', {}).get('valid')})")
    if sh.get("pc_tail_bytes_used_beyond_vanilla"):
        w(f"- Last PC sector holds {sh['pc_tail_bytes_used_beyond_vanilla']} bytes beyond the "
          f"known PokemonStorage layout")
    for x in d["warnings"]:
        w(f"- ⚠ {x}")
    if d["known_gaps"]:
        w("- Known gaps: " + "; ".join(d["known_gaps"]))
    return "\n".join(out) + "\n"


def _item_text(x):
    name = x["name"]
    if x.get("move"):
        name = f"{name} {x['move']}"
    return name if x["qty"] == 1 else f"{name} ×{x['qty']}"
