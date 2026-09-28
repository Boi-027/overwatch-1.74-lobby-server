"""
Loot box opening against the real 1.74 catalog.

Pools come from the hero catalog (24900) and the account catalog in 24300:
each store entry carries its event (+0x16: 0 base, 1-6 seasonal events, -1 not
in boxes) and price; rarity and type come from DataTool's unlock list.
"""

import random
import struct
from pathlib import Path

try:
    from storage import Profile, save_profile, create_default_boxes
except ImportError:
    from .storage import Profile, save_profile, create_default_boxes

DUPLICATE_CREDITS = {"Common": 5, "Rare": 15, "Epic": 50, "Legendary": 200}
BOX_EVENT = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 9: 6}
GUARANTEED_LEGENDARY = {7, 9, 12}
LOOTABLE_TYPES = {"Skin", "Emote", "VictoryPose", "VoiceLine", "Spray", "HighlightIntro", "Icon"}
SLOT_ODDS = (("Legendary", 0.03), ("Epic", 0.07), ("Rare", 0.30))
GUARANTEED_ODDS = (("Legendary", 0.06), ("Epic", 0.14))


class LootBoxEngine:
    def __init__(self, content, items):
        self.content = content
        self.items = items
        self.pools = {}
        for guid, entry in content.store_entries.items():
            event = entry["+0x16"]
            unlock = items.get(guid)
            if event < 0 or entry["+0x14"] <= 0 or unlock is None or unlock.type not in LOOTABLE_TYPES:
                continue
            self.pools.setdefault((event, unlock.rarity), []).append(guid)
        sizes = {f"{e}/{r}": len(v) for (e, r), v in sorted(self.pools.items())}
        print(f"[lootbox] Pools by event/rarity: {sizes}")

    def _roll(self, guaranteed=False, legendary=False) -> str:
        if legendary:
            return "Legendary"
        roll = random.random()
        for rarity, p in (GUARANTEED_ODDS if guaranteed else SLOT_ODDS):
            if roll < p:
                return rarity
            roll -= p
        return "Rare" if guaranteed else "Common"

    def _pick(self, event: int, rarity: str, is_owned=None) -> int:
        """Pick an item of the rarity, preferring the event pool then base, and — like the real game —
        preferring items the player does NOT own yet. A duplicate only drops when every candidate of
        that rarity is already owned."""
        pools = [self.pools[(e, rarity)] for e in (event, 0) if self.pools.get((e, rarity))]
        if not pools:
            pools = [self.pools[(0, "Common")]]
        if is_owned is not None:
            for pool in pools:
                unowned = [g for g in pool if not is_owned(g)]
                if unowned:
                    return random.choice(unowned)
        return random.choice(pools[0])

    def open_loot_box(self, box_id: int, profile: Profile, profile_path: Path):
        """Returns (drops, box_type, duplicate_credits, box_name); drops are dicts for content.box_result."""
        box = next((b for b in profile.loot_boxes if b["id"] == box_id), None)
        box_type, box_name = (box["type"], box["name"]) if box else (0, "Standard")
        if box:
            profile.loot_boxes.remove(box)

        event = BOX_EVENT.get(box_type, 0)
        rarities = [self._roll(guaranteed=True, legendary=box_type in GUARANTEED_LEGENDARY)] + [self._roll() for _ in range(3)]
        random.shuffle(rarities)
        events = [event if (event and (i == 0 or random.random() < 0.5)) else 0 for i in range(4)]

        owned = profile.get_unlocked_set()

        def is_owned(guid: int) -> bool:
            return guid in owned or self.content.owns(profile, guid)

        drops, credits = [], 0
        for order, (rarity, ev) in enumerate(zip(rarities, events)):
            guid = self._pick(ev, rarity, is_owned)
            duplicate = is_owned(guid)
            dup_credits = DUPLICATE_CREDITS[rarity] if duplicate else 0
            credits += dup_credits
            if not duplicate:
                owned.add(guid)
                profile.unlocked_items.append(f"0x{guid:016X}")
            drops.append({
                "hero": self.content.hero_of.get(guid, 0), "unlock": guid, "credits": dup_credits,
                "order": order, "highlight": box_type if ev else 0, "duplicate": duplicate, "new": not duplicate,
                "rarity": rarity,
            })

        profile.credits += credits
        profile.stats["boxes_opened"] = profile.stats.get("boxes_opened", 0) + 1
        profile.stats["legendaries_dropped"] = profile.stats.get("legendaries_dropped", 0) + rarities.count("Legendary")
        profile.stats["credits_earned_from_duplicates"] = profile.stats.get("credits_earned_from_duplicates", 0) + credits

        if len(profile.loot_boxes) < 5:
            more, profile.next_box_id = create_default_boxes(profile.next_box_id)
            profile.loot_boxes.extend(more)
            print(f"[lootbox] Inventory low. Auto-refilled +{len(more)} boxes!")

        save_profile(profile, profile_path)
        return drops, box_type, credits, box_name


def box_id_from(value: dict) -> int:
    return value["+0x78"]["+0x0"][0]


def box_id_bytes(box_id: int) -> bytes:
    return struct.pack("<QQ", box_id, 0)
