"""Competitive play: the ranked state from the capture, with the running seasons on our clock."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile
from ow174.catalog.templates import RetailTemplates
from ow174.content.clock import from_stu_datetime, server_time, stu_datetime
from ow174.content.ranked import COMPETITIVE_CTF, LUCIO_CUP, PC_PLATFORM, PC_POOL, TANK, Ranked
from ow174.jam.codec import Schemas
from ow174.jam.groups import RANKED

ROLE_QUEUE = 0x06300000000001B3
OPEN_QUEUE = 0x06300000000001B4


class RankedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = RetailTemplates()
        cls.ranked = Ranked(cls.templates)

    def test_packed_dates_round_trip(self):
        self.assertEqual(from_stu_datetime(stu_datetime(1_790_000_000)), 1_790_000_000)

    def test_the_running_season_contains_the_server_date(self):
        profile = Profile(server_date="2026-09-29")
        state = self.ranked.state(profile)
        (season,) = [s for s in state["+0x98"] if s["+0x18"] == ROLE_QUEUE]
        now = server_time(profile)
        self.assertLess(from_stu_datetime(season["+0x20"]["+0x0"]), now)
        self.assertGreater(from_stu_datetime(season["+0x30"]["+0x0"]), now)
        (left,) = [e["+0x8"] for e in state["+0xB0"] if e["+0x0"] == ROLE_QUEUE]
        self.assertGreater(left, 0)

    def test_past_seasons_keep_their_dates(self):
        recorded = self.templates.first(RANKED, 36300)["+0x98"][0]
        self.assertEqual(self.ranked.state(Profile())["+0x98"][0], recorded)

    def test_party_ratings_carry_the_tier(self):
        # The capture's 1.68 party state has tier 2 next to rating 2333 (gold).
        (role_queue, *_) = self.ranked.party_ratings(Profile())
        self.assertEqual(role_queue["+0x18"], ROLE_QUEUE)
        self.assertEqual({(r["+0x10"], r["+0x16"], r["+0x18"]) for r in role_queue["+0x0"]}, {(2333, 2, 2)})

    def test_ratings_are_on_the_pc_platform_and_pool(self):
        # The client only finds a rating on the platform and pool it asks for; pool 0 locked every
        # competitive card.
        for card in self.ranked.state(Profile())["+0x80"]["+0x0"]:
            self.assertEqual({(r["+0x10"], r["+0x14"]) for r in card["+0x0"]}, {(PC_PLATFORM, PC_POOL)})
        for card in self.ranked.party_ratings(Profile()):
            self.assertEqual({(r["+0x8"], r["+0xC"]) for r in card["+0x0"]}, {(PC_PLATFORM, PC_POOL)})

    def test_ratings_come_from_the_profile(self):
        profile = Profile(ratings={"tank": 4100, "open": 1234})
        cards = {card["+0x18"]: card["+0x0"] for card in self.ranked.cards(profile)}
        role_queue = {r["+0x0"]: r for r in cards[ROLE_QUEUE]}
        tank = role_queue[TANK]
        self.assertEqual((tank["+0x18"], tank["+0x1A"], tank["+0x22"], tank["+0x23"]), (4100, 4100, 6, 6))
        self.assertEqual((tank["+0x1C"], tank["+0x40"], tank["+0x28"]), (0, False, [{"+0x0": 1}] * 5))
        self.assertEqual(sorted(r["+0x18"] for r in role_queue.values()), [2333, 2333, 4100])
        self.assertEqual([r["+0x18"] for r in cards[OPEN_QUEUE]], [1234])
        (party_role_queue, *_) = self.ranked.party_ratings(profile)
        tank = next(r for r in party_role_queue["+0x0"] if r["+0x0"] == TANK)
        self.assertEqual((tank["+0x10"], tank["+0x16"], tank["+0x17"]), (4100, 6, False))

    def test_a_top_500_place_goes_in_the_record(self):
        # With a place the client draws the Top 500 icon instead of the tier (0x7FF78934CFB0).
        ranked = Ranked(self.templates)
        ranked.places = lambda profile: {"tank": 7}
        (role_queue, *_) = ranked.cards(Profile())
        places = {r["+0x0"]: (r["+0x1C"], r["+0x1E"]) for r in role_queue["+0x0"]}
        self.assertEqual(places[TANK], (7, 7))
        self.assertEqual(sorted(places.values()), [(0, 0), (0, 0), (7, 7)])
        (party_role_queue, *_) = ranked.party_ratings(Profile())
        self.assertIn(7, [r["+0x12"] for r in party_role_queue["+0x0"]])

    def test_a_past_season_takes_the_running_place(self):
        # Season 25: role queue card 0x121, open queue card 0x151; later seasons leave the history.
        profile = Profile(season=25, ratings={"tank": 3100, "open": 2500})
        state = self.ranked.state(profile)
        self.assertEqual(state["+0x78"], 0x0630000000000121)
        running = [entry["+0x0"] for entry in state["+0xB0"]]
        self.assertEqual(running, [0x0630000000000121, 0x0630000000000151, 0x06300000000001C3])
        seasons = {entry["+0x118"] for entry in state["+0x98"]}
        self.assertIn(25, seasons)
        self.assertNotIn(26, seasons)
        self.assertNotIn(ROLE_QUEUE, state["+0xC8"])
        cards = {card["+0x18"]: card["+0x0"] for card in state["+0x80"]["+0x0"]}
        self.assertEqual({r["+0x0"]: r["+0x18"] for r in cards[0x0630000000000121]}[TANK], 3100)
        self.assertEqual([r["+0x18"] for r in cards[0x0630000000000151]], [2500])

    def test_placements_run_for_the_first_5_matches(self):
        # While placements run the record has no rating: the cards show +0x20 and one mark per
        # match played (+0x28), and the group check takes the placement rating.
        profile = Profile(matches={"tank": 3}, ratings={"tank": 2600})
        role_queue = {r["+0x0"]: r for r in self.ranked.cards(profile)[0]["+0x0"]}
        tank = role_queue[TANK]
        placing = (tank["+0x18"], tank["+0x20"], tank["+0x40"], tank["+0x28"])
        self.assertEqual(placing, (0, 2600, True, [{"+0x0": 1}] * 3))
        self.assertEqual(sorted(r["+0x40"] for r in role_queue.values()), [False, False, True])
        (party_role_queue, *_) = self.ranked.party_ratings(profile)
        tank = next(r for r in party_role_queue["+0x0"] if r["+0x0"] == TANK)
        self.assertEqual((tank["+0x10"], tank["+0x14"], tank["+0x17"], tank["+0x18"]), (0, 2600, True, 3))

    def test_seasons_before_role_queue_have_one_rating(self):
        cards = self.ranked.cards(Profile(season=10, ratings={"open": 3300}))
        self.assertEqual(
            [(card["+0x18"], [r["+0x18"] for r in card["+0x0"]]) for card in cards[:1]],
            [(0x06300000000000E6, [3300])],
        )

    def test_a_seen_season_intro_is_not_shown_again(self):
        # The client shows a card's season intro while +0x2C is off; closing it sends 36200.
        profile = Profile()
        self.assertEqual({card["+0x2C"] for card in self.ranked.cards(profile)}, {False})
        profile.seasons_seen = [ROLE_QUEUE]
        seen = {card["+0x18"]: card["+0x2C"] for card in self.ranked.cards(profile)}
        self.assertEqual(seen[ROLE_QUEUE], True)
        self.assertEqual(seen[OPEN_QUEUE], False)

    def test_an_event_card_runs_a_season_while_its_arcade_shows_it(self):
        ranked = Ranked(self.templates)
        ranked.event_cards = lambda profile: [LUCIO_CUP]  # the Summer Games
        profile = Profile(server_date="2026-09-29", ratings={"lucio": 3300})
        state = ranked.state(profile)
        self.assertEqual({e["+0x0"] for e in state["+0xB0"]}, {ROLE_QUEUE, OPEN_QUEUE, LUCIO_CUP})
        (season,) = [s for s in state["+0x98"] if s["+0x18"] == LUCIO_CUP]
        self.assertLess(from_stu_datetime(season["+0x20"]["+0x0"]), server_time(profile))
        self.assertGreater(from_stu_datetime(season["+0x30"]["+0x0"]), server_time(profile))
        cards = {card["+0x18"]: card["+0x0"] for card in state["+0x80"]["+0x0"]}
        self.assertEqual([r["+0x18"] for r in cards[LUCIO_CUP]], [3300])
        self.assertNotIn(COMPETITIVE_CTF, cards)

    def test_party_ratings_have_every_event_card(self):
        # Events are per profile here; a member without a rating greys the card out for the group.
        ranked = Ranked(self.templates)
        ranked.event_cards = lambda profile: []
        ratings = ranked.party_ratings(Profile(ratings={"lucio": 2800}))
        cards = {card["+0x18"]: card["+0x0"] for card in ratings}
        self.assertEqual(set(cards), {ROLE_QUEUE, OPEN_QUEUE, COMPETITIVE_CTF, LUCIO_CUP})
        self.assertEqual([(r["+0x0"], r["+0x10"]) for r in cards[LUCIO_CUP]], [(0, 2800)])

    def test_the_state_fits_the_174_schema(self):
        schemas = Schemas()
        state = self.ranked.state(Profile())
        self.assertEqual(schemas.decode(RANKED, 36300, schemas.encode(RANKED, 36300, state)), state)


if __name__ == "__main__":
    unittest.main()
