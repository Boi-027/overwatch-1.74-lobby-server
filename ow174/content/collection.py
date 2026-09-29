"""What a player owns and has equipped, and the two messages that describe it to the client.

The retail capture holds one hero catalog (24900) and one account record (24300). They give the
list of heroes, what each owns by default, what the store sells, and which items are account-wide.
"""

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.jam.groups import HERO_CATALOG, PROGRESSION_IN
from ow174.jam.values import clone

# Loadout slots of a hero record in 24900
LOADOUT_SLOTS = ("+0x38", "+0x40", "+0x48", "+0x50", "+0x58", "+0x70", "+0x88")
SLOT_BY_TYPE = {
    "Skin": "+0x38",
    "HighlightIntro": "+0x40",
    "VictoryPose": "+0x48",
    "WeaponSkin": "+0x50",
}
LIST_SLOT_BY_TYPE = {"Spray": "+0x58", "VoiceLine": "+0x70", "Emote": "+0x88"}
# Icons, sprays and frames belong to the account. Many carry a hero tag in the catalog, but the
# capture lists the ones it owns only under the account, so they must reach the account list.
ACCOUNT_LEVEL_TYPES = frozenset({"Icon", "Spray", "PortraitFrame"})

# Portrait borders are 300 consecutive GUIDs: 5 tiers of 600 levels, each with 6 star counts
# (100 levels each) of 10 border steps (10 levels each).
BORDER_BASE_GUID = 0x0250000000000918
NO_UNLOCK_LEVEL = 65535  # account entries with this level are not level rewards


def _computed_border(level: int) -> int:
    """The border GUID for a level, used only when the catalog has no border for it."""
    levels_done = max(1, level) - 1
    tier = min(4, levels_done // 600)
    stars = min(5, (levels_done % 600) // 100)
    step = min(9, (levels_done % 100) // 10)
    return BORDER_BASE_GUID + tier * 60 + stars * 10 + step


def _guid_text(guid: int) -> str:
    """A GUID in the text form the profile stores."""
    return f"0x{guid:016X}"


def _parse_guids(value: str | list[str]) -> int | list[int]:
    if isinstance(value, list):
        return [int(guid, 0) for guid in value]
    return int(value, 0)


def _append_new(owned: list[int], extra: list[int]) -> list[int]:
    """Append the extra GUIDs that are not in the list yet, keeping their order."""
    seen = set(owned)
    for guid in extra:
        if guid not in seen:
            seen.add(guid)
            owned.append(guid)
    return owned


def _put_at(items: list[str], position: int, item: str) -> list[str]:
    """Put an item at a list position, filling any gap before it with copies of the last item."""
    filler = items[-1] if items else item
    while len(items) <= position:
        items.append(filler)
    items[position] = item
    return items


def _loot_box(box: dict) -> dict:
    return {"+0x0": {"+0x0": [box["id"], 0]}, "+0x10": box["type"], "+0x14": 1}


class Collection:
    def __init__(self, templates: RetailTemplates, items: ItemDB) -> None:
        self.items = items
        self._read_hero_catalog(templates.first(HERO_CATALOG, 24900))
        self._read_account_record(templates.first(PROGRESSION_IN, 24300))
        self._border_levels = self._portrait_borders()

    def _read_hero_catalog(self, catalog: dict) -> None:
        self.hero_template = catalog
        self.heroes: list[int] = []
        self.default_hero_owned: dict[int, list[int]] = {}
        self.default_loadouts: dict[int, dict] = {}
        for hero_record in catalog["+0x80"]:
            hero = hero_record["+0x30"]
            self.heroes.append(hero)
            self.default_hero_owned[hero] = [owned["+0x0"] for owned in hero_record["+0x0"]]
            self.default_loadouts[hero] = {slot: clone(hero_record[slot]) for slot in LOADOUT_SLOTS}

        self.hero_of: dict[int, int] = {}  # item GUID -> hero GUID, for hero-tagged items
        self.store_entries: dict[int, dict] = {}  # item GUID -> store entry (price at +0x14)
        for hero_store in catalog["+0x98"]:
            for entry in hero_store["+0x0"]:
                self.hero_of[entry["+0x0"]] = hero_store["+0x18"]
                self.store_entries[entry["+0x0"]] = entry

    def _read_account_record(self, progression: dict) -> None:
        self.progression_template = progression
        self.default_account_owned = [owned["+0x0"] for owned in progression["+0x78"]["+0x30"]]
        self.account_entries: dict[int, dict] = {}
        for entry in progression["+0xF8"]["+0x0"]:
            self.account_entries[entry["+0x0"]] = entry
        for guid, entry in self.account_entries.items():
            self.store_entries.setdefault(guid, entry)

    def _portrait_borders(self) -> list[tuple[int, int]]:
        """[(level, border GUID)] of the 179+ real portrait borders, lowest level first."""
        borders = []
        for guid, entry in self.account_entries.items():
            unlock_level = entry["+0x10"]
            if 0 <= unlock_level < NO_UNLOCK_LEVEL and self._item_type(guid) == "PortraitFrame":
                borders.append((unlock_level, guid))
        return sorted(borders)

    def _item_type(self, guid: int) -> str | None:
        unlock = self.items.get(guid)
        if unlock is None:
            return None
        return unlock.type

    # --- ownership -----------------------------------------------------------------------------

    # An item tagged with a hero, icons and sprays included, is listed only under that hero. Tested
    # in game: the client unlocks a hero's icon only from the hero's list, and when the account list
    # has it too, it does not send the equip request at all.

    def owned_for_hero(self, profile: Profile, hero: int) -> list[int]:
        owned = list(self.default_hero_owned.get(hero, []))
        if profile.unlock_all:
            extra = [guid for guid, owner in self.hero_of.items() if owner == hero]
        else:
            extra = [guid for guid in sorted(profile.unlocked_guids()) if self.hero_of.get(guid) == hero]
        return _append_new(owned, extra)

    def owned_account(self, profile: Profile) -> list[int]:
        owned = list(self.default_account_owned)
        if profile.unlock_all:
            extra = list(self.account_entries)
            for guid, unlock in self.items.unlocks.items():
                if unlock.type in ACCOUNT_LEVEL_TYPES and guid not in self.hero_of:
                    extra.append(guid)
        else:
            extra = [guid for guid in sorted(profile.unlocked_guids()) if guid not in self.hero_of]
        return _append_new(owned, extra)

    def owns(self, profile: Profile, guid: int) -> bool:
        if guid in self.hero_of and guid in self.owned_for_hero(profile, self.hero_of[guid]):
            return True
        return guid in self.owned_account(profile)

    # --- appearance ----------------------------------------------------------------------------

    def portrait_frame(self, profile: Profile) -> int:
        """The chosen frame, or the highest real border the level has reached.

        Levels above the top border keep it, because a made-up GUID shows no border at all.
        """
        if profile.frame_guid:
            return profile.frame_guid
        best = None
        for unlock_level, guid in self._border_levels:
            if unlock_level <= profile.level:
                best = guid
        if best:
            return best
        return _computed_border(profile.level)

    def loadout(self, profile: Profile, hero: int) -> dict:
        """The hero's equipped items as hero-record fields: the defaults with the profile's changes."""
        slots = clone(self.default_loadouts.get(hero, {}))
        changes = profile.loadouts.get(_guid_text(hero)) or {}
        for slot, value in changes.items():
            if slot in slots:
                slots[slot] = _parse_guids(value)
        return slots

    def equip(self, profile: Profile, hero: int, guid: int, slot: int) -> bool:
        """Store an equipped item in the profile. Returns False when the item has no slot there.

        A player icon is account-wide: it is sent with hero 0 from the account picker, or with the
        hero whose gallery lists it.
        """
        item_type = self._item_type(guid)
        if item_type == "Icon":
            profile.icon_guid = guid
            return True
        if hero == 0:
            return False
        loadout = profile.loadouts.setdefault(_guid_text(hero), {})
        if item_type in SLOT_BY_TYPE:
            loadout[SLOT_BY_TYPE[item_type]] = _guid_text(guid)
            return True
        if item_type in LIST_SLOT_BY_TYPE:
            list_slot = LIST_SLOT_BY_TYPE[item_type]
            items = loadout.get(list_slot) or self._equipped_list(profile, hero, list_slot)
            loadout[list_slot] = _put_at(items, max(0, slot), _guid_text(guid))
            return True
        return False

    def _equipped_list(self, profile: Profile, hero: int, list_slot: str) -> list[str]:
        return [_guid_text(equipped) for equipped in self.loadout(profile, hero)[list_slot]]

    # --- messages ------------------------------------------------------------------------------

    def progression(self, profile: Profile) -> dict:
        """24300: the account record with currencies, boxes, level, icon, frame and owned items."""
        value = clone(self.progression_template)
        record = value["+0x78"]
        record["+0x0"] = [_loot_box(box) for box in profile.loot_boxes]
        record["+0x18"] = []
        record["+0x30"] = [{"+0x0": guid, "+0x8": True} for guid in self.owned_account(profile)]
        record["+0x48"] = 0
        record["+0x50"] = 2000
        record["+0x58"] = self.portrait_frame(profile)
        record["+0x60"] = profile.icon_guid
        record["+0x68"] = profile.level
        record["+0x6C"] = profile.credits
        record["+0x70"] = profile.comp_points
        record["+0x74"] = profile.league_tokens
        record["+0x78"] = True
        record["+0x79"] = True
        return value

    def hero_catalog(self, profile: Profile) -> dict:
        """24900: every hero with the items it owns and its loadout."""
        value = clone(self.hero_template)
        for hero_record in value["+0x80"]:
            hero = hero_record["+0x30"]
            owned = self.owned_for_hero(profile, hero)
            hero_record["+0x0"] = [{"+0x0": guid, "+0x8": True} for guid in owned]
            hero_record.update(self.loadout(profile, hero))
        return value

    def unlock_granted(self, guid: int) -> tuple:
        """24901 {hero, unlock, new}: adds the unlock to the owned list and shows it as new."""
        return (HERO_CATALOG, 24901, {"+0x78": self.hero_of.get(guid, 0), "+0x80": guid, "+0x88": True})
