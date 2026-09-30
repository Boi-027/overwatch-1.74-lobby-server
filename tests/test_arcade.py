"""The Arcade follows the active events: their card groups first, then the everyday cards."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile
from ow174.catalog.templates import RetailTemplates
from ow174.content.arcade import CARD_BASE, LUNAR_BRAWLS, WINTER_BRAWLS, Arcade
from ow174.content.ranked import COMPETITIVE_CTF, LUCIO_CUP, Ranked
from ow174.jam.groups import ARCADE


class ArcadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = RetailTemplates()
        cls.arcade = Arcade(cls.templates, Ranked(cls.templates))

    def test_lunar_new_year_gives_the_retail_arcade(self):
        # The capture is from Lunar New Year 2022, so the cards built from the client's data must
        # match the recorded ones field for field.
        built = self.arcade.messages(Profile(events=["lunar"]), 0)
        recorded = self.templates.first(ARCADE, 39802)
        self.assertEqual(built[(ARCADE, 39802)], recorded)
        self.assertEqual(built[(ARCADE, 39804)], self.templates.first(ARCADE, 39804))

    def test_winter_brings_its_brawls_and_no_lunar_ones(self):
        cards = self.arcade.cards(Profile(events=["winter"]), 0)
        self.assertEqual(cards[: len(WINTER_BRAWLS)], list(WINTER_BRAWLS))
        self.assertFalse(set(LUNAR_BRAWLS) & set(cards))

    def test_without_an_event_the_everyday_cards_fill_the_slots(self):
        # Seven slots as retail had them: two fixed daily cards, three that change with the date,
        # then Quick Play Classic and Mystery Heroes.
        today = self.arcade.cards(Profile(events=["goodbye"]), 0)
        self.assertEqual(today, [0x15C, 0x34, 0x1C, 0x64, 0xAD, 0xEF, 0x2])
        tomorrow = self.arcade.cards(Profile(events=["goodbye"]), 86400)
        self.assertEqual(tomorrow[2:5], [0x64, 0xAD, 0x1])

    def test_the_events_competitive_cards_are_game_types_of_the_group_finder(self):
        self.assertEqual(self.arcade.competitive_cards(Profile(events=["summer"]), 0), [LUCIO_CUP])
        self.assertEqual(self.arcade.competitive_cards(Profile(events=["lunar"]), 0), [COMPETITIVE_CTF])
        self.assertEqual(self.arcade.competitive_cards(Profile(events=["goodbye"]), 0), [])

    def test_every_card_has_a_date_window(self):
        built = self.arcade.messages(Profile(events=["anniversary"]), 1000 * 86400)
        windows = {w["+0x0"] for w in built[(ARCADE, 39810)]["+0x78"]}
        cards = {c["+0x0"] for c in built[(ARCADE, 39802)]["+0x78"]["+0x0"]}
        self.assertEqual(windows, cards)
        self.assertTrue(all(card & CARD_BASE == CARD_BASE for card in cards))


if __name__ == "__main__":
    unittest.main()
