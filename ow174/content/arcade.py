"""The Arcade: its cards (39802), their dates (39810) and how busy each one is (39804).

The retail capture is from Lunar New Year 2022, so its Arcade holds the Lunar New Year brawls. Here
the cards follow the active events: the events' card groups first, then the everyday cards, up to the
seven slots the Arcade screen has. The client files each card under its menu group (the 0EE menu
nodes of catalog 10B.063), and each group takes one slot. With Lunar New Year this gives exactly the
retail Arcade.

A card is the wire form of its 0C7 asset (data/arcade_cards_174.json, made by
tools/extract_arcade_cards.py). The server adds how long it stays and whether it is permanent.
"""

import copy
import json

from ow174.accounts.profile import Profile
from ow174.catalog.templates import RetailTemplates
from ow174.content.clock import DAY
from ow174.content.ranked import CARD_BASE, Ranked
from ow174.jam.groups import ARCADE
from ow174.paths import DATA_DIR

SLOTS = 7
DEFAULT_BUSY = 0.1

# Card groups (0C7 indexes) from the Arcade menu nodes in the client's data.
LUNAR_BRAWLS = (0x1A3, 0x25, 0x10C)  # Bounty Hunter, Capture the Flag, CTF Blitz
COMPETITIVE_CTF_CARDS = (0x1C3,)
WINTER_BRAWLS = (
    0x98,
    0x107,
    0x4B,
    0x5B,
    0x1F,
)  # Freezethaw, Snowball Deathmatch, Yeti Hunter (two cards), Snowball Offensive
JUNKENSTEIN = (
    *(0x3C, 0x3D, 0x4A, 0x3E),  # Junkenstein's Revenge
    *(0x43, 0x44, 0x49, 0x45),  # Endless Revenge
    *range(0x16B, 0x171),  # its challenges
)
LUCIOBALL = (0x132, 0x35, 0x1AD)  # Lucioball Remix, Lucioball, Copa Lucioball
MISSION_ARCHIVES = (
    *(0x29, 0x24, 0x2A, 0x2B, 0x2C, 0x28, 0x2D, 0x2E),  # Uprising
    *(0x58, 0x3A, 0x59, 0x5A, 0x66, 0x67, 0x68, 0x69),  # Retribution
    *range(0x8A, 0x92),  # Storm Rising
)
CHALLENGE_MISSIONS = (0x119, 0x112, 0x1A9, 0x116, 0x127, 0x1AA, 0x111, 0x11B, 0x1AB)
SEASONAL_BRAWLS = (WINTER_BRAWLS, JUNKENSTEIN, LUCIOBALL, MISSION_ARCHIVES, LUNAR_BRAWLS)
EVENT_GROUPS = {
    "lunar": (LUNAR_BRAWLS, COMPETITIVE_CTF_CARDS),
    "winter": (WINTER_BRAWLS,),
    "halloween": (JUNKENSTEIN,),
    "summer": (LUCIOBALL,),
    "archives": (MISSION_ARCHIVES, CHALLENGE_MISSIONS),
    # The anniversary brought back every seasonal brawl.
    "anniversary": SEASONAL_BRAWLS,
    "anniversary_remix_1": SEASONAL_BRAWLS,
    "anniversary_remix_2": SEASONAL_BRAWLS,
    "malevento": ((0x1AE,), (0x1B0,)),  # Malevento Deathmatch, Malevento Team Deathmatch
}
# The everyday cards, as in the two retail Arcades we know (the capture, January 2022, and
# OverwatchArcade.today's record of 3 April 2022): Kanezaka Deathmatch and Team Deathmatch every day,
# more daily modes that change with the date (No Limits among them) in the slots events leave free,
# then Quick Play Classic and Mystery Heroes. Each card is its own menu group. With five event groups
# (the anniversary) those last two do not fit; retail's Arcade for that case is not known.
FIXED_DAILY = (0x15C, 0x34)  # Kanezaka Deathmatch, Team Deathmatch
ROTATING_DAILY = (
    0x1C,  # No Limits
    0x64,  # CTF: Ayutthaya Only
    0xAD,  # Mirrored Deathmatch
    0x1,  # Total Mayhem
    0x33,  # Low Gravity
    0x1E,  # Elimination
    0x3B,  # Deathmatch
    0x1D,  # Mystery Duel
    0x30,  # Limited Duel
    0xA6,  # Hero Gauntlet
    0x7A,  # Petra Deathmatch
    0x47,  # Chateau Deathmatch
)
PERMANENT = (0xEF, 0x2)  # Quick Play Classic, Mystery Heroes
DAILY = {*FIXED_DAILY, *ROTATING_DAILY}  # shown with 24 hours left
FLAG_12 = {0x1C, 0xEF, 0x2}  # the capture sets card flag 12 on No Limits, Quick Play Classic, Mystery Heroes
EVENT_CARDS = {card for groups in EVENT_GROUPS.values() for group in groups for card in group}


class Arcade:
    def __init__(self, templates: RetailTemplates, ranked: Ranked) -> None:
        self._templates = templates
        self._ranked = ranked
        self._cards = json.loads((DATA_DIR / "arcade_cards_174.json").read_text(encoding="utf-8"))

    def has_roles(self, card: int) -> bool:
        """Whether a mode card (a full 0C7 key or its index) is a role queue."""
        entry = self._cards.get(f"0x{card & 0xFFFFFFFF:X}")
        return bool(entry and entry.get("roles"))

    def pass_pool(self, card: int) -> int:
        """The priority pass pool of a mode card, 0 when it takes no passes (content/passes.py)."""
        entry = self._cards.get(f"0x{card & 0xFFFFFFFF:X}")
        return int(entry.get("passes", "0x0"), 16) if entry else 0

    def messages(self, profile: Profile, now: float) -> dict[tuple, dict]:
        """39802, 39810 and 39804 for the profile's events, keyed by (group, message id)."""
        cards = self.cards(profile, now)
        return {
            (ARCADE, 39802): self._card_lists(cards, profile),
            (ARCADE, 39810): {"+0x78": [self._window(card, now) for card in cards]},
            (ARCADE, 39804): self._busy(cards),
        }

    def cards(self, profile: Profile, now: float) -> list[int]:
        """The Arcade's cards (0C7 indexes): event groups first, then everyday ones, in 7 slots."""
        groups = []
        for event in profile.events or []:
            for group in EVENT_GROUPS.get(event, ()):
                if group not in groups:
                    groups.append(group)
        free = max(SLOTS - len(groups) - len(FIXED_DAILY) - len(PERMANENT), 0)
        day = int(now // DAY)
        rotating = [ROTATING_DAILY[(day + i) % len(ROTATING_DAILY)] for i in range(free)]
        groups += [(card,) for card in (*FIXED_DAILY, *rotating, *PERMANENT)]
        return [card for group in groups[:SLOTS] for card in group]

    def _card_lists(self, cards: list[int], profile: Profile) -> dict:
        # The quick play card stays as recorded; the competitive cards follow the profile's season.
        value = self._templates.first(ARCADE, 39802)
        value["+0x78"]["+0x0"] = [self._card(card) for card in cards]
        value["+0x78"]["+0xD0"] = self._competitive(value["+0x78"]["+0xD0"], profile)
        return value

    def _competitive(self, recorded: list[dict], profile: Profile) -> list[dict]:
        """The season's cards in the capture's shape: the role queue card (season 32's 1B3) with its
        open queue card (1B4) as a sub-card, then the open queue card itself."""
        season = self._ranked.season(profile)
        main = copy.deepcopy(recorded[0])
        main["+0x0"] = season.card
        main["+0x88"] = [season.open_card] if season.open_card else []
        main["+0xB2"] = season.has_roles  # flag 22, role queue
        cards = [main]
        if season.open_card:
            open_queue = copy.deepcopy(recorded[1])
            open_queue["+0x0"] = season.open_card
            open_queue["+0x18"] = int(self._cards[f"0x{season.open_card & 0xFFFFFFFF:X}"]["display"], 16)
            open_queue["+0x80"] = season.card
            cards.append(open_queue)
        return cards

    def _card(self, index: int) -> dict:
        data = self._cards[f"0x{index:X}"]
        # Flags 12-22: permanent, the seven from the asset, two unused, role queue.
        flags = [index in FLAG_12, *data["flags"], False, False, False]
        card = {
            "+0x0": CARD_BASE | index,
            "+0x8": 0,
            "+0x10": int(data["identifier"], 16),
            "+0x18": int(data["display"], 16),
            "+0x20": [int(reward, 16) for reward in data["rewards"]],
            "+0x38": [],
            "+0x50": [],
            "+0x68": [],
            "+0x80": 0,
            "+0x88": [],
            "+0xA0": 0,
            "+0xA4": 24 if index in DAILY else 0,
        }
        for number, flag in enumerate(flags):
            card[f"+0x{0xA8 + number:X}"] = flag
        return card

    @staticmethod
    def _window(card: int, now: float) -> dict:
        return {"+0x0": CARD_BASE | card, "+0x8": int(now) - DAY, "+0x10": int(now) + 30 * DAY}

    def _busy(self, cards: list[int]) -> dict:
        """The recorded busyness, without event cards that are not in this Arcade."""
        recorded = self._templates.first(ARCADE, 39804)["+0x78"]
        entries = [e for e in recorded if e["+0x0"] & ~CARD_BASE not in EVENT_CARDS - set(cards)]
        known = {e["+0x0"] for e in entries}
        for card in cards:
            if CARD_BASE | card not in known:
                entries.append({"+0x0": CARD_BASE | card, "+0x8": DEFAULT_BUSY})
        return {"+0x78": entries}
