"""Competitive play: the ranked state, message 36300.

It comes from the 1.68 retail capture: season 32 (January to March 2022) on the role queue card 1B3
(tank, damage and support ratings), the open queue card 1B4, Lunar New Year's Competitive CTF (1C3),
and 73 past seasons with their reward tiers. 1.74 added three fields to each rating record; they are
0. The ratings themselves are the profile's (the capture's 2333 by default).

An event's competitive Arcade card (Competitive CTF, the Summer Games' Lucio Cup 1AD) runs a season
of its own while the Arcade shows it, as 1C3 did in the capture. The group finder offers such a card
only when every member of the group has a rating on it (the group check below).

Every competitive season is a card of its own (0C7). The season list (+0x98) gives its number
(+0x118), and from season 23 an open queue card names it as its parent (+0xF0): season 1 is 0xDD,
18 (the first with roles) 0xC2, 32 0x1B3 with open queue 0x1B4. The profile picks the season; its
cards take season 32's place, its dates move to the server's clock, and later seasons are left out.

The client looks a rating up by card, role, platform and matchmaking pool. It asks for the PC
platform and, when a PC player is in the group (player card +0x3C = 1), the PC pool; a console-only
group asks for pool 16020070. The capture's ratings have pool 0, which 1.74 never asks for, so every
competitive card was locked with "group members' skill ratings differ too much".

A rating record (36300 +0x80, 36302), as the client's rank icon and rating getters read it
(0x7FF78934CFB0, 0x7FF789AB61F0, 0x7FF789930400):
    +0x18 skill rating (0: none this season; below 500 it shows as "<500"), +0x1A season high,
    +0x1C Top 500 place (the rank icon becomes the Top 500 one), +0x1E season high place,
    +0x20 the rating shown while placements run, +0x22 tier of +0x18, +0x23 tier of +0x1A,
    +0x28 placement results (1, 2 or 3 each; their count is the matches played), +0x40 placements run.
A party member carries the same in 20700 +0x68: +0x10 rating, +0x12 place, +0x14 placement rating,
+0x16 tier, +0x17 placements run, +0x18 placement tier.
"""

import copy
from collections.abc import Callable, Iterable
from typing import NamedTuple

from ow174.accounts.profile import Profile
from ow174.catalog.templates import RetailTemplates
from ow174.content.clock import DAY, from_stu_datetime, server_time, stu_datetime
from ow174.jam.groups import RANKED

# Lowest rating of each tier from silver up: bronze 0, silver 1, gold 2, platinum 3, diamond 4,
# master 5, grandmaster 6.
TIER_FLOORS = (1500, 2000, 2500, 3000, 3500, 4000)
DAYS_INTO_SEASON = 7
SEASON_DATES = ("+0x20", "+0x28", "+0x30", "+0x38")  # start, (a later mark), end, end
PC_PLATFORM = 15959616
PC_POOL = 16025156

CARD_BASE = 0x0630000000000000  # queue and Arcade cards (0C7)
ROLE_QUEUE = 0x06300000000001B3  # the capture's season, 32
OPEN_QUEUE = 0x06300000000001B4
COMPETITIVE_CTF = 0x06300000000001C3  # Lunar New Year 2022, the capture's
LUCIO_CUP = 0x06300000000001AD  # Summer Games 2021 (Copa Lucioball)
EVENT_QUEUES = {COMPETITIVE_CTF: "ctf", LUCIO_CUP: "lucio"}  # one rating each, no roles
CURRENT_SEASON = 32
FIRST_ROLE_QUEUE_SEASON = 18
# Role identifiers (01C), named after the heroes that carry them.
TANK = 0x0D80000000000850
DAMAGE = 0x0D8000000000084E
SUPPORT = 0x0D80000000000851
ROLES = {TANK: "tank", DAMAGE: "damage", SUPPORT: "support"}
# The profile keeps one rating per queue.
QUEUE_NAMES = ("tank", "damage", "support", "open", "ctf", "lucio")
DEFAULT_RATING = 2333
MAX_RATING = 5000
# Matches played this season: placements run for the first 5.
DEFAULT_MATCHES = 25
MAX_MATCHES = 9999
PLACEMENT_MATCHES = 5
PLACEMENT_WIN = 1  # a placement result; the client draws 1, 2 and 3 as different marks


def tier(rating: int) -> int:
    """The competitive tier of a rating (2333 is gold, 2)."""
    return sum(rating >= floor for floor in TIER_FLOORS)


def rating_of(profile: Profile, queue: str) -> int:
    """The profile's rating in a queue ("tank", "damage", "support", "open", "ctf" or "lucio")."""
    rating = (profile.ratings or {}).get(queue, DEFAULT_RATING)
    return max(1, min(int(rating), MAX_RATING))


def matches_of(profile: Profile, queue: str) -> int:
    """Competitive matches the profile played this season in a queue."""
    matches = (profile.matches or {}).get(queue, DEFAULT_MATCHES)
    return max(0, min(int(matches), MAX_MATCHES))


def wins_of(profile: Profile, queue: str) -> int:
    """Competitive matches the profile won this season in a queue, never more than it played."""
    wins = (profile.wins or {}).get(queue, 0)
    return max(0, min(int(wins), matches_of(profile, queue)))


class Season(NamedTuple):
    number: int
    card: int  # role queue from season 18
    open_card: int  # 0 before season 23

    @property
    def has_roles(self) -> bool:
        return self.number >= FIRST_ROLE_QUEUE_SEASON


class Ranked:
    def __init__(self, templates: RetailTemplates) -> None:
        self._templates = templates
        # A profile's Top 500 place in each queue ({"tank": 3}). A place depends on every account,
        # so the lobby sets this.
        self.places: Callable[[Profile], dict[str, int]] = lambda profile: {}
        # The event competitive cards the profile's Arcade shows (content/arcade.py sets this).
        self.event_cards: Callable[[Profile], list[int]] = lambda profile: [COMPETITIVE_CTF]
        entries = templates.first(RANKED, 36300)["+0x98"]
        self.seasons = {}
        for entry in entries:
            number, card = entry["+0x118"], entry["+0x18"]
            if number:
                children = [child["+0x18"] for child in entries if child["+0xF0"] == card]
                self.seasons[number] = Season(number, card, children[0] if children else 0)

    def season(self, profile: Profile) -> Season:
        return self.seasons.get(profile.season, self.seasons[CURRENT_SEASON])

    def queue(self, profile: Profile, card: int, role: int) -> str | None:
        """The queue a rating on a card of the profile's season counts in."""
        season = self.season(profile)
        if card in EVENT_QUEUES and not role:
            return EVENT_QUEUES[card]
        if card == season.open_card and not role:
            return "open"
        if card == season.card:
            if season.has_roles:
                return ROLES.get(role)
            return None if role else "open"
        return None

    def state(self, profile: Profile) -> dict:
        now = int(server_time(profile))
        season = self.season(profile)
        value = self._templates.first(RANKED, 36300)
        value["+0x78"] = season.card
        value["+0x80"]["+0x0"] = self.cards(profile)
        later = self._later_cards(season)
        value["+0x98"] = [entry for entry in value["+0x98"] if entry["+0x18"] not in later]
        value["+0xC8"] = [card for card in value["+0xC8"] if card not in later]
        recorded_running = {entry["+0x0"]: entry for entry in value["+0xB0"]}
        running = [card for card in (season.card, season.open_card, *self.event_cards(profile)) if card]
        value["+0xB0"] = []
        for entry in value["+0x98"]:
            card = entry["+0x18"]
            if card not in running:
                continue
            start = from_stu_datetime(entry["+0x20"]["+0x0"])
            shift = now - DAYS_INTO_SEASON * DAY - start
            for key in SEASON_DATES:
                entry[key] = {"+0x0": stu_datetime(from_stu_datetime(entry[key]["+0x0"]) + shift)}
            left = copy.deepcopy(recorded_running.get(card, recorded_running[ROLE_QUEUE]))
            left["+0x0"] = card
            left["+0x8"] = float(from_stu_datetime(entry["+0x30"]["+0x0"]) - now)
            value["+0xB0"].append(left)
        return value

    def _later_cards(self, season: Season) -> set[int]:
        """Cards of the seasons after this one, with their open queue cards."""
        later = set()
        for number, other in self.seasons.items():
            if number > season.number:
                later.update(card for card in (other.card, other.open_card) if card)
        return later

    def card_ratings(self, profile: Profile) -> list[dict]:
        """36302 values: the ratings of each competitive card. Retail sent one per card right after
        login, before the full state."""
        return [{"+0x78": entry} for entry in self.cards(profile)]

    def cards(self, profile: Profile) -> list[dict]:
        """The profile's ratings on each competitive card of its season, as 36300 (+0x80) and the
        career profile (+0x98) carry them."""
        return self._rated_cards(profile, self.event_cards(profile))

    def _rated_cards(self, profile: Profile, event_cards: Iterable[int]) -> list[dict]:
        season = self.season(profile)
        recorded = {card["+0x18"]: card for card in self._templates.first(RANKED, 36300)["+0x80"]["+0x0"]}
        # The capture's role queue card has one rating per role, its open queue card a single one.
        main = copy.deepcopy(recorded[ROLE_QUEUE] if season.has_roles else recorded[OPEN_QUEUE])
        main["+0x18"] = season.card
        cards = [main]
        if season.open_card:
            open_queue = copy.deepcopy(recorded[OPEN_QUEUE])
            open_queue["+0x18"] = season.open_card
            cards.append(open_queue)
        for card in event_cards:
            event = copy.deepcopy(recorded[COMPETITIVE_CTF])  # the capture's one-rating card
            event["+0x18"] = card
            cards.append(event)
        places = self.places(profile)
        for card in cards:
            # The client shows a card's season intro until this is on; closing it sends 36200.
            card["+0x2C"] = card["+0x18"] in (profile.seasons_seen or [])
            for rating in card["+0x0"]:
                rating["+0x10"] = PC_PLATFORM
                rating["+0x14"] = PC_POOL
                queue = self.queue(profile, card["+0x18"], rating["+0x0"])
                if queue:
                    rating.update(_rating(profile, queue, places.get(queue, 0)))
        return cards

    def party_ratings(self, profile: Profile) -> list[dict]:
        """The ratings a party member carries in 20700 (+0x68), one entry per competitive card.

        The client's group check drops every role choice where a member has no rating for the card,
        then compares the highest and lowest rating with 1000 (500 at tier 5, 350 at tier 6); while
        placements run it takes the placement rating and tier. Every event card is here whatever
        the member's own events: events are per profile on this server, and one member without a
        rating on the card greys it out for the whole group (0x7FF789AA3D80, check 0x7FF78934F3A0).
        """
        ratings = []
        for card in self._rated_cards(profile, EVENT_QUEUES):
            roles = []
            for rating in card["+0x0"]:
                roles.append(
                    {
                        "+0x0": rating["+0x0"],
                        "+0x8": rating["+0x10"],
                        "+0xC": rating["+0x14"],
                        "+0x10": rating["+0x18"],
                        "+0x12": rating["+0x1C"],
                        "+0x14": rating["+0x20"],
                        "+0x16": rating["+0x22"],
                        "+0x17": rating["+0x40"],
                        "+0x18": tier(rating["+0x20"]),
                    }
                )
            ratings.append({"+0x0": roles, "+0x18": card["+0x18"], "+0x20": 0})
        return ratings


def _rating(profile: Profile, queue: str, place: int) -> dict:
    """The fields of a rating record for one queue of the profile."""
    rating = rating_of(profile, queue)
    matches = matches_of(profile, queue)
    if matches < PLACEMENT_MATCHES:
        # No rating yet: the cards show +0x20 and a mark for each placement match played.
        return {
            "+0x18": 0,
            "+0x1A": 0,
            "+0x1C": 0,
            "+0x1E": 0,
            "+0x20": rating,
            "+0x22": 0,
            "+0x23": 0,
            "+0x28": _results(matches),
            "+0x40": True,
        }
    return {
        "+0x18": rating,
        "+0x1A": rating,
        "+0x1C": place,
        "+0x1E": place,
        "+0x20": rating,
        "+0x22": tier(rating),
        "+0x23": tier(rating),
        # The career profile lists a season only with placement results (0x7FF78934CA50).
        "+0x28": _results(PLACEMENT_MATCHES),
        "+0x40": False,
    }


def _results(matches: int) -> list[dict]:
    return [{"+0x0": PLACEMENT_WIN} for _ in range(matches)]
