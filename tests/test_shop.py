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

FUSION = 0x02500000000013C3
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
        self.assertEqual(receipt, {"guid": "0x02500000000013C3", "price": 100, "currency": "league_tokens"})
        self.assertEqual(self.balances(), (2000, 6000, 400))
        self.assertTrue(self.collection.owns(self.profile, FUSION))

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
        self.assertEqual(self.shop.catalog(self.profile, q="Blackwatch Reyes")["total"], 0)

    def test_unknown_and_malformed_guids_do_not_mutate_profile(self):
        for guid in ("garbage", "", None, True, 1.5, -1, 0, 0x025000000000FFFF):
            with self.subTest(guid=guid):
                before = asdict(self.profile)
                with self.assertRaises(ShopError) as raised:
                    self.shop.purchase(self.profile, guid)
                self.assertEqual(raised.exception.status, 400)
                self.assertEqual(asdict(self.profile), before)

    def test_catalog_filters_and_reports_real_currency_and_price(self):
        catalog = self.shop.catalog(
            self.profile, q="Philadelphia Fusion", hero="reaper", currency="league_tokens"
        )
        item = next(item for item in catalog["items"] if item["guid"] == "0x02500000000013C3")
        self.assertEqual(
            item,
            {
                "guid": "0x02500000000013C3",
                "name": "Philadelphia Fusion",
                "hero": "Reaper",
                "type": "Skin",
                "rarity": "Epic",
                "price": 100,
                "currency": "league_tokens",
                "owned": False,
                "purchasable": True,
            },
        )
        credits = self.shop.catalog(self.profile, q="Philadelphia Fusion", hero="Reaper", currency="credits")
        self.assertEqual(credits["total"], 0)
        golden = self.shop.catalog(self.profile, q="GOLDEN", hero="Reaper", currency="comp_points")
        self.assertEqual(golden["total"], 1)
        self.assertEqual(golden["items"][0]["guid"], "0x02500000000003C9")

    def test_catalog_pagination_is_stable_and_has_no_overlap(self):
        first = self.shop.catalog(self.profile, hero="Reaper", page=1, page_size=5)
        second = self.shop.catalog(self.profile, hero="Reaper", page=2, page_size=5)
        again = self.shop.catalog(self.profile, hero="Reaper", page=1, page_size=5)
        self.assertEqual(first, again)
        self.assertEqual(len(first["items"]), 5)
        self.assertEqual(len(second["items"]), 5)
        self.assertGreater(first["total"], 10)
        self.assertEqual(first["pages"], (first["total"] + 4) // 5)
        self.assertEqual((second["page"], second["page_size"]), (2, 5))
        first_guids = {item["guid"] for item in first["items"]}
        self.assertFalse(first_guids & {item["guid"] for item in second["items"]})

    def test_owned_catalog_items_are_not_purchasable(self):
        self.profile.unlocked_items = ["0x02500000000013C3"]
        catalog = self.shop.catalog(self.profile, q="Philadelphia Fusion", hero="Reaper")
        item = next(item for item in catalog["items"] if item["guid"] == "0x02500000000013C3")
        self.assertTrue(item["owned"])
        self.assertFalse(item["purchasable"])
        self.profile.unlock_all = True
        items = self.shop.catalog(self.profile)["items"]
        self.assertTrue(all(item["owned"] and not item["purchasable"] for item in items))


if __name__ == "__main__":
    unittest.main()
