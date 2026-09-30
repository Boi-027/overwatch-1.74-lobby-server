"""The hero that stands in the main menu.

A profile can ask for a hero by name, "random", "none", or a PvE character. A picked hero goes to
the client through a retail config key that can put any loaded hero record in the menu
(MENU_HERO_KEY). The client applies it live, so a dashboard change shows at once. PvE characters
have no lobby idle animation and stand in a bind pose.
"""

import random

from ow174.accounts.profile import Profile
from ow174.catalog.items import HERO_BASE, ItemDB
from ow174.content.collection import Collection

MENU_HERO_KEY = 0x87053C32  # config key in message 36600 that sets the menu hero
PVE_NPC_BASE = HERO_BASE
PVE_NPCS = {
    "Null Sector Eradicator": 0x173,
    "Null Sector Slicer": 0x178,
    "Null Sector Nulltrooper": 0x179,
    "Null Sector Detonator": 0x17C,
    "Talon Trooper": 0x1AC,
    "Talon Sniper": 0x1B8,
    "Talon Heavy Assault": 0x1BA,
    "Talon Assassin": 0x1BB,
    "Talon Enforcer": 0x1CE,
    "Junkenstein Zomnic": 0x175,
    "Junkenstein Shock-Tire": 0x176,
    "Junkenstein Zombardier": 0x177,
    "B.O.B.": 0x21D,
}


def pve_character(profile: Profile) -> int | None:
    """GUID of the PvE character chosen as the menu hero, or None."""
    wanted = (profile.lobby_hero or "").strip().casefold()
    for name, index in PVE_NPCS.items():
        if name.casefold() == wanted:
            return PVE_NPC_BASE | index
    return None


class MenuHero:
    def __init__(self, collection: Collection, items: ItemDB) -> None:
        self._collection = collection
        self._items = items
        self._random_picks: dict[str, int] = {}  # player name -> hero, kept until the next login
        self._last_choice: dict[str, str] = {}  # player name -> lobby_hero seen last time

    def choose(self, profile: Profile) -> int:
        """The hero GUID for the party card and career stats, or 0 for "none".

        A PvE character is not a catalog hero, so it gets the random pick here.
        """
        choice = (profile.lobby_hero or "random").strip()
        if self._last_choice.get(profile.player_name) != choice:
            self._last_choice[profile.player_name] = choice
            self.reroll(profile)  # switching to "random" picks a new hero
        if choice.lower() == "none":
            return 0
        if choice.lower() != "random":
            hero = self._hero_by_name_or_guid(choice)
            if hero in self._collection.default_loadouts:
                return hero
        return self._random_picks.setdefault(profile.player_name, random.choice(self._collection.heroes))

    def picked(self, profile: Profile) -> int:
        """The hero picked for the menu in the dashboard, or 0 for random, none or a PvE character."""
        choice = (profile.lobby_hero or "").strip()
        if choice.lower() in ("", "random", "none"):
            return 0
        hero = self._hero_by_name_or_guid(choice)
        return hero if hero in self._collection.default_loadouts else 0

    def menu_guid(self, profile: Profile) -> int | None:
        """The hero record to put in the menu through MENU_HERO_KEY, or None to leave it alone.

        Only a hero the player picked goes through the key. With "random" or "none" the menu keeps
        what the event scene brings, such as the OWL lobby's Genji.
        """
        npc = pve_character(profile)
        if npc:
            return npc
        if (profile.lobby_hero or "random").strip().lower() in ("random", "none"):
            return None
        return self.choose(profile) or None

    def reroll(self, profile: Profile) -> None:
        """Forget the random pick, so the next choose() picks again."""
        self._random_picks.pop(profile.player_name, None)

    def _hero_by_name_or_guid(self, choice: str) -> int | None:
        hero = self._items.hero_by_name(choice)
        if hero is None and choice.lower().startswith("0x"):
            hero = int(choice, 16)
        return hero
