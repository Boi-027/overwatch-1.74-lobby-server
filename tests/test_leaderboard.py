"""The competitive leaderboard (39100 -> 39000) and the ratings in the career profile."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.content.identity import Identity
from ow174.content.leaderboard import Player
from ow174.content.ranked import DAMAGE, QUEUE_NAMES, TANK
from ow174.jam.codec import Schemas
from ow174.jam.groups import PROFILES

# Keys the client sent: season 32 with every role, tank only, and Europe; season 25; friends only;
# season 32's open queue board.
ALL_ROLES = 0x0001000004000020
TANK_ONLY = 0x0001000002000020
EUROPE = 0x0002000004000020
SEASON_25 = 0x0001000004000019
FRIENDS = 0x0005000104000020
OPEN_QUEUE = 0x00010200000001B4
# Enough wins for the Top 500 in every queue.
WINS = dict.fromkeys(QUEUE_NAMES, 25)


class LeaderboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = Schemas()
        cls.content = Content(cls.schemas, RetailTemplates(), ItemDB())

    def setUp(self):
        self.me = Player(
            "Me",
            Profile(ratings={"tank": 3000, "damage": 4444, "support": 3111, "open": 3222}, wins=dict(WINS)),
            Identity.for_account(0x10000001),
        )
        self.friend = Player(
            "Friend",
            Profile(ratings={"tank": 3500, "open": 3800}, game_region="europe", wins=dict(WINS)),
            Identity.for_account(0x10000002),
        )
        self.stranger = Player(
            "Stranger",
            Profile(ratings={"tank": 3200, "open": 4000}, wins=dict(WINS)),
            Identity.for_account(0x10000003),
        )
        self.me.profile.friends = ["friend"]

    def rows(self, key):
        players = [self.me, self.friend, self.stranger]
        page = self.content.leaderboard.page({"+0x0": key, "+0x8": 0}, players, self.me, {"friend"})
        self.assertEqual(page["+0x78"]["+0x18"], {"+0x0": key, "+0x8": 0})
        encoded = self.schemas.encode(PROFILES, 39000, page)
        self.assertEqual(self.schemas.decode(PROFILES, 39000, encoded), page)
        rows = []
        for entry in page["+0x78"]["+0x0"]:
            rows.append((entry["+0x0"]["+0x0"]["+0x0"], entry["+0x68"]["+0x8"], entry["+0x68"]["+0x10"]))
        return rows

    def test_one_role_ranks_that_role(self):
        self.assertEqual(self.rows(TANK_ONLY), [(0x10000003, TANK, 3200), (0x10000001, TANK, 3000)])

    def test_all_roles_lists_each_player_once_by_best_role(self):
        # Stranger: tank 3200; damage and support at the default 2333 are below the board.
        self.assertEqual(self.rows(ALL_ROLES), [(0x10000001, DAMAGE, 4444), (0x10000003, TANK, 3200)])

    def test_region_friends_and_seasons_filter(self):
        self.assertEqual({row[0] for row in self.rows(EUROPE)}, {0x10000002})
        self.assertEqual({row[0] for row in self.rows(FRIENDS)}, {0x10000001, 0x10000002})
        self.assertEqual(self.rows(SEASON_25), [])

    def test_the_open_queue_board_uses_the_open_rating(self):
        self.assertEqual(self.rows(OPEN_QUEUE), [(0x10000003, 0, 4000), (0x10000001, 0, 3222)])

    def test_top_500_needs_sms_protect_a_diamond_rating_and_25_wins(self):
        # Stricter than the game's text (82C5.07C: SMS Protect and 25 matches completed, any rating).
        self.stranger.profile.sms_protect = False
        self.me.profile.wins = {"tank": 24}
        self.assertEqual(self.rows(TANK_ONLY), [])
        self.me.profile.wins = {"tank": 25}
        self.assertEqual(self.rows(TANK_ONLY), [(0x10000001, TANK, 3000)])
        self.me.profile.matches = {"tank": 24}  # no more wins than matches
        self.assertEqual(self.rows(TANK_ONLY), [])
        self.me.profile.matches = {}
        self.me.profile.ratings["tank"] = 2999
        self.assertEqual(self.rows(TANK_ONLY), [])

    def test_places_are_the_rows_of_the_players_region(self):
        players = [self.me, self.friend, self.stranger]
        places = self.content.leaderboard.places(self.me.profile, players)
        # CTF and Lucio Cup stay at the default 2333, below the board.
        self.assertEqual(places, {"tank": 2, "damage": 1, "support": 1, "open": 2})
        # Friend plays in Europe alone.
        self.assertEqual(self.content.leaderboard.places(self.friend.profile, players)["tank"], 1)
        self.stranger.profile.sms_protect = False
        self.assertEqual(self.content.leaderboard.places(self.me.profile, players)["open"], 1)
        self.me.profile.wins = {"tank": 24}
        self.assertNotIn("tank", self.content.leaderboard.places(self.me.profile, players))

    def test_the_ctf_board_uses_the_ctf_rating(self):
        self.me.profile.ratings["ctf"] = 3900
        self.assertEqual(self.rows(0x00010200000001C3)[0], (0x10000001, 0, 3900))

    def test_the_career_profile_carries_the_ratings(self):
        # The role and open queue cards; an event's competitive card only while its Arcade shows it.
        profile = Profile(ratings={"damage": 3100})
        identity = Identity.for_account(0x10000003)
        (full, _summary) = self.content.career.profile(profile, identity, {"+0x0": 0x10000003})
        cards = full[2]["+0x90"]["+0x98"]["+0x0"]
        self.assertEqual(len(cards), 2)
        self.assertIn(3100, [r["+0x18"] for card in cards for r in card["+0x0"]])

    def test_the_career_profile_shows_the_matches_won_per_role(self):
        # The role tables sum a hero stat per role themselves, so a role's wins go on one of its heroes
        # (062/039), in "All modes" (key 0) and in the running season's category.
        profile = Profile(wins={"damage": 12, "support": 3}, matches={"damage": 30})
        identity = Identity.for_account(0x10000003)
        (full, _summary) = self.content.career.profile(profile, identity, {"+0x0": 0x10000003})
        crc, msg_id, value = full
        classes = self.content.collection.items.hero_classes
        categories = {c["+0x18"]: c["+0x0"] for c in value["+0x90"]["+0xB0"]}
        for key in (0, (32 << 16) | 0x03, (32 << 16) | 0x23):
            won = {}
            for entry in categories[key]:
                for stat in entry["+0x0"]:
                    if stat["+0x0"] == 0x0860000000000039:
                        won[classes[entry["+0x18"]]] = won.get(classes[entry["+0x18"]], 0) + stat["+0x8"]
            self.assertEqual(won, {"Damage": 12.0, "Support": 3.0}, hex(key))
        self.schemas.encode(crc, msg_id, value)

    def test_the_favourite_heroes_stay_the_same_with_a_random_menu_hero(self):
        profile = Profile(player_name="Mei", lobby_hero="random")
        first = self.content.career.stats(profile)[0]["+0x0"]
        self.content.menu_hero.reroll(profile)  # a new random menu hero
        self.assertEqual(self.content.career.stats(profile)[0]["+0x0"], first)

    def test_the_hello_carries_the_game_region(self):
        hello = self.content.player.hello(Profile(game_region="europe", region="RU"), Identity.for_account(1))
        self.assertEqual((hello["+0xF8"], hello["+0x14C"], hello["+0x150"]), ("RUS", 2, 2))


if __name__ == "__main__":
    unittest.main()
