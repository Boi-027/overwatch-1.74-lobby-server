"""The career profile screen: per-hero stats, mode rules and the full profile answer."""

import zlib

from ow174.accounts.profile import Profile
from ow174.catalog.templates import RetailTemplates
from ow174.content.collection import Collection
from ow174.content.identity import Identity
from ow174.content.menu_hero import MenuHero
from ow174.content.player import PlayerMessages, endorsement
from ow174.content.ranked import ROLES, Ranked, wins_of
from ow174.jam.groups import LOBBY, MODE_RULES, PROFILES

TIME_PLAYED_STAT = 0x0860000000000021  # a hero's lifetime "Time Played" (062/021), in seconds
# A hero's "Matches won" (062/039). The career profile's role tables sum a hero stat per role
# themselves, as they do with the time played: "Matches won" of a role is 062/713, the sum of 062/039
# (0x7FF789936F33 reads it). Our wins are per role queue, so each role's go on one hero of the role.
MATCHES_WON_STAT = 0x0860000000000039
# The stats category of "All modes" has key 0. A competitive season's has key (season << 16) | 3,
# with 0x20 when the season item has the flag 0xD800000000084E7 (0x7FF7893528A0, 0x7FF78992D210);
# both keys are sent.
ALL_MODES = 0
SEASON_KEY_KIND = 3
SEASON_KEY_FLAG = 0x20
MAIN_HERO_HOURS = 500.0
OTHER_HEROES_SHOWN = 4


class CareerMessages:
    def __init__(
        self,
        templates: RetailTemplates,
        collection: Collection,
        menu_hero: MenuHero,
        player: PlayerMessages,
        ranked: Ranked,
    ) -> None:
        self._templates = templates
        self._collection = collection
        self._menu_hero = menu_hero
        self._player = player
        self._ranked = ranked

    def stats(self, profile: Profile) -> list[dict]:
        """The stat categories: "All modes" with the time played per hero and the season's matches
        won, and the running competitive season with its matches won."""
        played = self._hours_played(profile)
        won = self._matches_won(profile, played)
        all_modes = {hero: [_stat(TIME_PLAYED_STAT, hours * 3600)] for hero, hours in played}
        for hero, wins in won.items():
            all_modes.setdefault(hero, []).append(_stat(MATCHES_WON_STAT, wins))
        season = {hero: [_stat(MATCHES_WON_STAT, wins)] for hero, wins in won.items()}
        key = (self._ranked.season(profile).number << 16) | SEASON_KEY_KIND
        return [
            _category(all_modes, ALL_MODES),
            _category(season, key),
            _category(season, key | SEASON_KEY_FLAG),
        ]

    def _hours_played(self, profile: Profile) -> list[tuple[int, float]]:
        """The heroes shown as played, most first. The hero picked for the menu is the main one;
        without one the main hero comes from the player's name, so the favourites stay put (a random
        menu hero changes between runs)."""
        heroes = self._collection.heroes
        main = self._menu_hero.picked(profile)
        if not main:
            main = heroes[zlib.crc32(profile.player_name.encode("utf-8")) % len(heroes)]
        others = [hero for hero in heroes if hero != main][:OTHER_HEROES_SHOWN]
        return [(main, MAIN_HERO_HOURS)] + [(hero, 50.0 - 10 * rank) for rank, hero in enumerate(others)]

    def _matches_won(self, profile: Profile, played: list[tuple[int, float]]) -> dict[int, int]:
        """Each role queue's wins of the season on one hero of the role: the most played one, else
        the first of the catalog."""
        classes = self._collection.items.hero_classes
        heroes = [hero for hero, _ in played] + list(self._collection.heroes)
        won: dict[int, int] = {}
        for queue in ROLES.values():
            wins = wins_of(profile, queue)
            hero = next((hero for hero in heroes if classes.get(hero, "").lower() == queue), 0)
            if wins and hero:
                won[hero] = won.get(hero, 0) + wins
        return won

    def mode_rules(self, profile: Profile) -> dict:
        """27202: the capture's stat catalog per hero and map, plus our career stats."""
        value = self._templates.first(MODE_RULES, 27202)
        value["+0x78"] = self.stats(profile)
        value["+0xA8"] = self.stats(profile)
        return value

    def profile(
        self, profile: Profile, identity: Identity, target: dict, request_id: dict | None = None
    ) -> list[tuple]:
        """The answer to a career profile request (22206).

        Only our own player has a full profile (20807) and summary (39002). Anyone else gets a
        profile status (39001). 20807 repeats the request's own id (22206 +0x78): with another id the
        client leaves the screen empty (ProCore research).
        """
        if target.get("+0x0") != identity.account_lo:
            return [(PROFILES, 39001, {"+0x78": target, "+0x88": 1})]
        full_profile = {
            "+0x78": request_id or identity.account,
            # False: the profile is inline at +0x90. True would mean compressed in the +0x218 blob.
            "+0x88": False,
            "+0x90": self._profile_body(profile, identity),
            "+0x218": b"",
        }
        return [(LOBBY, 20807, full_profile), (PROFILES, 39002, self._player.summary(profile, identity))]

    def _profile_body(self, profile: Profile, identity: Identity) -> dict:
        return {
            "+0x0": self._collection.progression(profile)["+0x78"],
            "+0x80": self._collection.hero_catalog(profile)["+0x80"],
            "+0x98": {"+0x0": self._ranked.cards(profile)},  # the same card ratings as 36300
            "+0xB0": self.stats(profile),
            "+0xC8": self.stats(profile),
            "+0xE0": endorsement(profile.endorsement_level),
            "+0x100": identity.account,
            "+0x110": [],
            "+0x128": 0,
            "+0x12C": 0,
            "+0x130": False,
            "+0x138": profile.player_name,
            "+0x160": "",
        }


def _stat(stat: int, value: float) -> dict:
    return {"+0x0": stat, "+0x8": float(value)}


def _category(stats_by_hero: dict[int, list[dict]], key: int) -> dict:
    return {"+0x0": [{"+0x0": stats, "+0x18": hero} for hero, stats in stats_by_hero.items()], "+0x18": key}
