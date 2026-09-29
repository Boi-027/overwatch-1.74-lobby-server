"""Opening loot boxes against the real 1.74 catalog.

Every store entry in the hero and account catalogs has a price and an event: 0 for the base pool,
1-6 for a seasonal event, -1 for items that never drop from boxes (challenge rewards among them).
Icons have no price, as the game never sold them, but they drop. Rarity and item type come from the
extracted unlock list in ItemDB.
"""

import logging
import random
from dataclasses import dataclass

from ow174.accounts.profile import Profile
from ow174.catalog.boxes import BOX_TYPES, starter_boxes
from ow174.catalog.items import ItemDB
from ow174.content.collection import Collection

log = logging.getLogger(__name__)

DUPLICATE_CREDITS = {"Common": 5, "Rare": 15, "Epic": 50, "Legendary": 200}
LOOTABLE_TYPES = {"Skin", "Emote", "VictoryPose", "VoiceLine", "Spray", "HighlightIntro", "Icon"}
# Chance of each rarity for an ordinary slot, and for the one slot that is at least rare. They give
# the odds of the game's own "average chance from containers" screen for a whole box: some common
# item 99%, rare 94% (here 95%), epic 18.5%, legendary 7.5%.
SLOT_ODDS = (("Legendary", 0.0193), ("Epic", 0.0498), ("Rare", 0.10))
GUARANTEED_ODDS = (("Legendary", 0.0193), ("Epic", 0.0498))
SLOTS_PER_BOX = 4
LOW_STOCK = 5  # a profile with fewer boxes left is refilled with the starter set
# Credits drop in place of an item of the same rarity (STUUnlock_Currency 0A5/088B-088D; 500 is the
# chest full of coins), so a box keeps the rarity odds of the game's odds screen. Retail's share of
# credits in a rarity is not known: here half of the legendary slots hold the 500 (as often as a
# legendary item) and 1 in 20 rare or epic slots holds 50 or 150.
CREDIT_DROPS = {
    "Rare": (0x025000000000088B, 50),
    "Epic": (0x025000000000088C, 150),
    "Legendary": (0x025000000000088D, 500),
}
CREDIT_SHARE = {"Rare": 0.05, "Epic": 0.05, "Legendary": 0.5}


@dataclass
class BoxOpening:
    """The result of opening one box."""

    drops: list[dict]  # one dict per item, see box_result_message
    box_name: str


def box_id_from(value: dict) -> int:
    """The box id in a client "open box" request."""
    return value["+0x78"]["+0x0"][0]


def box_result_message(box_id: tuple, drops: list[dict]) -> dict:
    """Message 24305: what the client shows after a box opens."""
    items = []
    for drop in drops:
        items.append(
            {
                "+0x0": drop["hero"],
                "+0x8": drop["unlock"],
                "+0x10": drop["credits"],
                "+0x14": drop["order"],
                "+0x18": drop["highlight"],
                "+0x1C": drop["duplicate"],
                "+0x1D": drop["new"],
            }
        )
    return {"+0x78": {"+0x0": items, "+0x18": {"+0x0": list(box_id)}, "+0x28": 0}}


def _roll_rarity(guaranteed: bool = False, legendary: bool = False) -> str:
    if legendary:
        return "Legendary"
    odds = GUARANTEED_ODDS if guaranteed else SLOT_ODDS
    roll = random.random()
    for rarity, chance in odds:
        if roll < chance:
            return rarity
        roll -= chance
    return "Rare" if guaranteed else "Common"


def _roll_slot_rarities(always_legendary: bool) -> list[str]:
    """One guaranteed slot and three ordinary ones, in random order."""
    rarities = [_roll_rarity(guaranteed=True, legendary=always_legendary)]
    for _ in range(SLOTS_PER_BOX - 1):
        rarities.append(_roll_rarity())
    random.shuffle(rarities)
    return rarities


def _roll_slot_events(event: int) -> list[int]:
    """The pool event of each slot. In an event box the first slot is always from the event."""
    events = []
    for slot in range(SLOTS_PER_BOX):
        if event and (slot == 0 or random.random() < 0.5):
            events.append(event)
        else:
            events.append(0)
    return events


def _roll_credits(rarity: str, slot_event: int, promised: bool) -> tuple | None:
    """The (currency unlock, amount) a slot gives instead of an item of its rarity, if any. Never in
    an event slot, nor in place of the legendary item a legendary box promises."""
    if slot_event or promised or rarity not in CREDIT_SHARE:
        return None
    if random.random() < CREDIT_SHARE[rarity]:
        return CREDIT_DROPS[rarity]
    return None


def _credit_drop(guid: int, amount: int, order: int, rarity: str) -> dict:
    """A slot that gave credits. The client takes the amount from the currency unlock."""
    return {
        "hero": 0,
        "unlock": guid,
        "credits": 0,
        "order": order,
        "highlight": 0,
        "duplicate": False,
        "new": False,
        "rarity": rarity,
        "amount": amount,
    }


def _count_stats(profile: Profile, rarities: list[str], credits: int) -> None:
    stats = profile.stats
    stats["boxes_opened"] = stats.get("boxes_opened", 0) + 1
    stats["legendaries_dropped"] = stats.get("legendaries_dropped", 0) + rarities.count("Legendary")
    stats["credits_earned_from_duplicates"] = stats.get("credits_earned_from_duplicates", 0) + credits


def _take_box(profile: Profile, box_id: int) -> tuple[int, str]:
    """Remove the box from the profile. Returns its type and name. An unknown id opens a standard box."""
    for box in profile.loot_boxes:
        if box["id"] == box_id:
            profile.loot_boxes.remove(box)
            return box["type"], box["name"]
    return 0, "Standard"


def _refill_if_low(profile: Profile) -> None:
    if len(profile.loot_boxes) >= LOW_STOCK:
        return
    more, profile.next_box_id = starter_boxes(profile.next_box_id)
    profile.loot_boxes.extend(more)
    log.info("Box stock was low, added %d boxes", len(more))


class LootBoxEngine:
    def __init__(self, collection: Collection, items: ItemDB) -> None:
        self._collection = collection
        self._pools: dict[tuple[int, str], list[int]] = {}  # (event, rarity) -> item GUIDs
        self._hero_pools: dict[tuple[str, str], list[int]] = {}  # (hero, rarity) -> item GUIDs
        for guid, entry in collection.store_entries.items():
            event = entry["+0x16"]
            price = entry["+0x14"]
            unlock = items.get(guid)
            if event < 0 or unlock is None or unlock.type not in LOOTABLE_TYPES:
                continue
            if price <= 0 and unlock.type != "Icon":
                continue
            self._pools.setdefault((event, unlock.rarity), []).append(guid)
            if event == 0 and unlock.hero:
                self._hero_pools.setdefault((unlock.hero, unlock.rarity), []).append(guid)
        sizes = {}
        for (event, rarity), pool in sorted(self._pools.items()):
            sizes[f"{event}/{rarity}"] = len(pool)
        log.info("Loot box pools by event/rarity: %s", sizes)

    def _pick(self, event: int, rarity: str, is_owned) -> int:
        """An item of the rarity from the event pool, then the base pool.

        Like the real game, it prefers items the player does not own yet. A duplicate only drops when
        every candidate of that rarity is already owned.
        """
        pools = []
        for pool_event in (event, 0):
            pool = self._pools.get((pool_event, rarity))
            if pool:
                pools.append(pool)
        if not pools:
            pools = [self._pools[(0, "Common")]]
        for pool in pools:
            unowned = [guid for guid in pool if not is_owned(guid)]
            if unowned:
                return random.choice(unowned)
        return random.choice(pools[0])

    def _pick_hero_item(self, hero: str, rarity: str, is_owned) -> int:
        """An item for a hero's box (the Wrecking Ball box), which only holds that hero's items from
        the base pool. It prefers items the player does not own yet, like _pick."""
        pool = self._hero_pools[(hero, rarity)]
        unowned = [guid for guid in pool if not is_owned(guid)]
        return random.choice(unowned or pool)

    def open(self, box_id: int, profile: Profile, refill: bool = True) -> BoxOpening:
        """Open a box: remove it from the profile, add the drops and credits, update the stats.
        Without `refill` a low stock stays low.

        The caller saves the profile.
        """
        box_type, box_name = _take_box(profile, box_id)
        kind = BOX_TYPES.get(box_type)
        event = kind.event if kind else 0
        hero = kind.hero if kind else ""
        rarities = _roll_slot_rarities(always_legendary=bool(kind and kind.legendary))
        events = _roll_slot_events(event)
        promised = rarities.index("Legendary") if kind and kind.legendary else None

        # Worked out once per box: asking the collection for each candidate made a box take seconds.
        owned = self._collection.owned_set(profile)

        def is_owned(guid: int) -> bool:
            return profile.unlock_all or guid in owned

        drops = []
        credits = 0
        for order, (rarity, slot_event) in enumerate(zip(rarities, events, strict=True)):
            credit = _roll_credits(rarity, slot_event, order == promised)
            if credit:
                guid, amount = credit
                profile.credits += amount
                drops.append(_credit_drop(guid, amount, order, rarity))
                continue
            if hero:
                guid = self._pick_hero_item(hero, rarity, is_owned)
            else:
                guid = self._pick(slot_event, rarity, is_owned)
            duplicate = is_owned(guid)
            duplicate_credits = DUPLICATE_CREDITS[rarity] if duplicate else 0
            credits += duplicate_credits
            if not duplicate:
                owned.add(guid)
                profile.unlocked_items.append(f"0x{guid:016X}")
            drops.append(
                {
                    "hero": self._collection.hero_of.get(guid, 0),
                    "unlock": guid,
                    "credits": duplicate_credits,
                    "order": order,
                    "highlight": box_type if slot_event else 0,
                    "duplicate": duplicate,
                    "new": not duplicate,
                    "rarity": rarity,
                }
            )

        profile.credits += credits
        _count_stats(profile, rarities, credits)
        if refill:
            _refill_if_low(profile)
        return BoxOpening(drops, box_name)

    def open_all(self, profile: Profile) -> tuple[int, list[int]]:
        """Open every box, as the move to Overwatch 2 did. Returns how many were opened and the
        items that were new."""
        granted = []
        box_ids = [box["id"] for box in profile.loot_boxes]
        for box_id in box_ids:
            for drop in self.open(box_id, profile, refill=False).drops:
                if drop["new"]:
                    granted.append(drop["unlock"])
        return len(box_ids), granted
