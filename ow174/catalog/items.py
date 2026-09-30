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
SKIN_THEME_BASE = 0x0A50000000000000  # skin themes (0A6)
HERO_BASE = 0x02E0000000000000

# The text of Overwatch League team skins. Each one has a partner of the same hero and team (home
# and away, or the white and gray league skins) that unlocks with it.
TEAM_SKIN_TEXT = "Unlocking includes both home and away skins"
SKIN_YEAR = re.compile(r" (\d{4})$")  # the 2018 team skins pair up apart from the current ones


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
    skin_theme: int = 0  # for a skin: the skin theme GUID the client loads to show it
    esports_team: str = ""  # for an Overwatch League skin: its team


def unlock_guid(datatool_guid: str) -> int:
    """The runtime GUID of a DataTool unlock GUID such as "4AEA.0A5"."""
    index, _, kind = datatool_guid.partition(".")
    if kind.upper() != "0A5":
        raise ValueError(f"not an unlock GUID: {datatool_guid}")
    return UNLOCK_BASE | int(index, 16)


def _skin_theme_guid(datatool_guid: str | None) -> int:
    """The runtime GUID of a DataTool skin theme GUID such as "4924.0A6", or 0."""
    if not datatool_guid:
        return 0
    return SKIN_THEME_BASE | int(datatool_guid.partition(".")[0], 16)


def _read_json(path: Path) -> dict:
    """The file's JSON, or an empty dict when the file is missing."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")) or {}


class ItemDB:
    """All cosmetic unlocks and heroes of the client, by GUID."""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.unlocks: dict[int, Unlock] = {}
        self.hero_names: dict[int, str] = {}
        self.hero_classes: dict[int, str] = {}  # "Tank", "Damage" or "Support"
        for guid, hero in _read_json(data_dir / "extracted_heroes.json").items():
            if hero.get("Name"):
                index = int(guid.partition(".")[0], 16)
                self.hero_names[HERO_BASE | index] = hero["Name"]
                self.hero_classes[HERO_BASE | index] = hero.get("Class", "")
        for hero, groups in _read_json(data_dir / "extracted_items.json").items():
            self._load_groups(groups, hero)
        self._load_groups(_read_json(data_dir / "extracted_general_unlocks.json"), None)
        self.team_skin_pairs = self._pair_team_skins()

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
                skin_theme=_skin_theme_guid(unlock.get("SkinThemeGUID")),
                esports_team=unlock.get("EsportsTeam") or "",
            ),
        )

    def _pair_team_skins(self) -> dict[int, int]:
        """Each team skin and the skin that unlocks with it, both ways."""
        teams: dict[tuple, list[int]] = {}
        for unlock in self.unlocks.values():
            if unlock.available_in == TEAM_SKIN_TEXT:
                year = SKIN_YEAR.search(unlock.name)
                key = (unlock.hero, unlock.esports_team, year.group(1) if year else "")
                teams.setdefault(key, []).append(unlock.guid)
        pairs = {}
        for guids in teams.values():
            if len(guids) == 2:
                first, second = guids
                pairs[first], pairs[second] = second, first
        return pairs

    def team_skin_pair(self, guid: int) -> int | None:
        """The home or away skin that comes with an Overwatch League team skin."""
        return self.team_skin_pairs.get(guid)

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
