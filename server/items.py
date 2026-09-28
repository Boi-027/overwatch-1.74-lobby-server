"""
Unlock metadata from DataTool's list-unlocks / list-general-unlocks JSON
(data/extracted_items.json, data/extracted_general_unlocks.json).

DataTool writes GUIDs as "<index>.<type>"; unlocks (type 0A5) are
0x0250000000000000 | index at runtime.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UNLOCK_BASE = 0x0250000000000000

RARITIES = ("Common", "Rare", "Epic", "Legendary")
CHALLENGE_TEXT = re.compile(r"Unlocked by (participating in|watching participating streams during) (?:the )?(.+?)$")


@dataclass
class Unlock:
    guid: int
    name: str
    type: str
    rarity: str
    hero: Optional[str]
    source: str
    box_type: Optional[str] = None
    categories: list = field(default_factory=list)
    available_in: str = ""


def unlock_guid(datatool_guid: str) -> int:
    index, _, kind = datatool_guid.partition(".")
    if kind.upper() != "0A5":
        raise ValueError(f"not an unlock GUID: {datatool_guid}")
    return UNLOCK_BASE | int(index, 16)


class ItemDB:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.unlocks = {}
        self.hero_names = {}
        heroes_path = data_dir / "extracted_heroes.json"
        if heroes_path.is_file():
            for g, h in json.loads(heroes_path.read_text(encoding="utf-8")).items():
                index, _, _ = g.partition(".")
                if h.get("Name"):
                    self.hero_names[0x02E0000000000000 | int(index, 16)] = h["Name"]
        items_path = data_dir / "extracted_items.json"
        if items_path.is_file():
            for hero, groups in json.loads(items_path.read_text(encoding="utf-8")).items():
                self._load_groups(groups, hero)
        general_path = data_dir / "extracted_general_unlocks.json"
        if general_path.is_file():
            self._load_groups(json.loads(general_path.read_text(encoding="utf-8")), None)

    def _load_groups(self, groups: dict, hero):
        for source, entries in groups.items():
            for entry in entries or []:
                if "Unlocks" in entry:
                    for u in entry["Unlocks"]:
                        self._add(u, hero, source, entry.get("LootBoxType"))
                else:
                    self._add(entry, hero, source, None)

    def _add(self, u: dict, hero, source, box_type):
        try:
            guid = unlock_guid(u["GUID"])
        except (KeyError, ValueError):
            return
        self.unlocks.setdefault(guid, Unlock(
            guid=guid, name=u.get("Name") or "", type=u.get("Type") or "", rarity=u.get("Rarity") or "Common",
            hero=hero, source=source, box_type=box_type, categories=u.get("Categories") or [],
            available_in=u.get("AvailableIn") or ""))

    def challenges(self) -> dict:
        """OW1 challenges and their rewards, from DataTool's 'Unlocked by participating in ...' texts."""
        out = {}
        for u in self.unlocks.values():
            m = CHALLENGE_TEXT.match(u.available_in)
            if m:
                title = m.group(2).strip()
                if m.group(1).startswith("watching"):
                    title += " (Twitch)"
                out.setdefault(title, []).append(u)
        order = {"Icon": 0, "Spray": 1, "VoiceLine": 2, "Emote": 3, "VictoryPose": 4, "HighlightIntro": 5, "Skin": 6}
        for rewards in out.values():
            rewards.sort(key=lambda u: (order.get(u.type, 9), RARITIES.index(u.rarity) if u.rarity in RARITIES else 0, u.guid))
        return out

    def get(self, guid: int) -> Optional[Unlock]:
        return self.unlocks.get(guid)

    def describe(self, guid: int) -> str:
        u = self.unlocks.get(guid)
        if not u:
            return f"0x{guid:016X}"
        owner = f"{u.hero} " if u.hero else ""
        return f"{owner}{u.type} '{u.name}' ({u.rarity})"

    def hero_name(self, hero_guid: int) -> str:
        return self.hero_names.get(hero_guid, f"0x{hero_guid:016X}")

    def hero_by_name(self, name: str) -> Optional[int]:
        want = name.strip().lower()
        for guid, n in self.hero_names.items():
            if n.lower() == want:
                return guid
        return None
