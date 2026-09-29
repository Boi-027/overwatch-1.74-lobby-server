"""Shop regressions using the captured catalog and extracted unlock metadata."""

import sys
import unittest
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content.collection import Collection
from ow174.services.shop import ShopError, ShopService

FUSION = 0x02500000000013C3  # Reaper's Philadelphia Fusion skin
BLOOD = 0x02500000000003F8
GOLDEN = 0x02500000000003C9
ORIGINS = 0x0250000000000405


class ShopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = ItemDB()
        cls.collection = Collection(RetailTemplates(), cls.items)

    def setUp(self):
        self.shop = ShopService(self.collection, self.items)
        self.profile = Profile(credits=2000, comp_points=6000, league_tokens=500)

    def balances(self):
        return self.profile.credits, self.profile.comp_points, self.profile.league_tokens

    def test_owl_purchase_spends_tokens_and_grants_ownership(self):
        receipt = self.shop.purchase(self.profile, FUSION)
        away = self.items.team_skin_pair(FUSION)
        expected = {"guid": "0x02500000000013C3", "price": 100, "currency": "league_tokens"}
        self.assertEqual(receipt, {**expected, "also": [f"0x{away:016X}"]})
        self.assertEqual(self.balances(), (2000, 6000, 400))
        self.assertTrue(self.collection.owns(self.profile, FUSION))

    def test_a_team_skin_comes_with_its_home_or_away_partner(self):
        away = self.items.team_skin_pair(FUSION)
        self.assertEqual(self.items.get(away).name, "Philadelphia Fusion Away")
        self.assertEqual(self.items.team_skin_pair(away), FUSION)
        self.shop.purchase(self.profile, away)
        self.assertTrue(self.collection.owns(self.profile, FUSION))
        with self.assertRaises(ShopError):
            self.shop.purchase(self.profile, FUSION)
        self.assertEqual(self.profile.league_tokens, 400)

    def test_every_team_skin_has_a_partner_of_the_same_hero(self):
        unlocks = self.items.unlocks.values()
        team_skins = [u for u in unlocks if u.available_in.startswith("Unlocking includes both")]
        self.assertEqual(len(team_skins), 1528)
        for unlock in team_skins:
            pair = self.items.get(self.items.team_skin_pair(unlock.guid))
            self.assertEqual((pair.hero, pair.esports_team), (unlock.hero, unlock.esports_team), unlock.name)
        gray = next(u for u in team_skins if u.hero == "D.Va" and u.name == "Overwatch League Gray")
        self.assertEqual(self.items.get(self.items.team_skin_pair(gray.guid)).name, "Overwatch League White")

    def test_older_purchases_get_their_partners(self):
        self.profile.unlocked_items = ["0x02500000000013C3", "0x02500000000003F8"]
        away = self.items.team_skin_pair(FUSION)
        self.assertEqual(self.shop.add_missing_pairs(self.profile), [away])
        self.assertIn(f"0x{away:016X}", self.profile.unlocked_items)
        self.assertEqual(self.shop.add_missing_pairs(self.profile), [])

    def test_credit_purchase_uses_captured_price(self):
        receipt = self.shop.purchase(self.profile, "0x02500000000003F8")
        self.assertEqual(receipt["price"], 75)
        self.assertEqual(receipt["currency"], "credits")
        self.assertEqual(self.balances(), (1925, 6000, 500))
        self.assertTrue(self.collection.owns(self.profile, BLOOD))

    def test_golden_weapon_spends_competitive_points(self):
        receipt = self.shop.purchase(self.profile, GOLDEN)
        self.assertEqual(receipt["price"], 3000)
        self.assertEqual(receipt["currency"], "comp_points")
        self.assertEqual(self.balances(), (2000, 3000, 500))
        self.assertTrue(self.collection.owns(self.profile, GOLDEN))

    def test_insufficient_tokens_cannot_spend_credits_instead(self):
        self.profile.league_tokens = 99
        before = asdict(self.profile)
        with self.assertRaises(ShopError) as raised:
            self.shop.purchase(self.profile, FUSION)
        self.assertEqual((raised.exception.code, raised.exception.status), ("insufficient_balance", 409))
        self.assertEqual(asdict(self.profile), before)

    def test_exact_balance_can_be_spent(self):
        self.profile.league_tokens = 100
        self.shop.purchase(self.profile, FUSION)
        self.assertEqual(self.profile.league_tokens, 0)

    def test_duplicate_purchase_does_not_mutate_profile(self):
        self.profile.unlocked_items = ["0x02500000000013c3"]
        before = asdict(self.profile)
        with self.assertRaises(ShopError) as raised:
            self.shop.purchase(self.profile, FUSION)
        self.assertEqual((raised.exception.code, raised.exception.status), ("already_owned", 409))
        self.assertEqual(asdict(self.profile), before)

    def test_unlock_all_blocks_charging_for_owned_items(self):
        self.profile.unlock_all = True
        before = asdict(self.profile)
        with self.assertRaises(ShopError) as raised:
            self.shop.purchase(self.profile, BLOOD)
        self.assertEqual(raised.exception.code, "already_owned")
        self.assertEqual(asdict(self.profile), before)

    def test_unpriced_edition_reward_is_not_a_product(self):
        before = asdict(self.profile)
        with self.assertRaises(ShopError) as raised:
            self.shop.purchase(self.profile, ORIGINS)
        self.assertEqual((raised.exception.code, raised.exception.status), ("not_purchasable", 400))
        self.assertEqual(asdict(self.profile), before)
        self.assertIsNone(self.shop.product(ORIGINS))

    def test_unknown_and_malformed_guids_do_not_mutate_profile(self):
        for guid in ("garbage", "", None, True, 1.5, -1, 0, 0x025000000000FFFF):
            with self.subTest(guid=guid):
                before = asdict(self.profile)
                with self.assertRaises(ShopError) as raised:
                    self.shop.purchase(self.profile, guid)
                self.assertEqual(raised.exception.status, 400)
                self.assertEqual(asdict(self.profile), before)

    def test_products_carry_the_real_currency_and_price(self):
        expected = {
            "guid": "0x02500000000013C3",
            "name": "Philadelphia Fusion",
            "hero": "Reaper",
            "type": "Skin",
            "rarity": "Epic",
            "price": 100,
            "currency": "league_tokens",
        }
        self.assertEqual(self.shop.product(FUSION), expected)
        self.assertEqual(self.shop.product(GOLDEN)["currency"], "comp_points")
        self.assertEqual(self.shop.product(BLOOD)["currency"], "credits")


if __name__ == "__main__":
    unittest.main()
