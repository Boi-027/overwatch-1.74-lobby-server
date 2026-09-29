"""The career profile screen: per-hero stats, mode rules and the full profile answer."""

from ow174.accounts.profile import Profile
from ow174.catalog.templates import RetailTemplates
from ow174.content.collection import Collection
from ow174.content.identity import Identity
from ow174.content.menu_hero import MenuHero
from ow174.content.player import PlayerMessages, endorsement
from ow174.content.ranked import Ranked
from ow174.jam.groups import LOBBY, MODE_RULES, PROFILES

TIME_PLAYED_STAT = 0x0860000000000021  # the lifetime "Time Played" stat of a hero, in seconds
MENU_HERO_HOURS = 500.0
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
        """Time played per hero, as one stat category. The menu hero is the most played."""
        menu_hero = self._menu_hero.choose(profile)
        hours_played = [(menu_hero, MENU_HERO_HOURS)]
        other_heroes = [hero for hero in self._collection.heroes if hero != menu_hero]
        for rank, hero in enumerate(other_heroes[:OTHER_HEROES_SHOWN]):
            hours_played.append((hero, 50.0 - 10 * rank))

        hero_stats = []
        for hero, hours in hours_played:
            if not hero:  # 0 means the profile shows no menu hero
                continue
            time_played = {"+0x0": TIME_PLAYED_STAT, "+0x8": hours * 3600}
            hero_stats.append({"+0x0": [time_played], "+0x18": hero})
        return [{"+0x0": hero_stats, "+0x18": 0}]

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
