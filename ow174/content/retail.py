"""Static messages replayed from the retail capture at login (arcade, config, store, replays, ...)."""

from ow174.accounts.profile import Profile
from ow174.catalog.regions import localize
from ow174.catalog.templates import RetailTemplates, personalize
from ow174.content.clock import server_time
from ow174.content.identity import Identity
from ow174.content.menu_hero import MENU_HERO_KEY, pve_character
from ow174.jam.groups import (
    ARCADE,
    CONFIG,
    CUSTOM_GAMES,
    FRIENDS,
    IN_CONNECT,
    LOBBY,
    REPLAYS,
    STORE,
)

CLIENT_BUILD = 104319  # the 1.74 client
SUPPORTED_BUILDS_KEY = 0x04227E56  # config key in 36600: the client builds the config accepts
DAY = 86400

# Recorded messages sent at login, in capture order. Groups without a name are unidentified.
AT_LOGIN = [
    (LOBBY, 20814),
    (LOBBY, 20806),
    (LOBBY, 20812),
    (LOBBY, 20813),
    (LOBBY, 20809),
    (LOBBY, 20821),
    (0xAD1C34BA, 58501),
    (0xAA91BE18, 40900),
    (0xAA91BE18, 40914),
    (0x713D7589, 43200),
    (ARCADE, 39800),
    (ARCADE, 39801),
    (ARCADE, 39802),
    (ARCADE, 39803),
    (ARCADE, 39810),
    (ARCADE, 39806),
    (ARCADE, 39807),
    (ARCADE, 39804),
    (0x267FDE9E, 28001),
    (0x0DFEEFEF, 38300),
    (CONFIG, 36600),
    (CONFIG, 36603),
    (CONFIG, 36602),
    (REPLAYS, 51804),
    (REPLAYS, 51807),
    (REPLAYS, 51810),
    (REPLAYS, 51817),
    (REPLAYS, 51812),
    (0x34BB385D, 43301),
    (STORE, 26404),
    (STORE, 26400),
    (FRIENDS, 27104),
    (LOBBY, 20805),
    (LOBBY, 20820),
    (CUSTOM_GAMES, 23301),
]


class RetailReplay:
    def __init__(self, templates: RetailTemplates) -> None:
        self._templates = templates

    def at_login(self, profile: Profile, identity: Identity) -> list[tuple]:
        """The recorded messages, personalized and localized for this player."""
        now = int(server_time(profile))
        menu_npc = pve_character(profile)
        messages = []
        for group, msg_id in AT_LOGIN:
            for _, recorded in self._templates.all(group, msg_id):
                value = self._localized(recorded, profile, identity)
                messages.append((group, msg_id, self._refresh(group, msg_id, value, now, menu_npc)))
        return messages

    def menu_config(self, profile: Profile, identity: Identity) -> dict:
        """36600 with the menu hero override.

        The client re-reads it while connected, so a hero change in the dashboard shows up without
        restarting the game.
        """
        _, recorded = self._templates.all(CONFIG, 36600)[0]
        value = self._localized(recorded, profile, identity)
        return self._refresh(CONFIG, 36600, value, int(server_time(profile)), pve_character(profile))

    def preload(self, extra_skins: tuple) -> dict:
        """20505: content the client treats as available (heroes, skin themes, map headers).

        The capture's lists are from 1.68. Skins added later (like the ones the lobby maps put on
        their hero) must be appended, or the client does not spawn them.
        """
        value = self._templates.first(IN_CONNECT, 20505)
        recorded_skins = set(value["+0x90"])
        for skin in extra_skins:
            if skin not in recorded_skins:
                value["+0x90"].append(skin)
        return value

    @staticmethod
    def _localized(value, profile: Profile, identity: Identity):
        return localize(personalize(value, profile.player_name, identity.account_lo), profile.region)

    @staticmethod
    def _refresh(group: int, msg_id: int, value: dict, now: int, menu_npc: int | None) -> dict:
        """Update the parts of a recorded message that depend on time, build or the menu hero."""
        if (group, msg_id) == (ARCADE, 39810):
            for window in value["+0x78"]:
                window["+0x8"] = now - DAY
                window["+0x10"] = now + 30 * DAY
        elif (group, msg_id) == (CONFIG, 36602):
            value["+0x78"] = now
        elif (group, msg_id) == (CONFIG, 36600):
            _add_client_build(value["+0x78"])
            if menu_npc is not None:
                value["+0x78"].append({"+0x0": MENU_HERO_KEY, "+0x8": f"0x{menu_npc:016x}"})
        return value


def _add_client_build(config_entries: list[dict]) -> None:
    """Add our build to the supported builds entry, a text list like "[1,2,3]"."""
    for entry in config_entries:
        if entry["+0x0"] == SUPPORTED_BUILDS_KEY and str(CLIENT_BUILD) not in entry["+0x8"]:
            entry["+0x8"] = entry["+0x8"].rstrip("]") + f",{CLIENT_BUILD}]"
