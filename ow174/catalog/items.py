"""Unlock metadata (names, types, rarity) from DataTool's list-unlocks JSON files.

DataTool writes GUIDs as "<index>.<type>". Unlocks (type 0A5) are 0x0250000000000000 | index at
runtime and heroes (type 075) are 0x02E0000000000000 | index.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ow174.paths import DATA_DIR

UNLOCK_BASE = 0x0250000000000000
HERO_BASE = 0x02E0000000000000

RARITIES = ("Common", "Rare", "Epic", "Legendary")
CHALLENGE_TEXT = re.compile(
    r"Unlocked by (participating in|watching participating streams during) (?:the )?(.+?)$"
)
# The order in which a challenge lists its rewards, by unlock type.
CHALLENGE_REWARD_ORDER = {
    "Icon": 0,
    "Spray": 1,
    "VoiceLine": 2,
    "Emote": 3,
    "VictoryPose": 4,
    "HighlightIntro": 5,
    "Skin": 6,
}


@dataclass
class Unlock:
    guid: int
    name: str
    type: str
    rarity: str
    hero: str | None
    source: str
    box_type: str | None = None
    categories: list = field(default_factory=list)
    available_in: str = ""


def unlock_guid(datatool_guid: str) -> int:
    """The runtime GUID of a DataTool unlock GUID such as "4AEA.0A5"."""
    index, _, kind = datatool_guid.partition(".")
    if kind.upper() != "0A5":
        raise ValueError(f"not an unlock GUID: {datatool_guid}")
    return UNLOCK_BASE | int(index, 16)


def _read_json(path: Path) -> dict:
    """The file's JSON, or an empty dict when the file is missing."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")) or {}


def _reward_order(unlock: Unlock) -> tuple:
    rarity = RARITIES.index(unlock.rarity) if unlock.rarity in RARITIES else 0
    return CHALLENGE_REWARD_ORDER.get(unlock.type, 9), rarity, unlock.guid


class ItemDB:
    """All cosmetic unlocks and heroes of the client, by GUID."""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.unlocks: dict[int, Unlock] = {}
        self.hero_names: dict[int, str] = {}
        for guid, hero in _read_json(data_dir / "extracted_heroes.json").items():
            if hero.get("Name"):
                index = int(guid.partition(".")[0], 16)
                self.hero_names[HERO_BASE | index] = hero["Name"]
        for hero, groups in _read_json(data_dir / "extracted_items.json").items():
            self._load_groups(groups, hero)
        self._load_groups(_read_json(data_dir / "extracted_general_unlocks.json"), None)

    def _load_groups(self, groups: dict, hero: str | None) -> None:
        """Add the unlocks of one DataTool file. An entry is an unlock or a loot box group of them."""
        for source, entries in groups.items():
            for entry in entries or []:
                if "Unlocks" in entry:
                    for unlock in entry["Unlocks"]:
                        self._add(unlock, hero, source, entry.get("LootBoxType"))
                else:
                    self._add(entry, hero, source, None)

    def _add(self, unlock: dict, hero: str | None, source: str, box_type: str | None) -> None:
        try:
            guid = unlock_guid(unlock["GUID"])
        except (KeyError, ValueError):
            return
        self.unlocks.setdefault(
            guid,
            Unlock(
                guid=guid,
                name=unlock.get("Name") or "",
                type=unlock.get("Type") or "",
                rarity=unlock.get("Rarity") or "Common",
                hero=hero,
                source=source,
                box_type=box_type,
                categories=unlock.get("Categories") or [],
                available_in=unlock.get("AvailableIn") or "",
            ),
        )

    def challenges(self) -> dict[str, list[Unlock]]:
        """Hero challenges and their rewards, from the "Unlocked by participating in ..." texts."""
        challenges: dict[str, list[Unlock]] = {}
        for unlock in self.unlocks.values():
            match = CHALLENGE_TEXT.match(unlock.available_in)
            if not match:
                continue
            title = match.group(2).strip()
            if match.group(1).startswith("watching"):
                title += " (Twitch)"
            challenges.setdefault(title, []).append(unlock)
        for rewards in challenges.values():
            rewards.sort(key=_reward_order)
        return challenges

    def get(self, guid: int) -> Unlock | None:
        return self.unlocks.get(guid)

    def describe(self, guid: int) -> str:
        unlock = self.unlocks.get(guid)
        if not unlock:
            return f"0x{guid:016X}"
        owner = f"{unlock.hero} " if unlock.hero else ""
        return f"{owner}{unlock.type} '{unlock.name}' ({unlock.rarity})"

    def hero_name(self, hero_guid: int) -> str:
        return self.hero_names.get(hero_guid, f"0x{hero_guid:016X}")

    def hero_by_name(self, name: str) -> int | None:
        wanted = name.strip().lower()
        for guid, hero_name in self.hero_names.items():
            if hero_name.lower() == wanted:
                return guid
        return None
