"""Collect every write a conversion would make, with what it replaces.

A Plan is data: the caller can print it, diff it, or apply it. Nothing here
opens or writes a file.
"""
from __future__ import annotations

import rules

# The four ways a bag entry can fail to carry over, each with its own heading.
# They are different problems: only pocket_full is about capacity. unknown_item
# means FRLG+ has no such item, or no pocket to put it in.
LOSS_HEADINGS = {
    "tm_quantity": "TMs and HMs kept once (the TM Case stores one bit per TM)",
    "pocket_full": "Did not fit (the FRLG+ pocket is full)",
    "key_item_quantity": "Key items kept once (Key Items stores no quantity)",
    "unmapped_key_item": "Key items with no FRLG+ key-item index",
    "unknown_item": "Not carried: FRLG+ has no such item, or no pocket for it",
}


def build_plan(src, vanilla_L, plus_L, tables, *, source_version):
    bag_writes, losses = rules.convert_bag(src["pockets"], plus_L, tables)
    # convert_bag works in plaintext; the game stores each quantity XOR'd with the key.
    writes = rules.encrypt_quantities(bag_writes, plus_L, src["key"] & 0xFFFF)
    writes.update(rules.convert_daycare(src["sb1"], vanilla_L, plus_L))
    id_writes, notes = rules.remap_ids(src["sb1"], vanilla_L, plus_L, src["remaps"])
    writes.update(id_writes)
    writes["sb1_key_flags"] = rules.build_key_flags(source_version).to_bytes(2, "little")
    writes.update(rules.zeroed_regions(plus_L))
    mons, skipped, decisions = rules.convert_mons(
        src, plus_L, tables, source_version=source_version)
    return {"writes": writes, "mons": mons, "losses": losses, "skipped": skipped,
            "decisions": decisions, "notes": notes,
            "source_version": source_version}


def render_plan(plan, src, plus_L):
    L = []
    slot = src["slot"]
    L.append(f"Source: slot {slot['slot']}, save #{slot['counter']}, "
             f"{'checksums valid' if slot['valid'] else 'CHECKSUMS INVALID'}")
    L.append(f"Trainer: {src['player']['name']} — "
             f"{src['player']['play_time'][0]}h played, "
             f"{src['money']} money, {src['coins']} coins")
    L.append(f"Source game: "
             f"{'FireRed' if plan['source_version'] == 'fr' else 'LeafGreen'}")
    L.append("")
    party = len([m for m in src["party"] if m])
    boxed = sum(1 for row in src["boxes"] for m in row if m)
    L.append(f"Pokémon: {party} in the party, {boxed} in the boxes — every one gets "
             f"boxHP written, so none withdraws fainted under Nuzlocke or No Free Heals")
    items = sum(len(p) for p in src["pockets"].values())
    L.append(f"Bag: {items} items re-pocketed into FRLG+'s pockets")
    L.append("")
    L.append("Field writes:")
    for key in sorted(plan["writes"]):
        n = len(plan["writes"][key])
        L.append(f"  {key:34s} {n:5d} byte{'s' if n != 1 else ''}")

    if plan["losses"]:
        L.append("")
        L.append(f"Losses ({len(plan['losses'])}):")
        by_kind = {}
        for loss in plan["losses"]:
            by_kind.setdefault(loss["kind"], []).append(loss)
        order = list(LOSS_HEADINGS) + sorted(set(by_kind) - set(LOSS_HEADINGS))
        for kind in order:
            if kind not in by_kind:
                continue
            L.append(f"  {LOSS_HEADINGS.get(kind, kind)}:")
            for loss in by_kind[kind]:
                L.append(f"    - {loss['item']}: {loss['detail']}")

    for title, items_ in (("Skipped", plan["skipped"]),
                          ("Decisions", plan["decisions"]), ("Notes", plan["notes"])):
        if not items_:
            continue
        L.append("")
        L.append(f"{title}:")
        for it in items_:
            L.append(f"  - {it}")
    return "\n".join(L)
