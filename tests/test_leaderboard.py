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
from ow174.content.ranked import DAMAGE, TANK
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


class LeaderboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = Schemas()
        cls.content = Content(cls.schemas, RetailTemplates(), ItemDB())

    def setUp(self):
        self.me = Player(
            "Me",
            Profile(ratings={"tank": 3000, "damage": 4444, "support": 1111, "open": 2222}),
            Identity.for_account(0x10000001),
        )
        self.friend = Player(
            "Friend",
            Profile(ratings={"tank": 3500, "open": 1800}, game_region="europe"),
            Identity.for_account(0x10000002),
        )
        self.stranger = Player(
            "Stranger", Profile(ratings={"tank": 2000, "open": 4000}), Identity.for_account(0x10000003)
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
        self.assertEqual(self.rows(TANK_ONLY), [(0x10000001, TANK, 3000), (0x10000003, TANK, 2000)])

    def test_all_roles_lists_each_player_once_by_best_role(self):
        # Stranger: tank 2000, damage and support at the default 2333.
        self.assertEqual(self.rows(ALL_ROLES), [(0x10000001, DAMAGE, 4444), (0x10000003, DAMAGE, 2333)])

    def test_region_friends_and_seasons_filter(self):
        self.assertEqual({row[0] for row in self.rows(EUROPE)}, {0x10000002})
        self.assertEqual({row[0] for row in self.rows(FRIENDS)}, {0x10000001, 0x10000002})
        self.assertEqual(self.rows(SEASON_25), [])

    def test_the_open_queue_board_uses_the_open_rating(self):
        self.assertEqual(self.rows(OPEN_QUEUE), [(0x10000003, 0, 4000), (0x10000001, 0, 2222)])

    def test_top_500_needs_sms_protect_and_25_matches(self):
        # The game's rules text (82C5.07C): SMS Protect on and 25 matches completed.
        self.stranger.profile.sms_protect = False
        self.me.profile.matches = {"tank": 24}
        self.assertEqual(self.rows(TANK_ONLY), [])
        self.me.profile.matches = {"tank": 25}
        self.assertEqual(self.rows(TANK_ONLY), [(0x10000001, TANK, 3000)])

    def test_places_are_the_rows_of_the_players_region(self):
        players = [self.me, self.friend, self.stranger]
        places = self.content.leaderboard.places(self.me.profile, players)
        self.assertEqual(places, {"tank": 1, "damage": 1, "support": 2, "open": 2, "ctf": 1})
        # Friend plays in Europe alone.
        self.assertEqual(self.content.leaderboard.places(self.friend.profile, players)["tank"], 1)
        self.stranger.profile.sms_protect = False
        self.assertEqual(self.content.leaderboard.places(self.me.profile, players)["open"], 1)
        self.me.profile.matches = {"tank": 24}
        self.assertNotIn("tank", self.content.leaderboard.places(self.me.profile, players))

    def test_the_ctf_board_uses_the_ctf_rating(self):
        self.me.profile.ratings["ctf"] = 3900
        self.assertEqual(self.rows(0x00010200000001C3)[0], (0x10000001, 0, 3900))

    def test_the_career_profile_carries_the_ratings(self):
        profile = Profile(ratings={"damage": 3100})
        identity = Identity.for_account(0x10000003)
        (full, _summary) = self.content.career.profile(profile, identity, {"+0x0": 0x10000003})
        cards = full[2]["+0x90"]["+0x98"]["+0x0"]
        self.assertEqual(len(cards), 3)
        self.assertIn(3100, [r["+0x18"] for card in cards for r in card["+0x0"]])

    def test_the_hello_carries_the_game_region(self):
        hello = self.content.player.hello(Profile(game_region="europe", region="RU"), Identity.for_account(1))
        self.assertEqual((hello["+0xF8"], hello["+0x14C"], hello["+0x150"]), ("RUS", 2, 2))


if __name__ == "__main__":
    unittest.main()
