"""The competitive leaderboard (Top 500): message 39000, the answer to 39100.

The client asks for a page by key and page number, and keeps the answer for 15 s under that key and
page, so the answer repeats both. The Top 500 screen packs its filters into the key:

    bits 0-15 season, 24-31 role (0 before role queue, 1 damage, 2 tank, 3 support, 4 all roles),
    32-47 friends only, 48-63 region (1 Americas, 2 Europe, 3 Asia; 5 with friends only)

(the role numbers are the ones the client's group check uses). Open queue and Competitive CTF
boards name a card instead: card in bits 0-31, 2 in bits 40-47, region in bits 48-55
(0x00010200000001B4 is season 32's open queue in the Americas).

An entry is a player summary, the same as 39002. The screen sorts the entries by the f64 at +0xB8,
highest first, and takes names and icons from the client's player card cache.

The game's own rules for the board (string 82C5.07C): the best 500 of each region, Battle.net SMS
Protect on, 25 matches completed, and only the region with the most matches counts (ours is the
account's game region). There is no rating floor.
"""

from dataclasses import dataclass

from ow174.accounts.profile import Profile
from ow174.catalog.regions import game_region_number
from ow174.content.identity import Identity
from ow174.content.player import PlayerMessages
from ow174.content.queue import ROLE_NUMBERS
from ow174.content.ranked import (
    CARD_BASE,
    COMPETITIVE_CTF,
    QUEUE_NAMES,
    Ranked,
    matches_of,
    rating_of,
    tier,
)

TOP = 500
MATCHES_NEEDED = 25
CARD_BOARD = 2


def on_board(profile: Profile, queue: str) -> bool:
    return profile.sms_protect and matches_of(profile, queue) >= MATCHES_NEEDED


@dataclass
class Player:
    name: str
    profile: Profile
    identity: Identity


@dataclass
class Filters:
    card: int | None  # None: a season board
    season: int
    role: int  # a ROLE_NUMBERS key; anything else lists every role
    region: int  # 0: any
    friends_only: bool

    @classmethod
    def from_key(cls, key: int) -> "Filters":
        if (key >> 40) & 0xFF == CARD_BOARD:
            return cls(CARD_BASE | (key & 0xFFFFFFFF), 0, 0, (key >> 48) & 0xFF, False)
        friends_only = bool((key >> 32) & 0xFFFF)
        region = 0 if friends_only else (key >> 48) & 0xFFFF
        return cls(None, key & 0xFFFF, (key >> 24) & 0xFF, region, friends_only)


class Leaderboard:
    def __init__(self, player: PlayerMessages, ranked: Ranked) -> None:
        self._player = player
        self._ranked = ranked

    def page(self, request: dict, players: list[Player], me: Player, friends: set[str]) -> dict:
        """39000 for a 39100 request {key, page}: the players the filters keep, best rating first."""
        key = request.get("+0x0", 0)
        filters = Filters.from_key(key)
        best = {}  # name -> (rating, card, role, player): "all roles" lists a player once, by best role
        for card, role in self.boards(filters, me.profile):
            queue = self._ranked.queue(me.profile, card, role)
            for player in players:
                if filters.friends_only and player.name != me.name and player.name.lower() not in friends:
                    continue
                if filters.region and game_region_number(player.profile.game_region) != filters.region:
                    continue
                if not on_board(player.profile, queue):
                    continue
                rating = rating_of(player.profile, queue)
                if player.name not in best or rating > best[player.name][0]:
                    best[player.name] = (rating, card, role, player)
        rows = sorted(best.values(), key=lambda row: row[0], reverse=True)
        entries = []
        for place, (rating, card, role, player) in enumerate(rows[:TOP], 1):
            entries.append(self.entry(player, card, role, rating, place))
        return {"+0x78": {"+0x0": entries, "+0x18": {"+0x0": key, "+0x8": request.get("+0x8", 0)}}}

    def places(self, profile: Profile, players: list[Player]) -> dict[str, int]:
        """The profile's place in each queue's board of its own region, for the queues it is in
        the Top 500 of."""
        region = game_region_number(profile.game_region)
        rivals = [player for player in players if game_region_number(player.profile.game_region) == region]
        places = {}
        for queue in QUEUE_NAMES:
            board = [player for player in rivals if on_board(player.profile, queue)]
            board.sort(key=lambda player: rating_of(player.profile, queue), reverse=True)
            for place, player in enumerate(board[:TOP], 1):
                if player.profile is profile:
                    places[queue] = place
        return places

    def boards(self, filters: Filters, profile: Profile) -> list[tuple[int, int]]:
        """The (card, role) ratings a request lists; only the profile's running season has any."""
        season = self._ranked.season(profile)
        if filters.card is not None:
            if filters.card in (season.open_card, COMPETITIVE_CTF):
                return [(filters.card, 0)]
            return []
        if filters.season != season.number:
            return []
        if not season.has_roles:
            return [(season.card, 0)]
        if filters.role in ROLE_NUMBERS:
            return [(season.card, ROLE_NUMBERS[filters.role])]
        return [(season.card, role) for role in ROLE_NUMBERS.values()]

    def entry(self, player: Player, card: int, role: int, rating: int, place: int) -> dict:
        entry = self._player.summary(player.profile, player.identity)["+0x88"]
        entry["+0x68"] = {
            "+0x0": card,
            "+0x8": role,
            "+0x10": rating,
            "+0x12": place,
            "+0x14": tier(rating),
            "+0x15": 0,
            "+0x16": True,
        }
        entry["+0xB8"] = float(rating)
        return entry
