"""Opening loot boxes."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content.collection import Collection
from ow174.services.lootbox import LootBoxEngine, _roll_slot_rarities

HALLOWEEN = 2
WRECKING_BALL = 10
LEGENDARY = 12


class LootBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = ItemDB()
        cls.collection = Collection(RetailTemplates(), cls.items)
        cls.engine = LootBoxEngine(cls.collection, cls.items)

    def open_box(self, profile, box_type=HALLOWEEN):
        profile.loot_boxes = [{"id": 1, "type": box_type, "name": "box"}] * 10
        return self.engine.open(1, profile)

    def test_unlock_all_gives_only_duplicates(self):
        opening = self.open_box(Profile(unlock_all=True))
        self.assertTrue(all(drop["duplicate"] for drop in opening.drops if "amount" not in drop))

    def test_ownership_is_worked_out_once_per_box(self):
        # Asking the collection for every candidate made one box take seconds.
        with patch.object(self.collection, "owns", side_effect=AssertionError("slow path")):
            self.open_box(Profile())

    def test_a_dropped_item_is_not_dropped_as_new_again(self):
        profile = Profile()
        seen = set()
        for _ in range(30):
            for drop in self.open_box(profile).drops:
                if drop["new"]:
                    self.assertNotIn(drop["unlock"], seen)
                    seen.add(drop["unlock"])

    def test_an_event_box_always_has_an_event_item(self):
        for _ in range(20):
            opening = self.open_box(Profile())
            self.assertTrue(any(drop["highlight"] == HALLOWEEN for drop in opening.drops))

    def test_credits_drop_in_place_of_an_item_of_their_rarity(self):
        profile = Profile(credits=0)
        rolled = ["Legendary", "Rare", "Epic", "Common"]
        with (
            patch("ow174.services.lootbox._roll_slot_rarities", return_value=list(rolled)),
            patch.dict("ow174.services.lootbox.CREDIT_SHARE", {"Rare": 1.0, "Epic": 1.0, "Legendary": 1.0}),
        ):
            opening = self.open_box(profile, box_type=0)
        self.assertEqual([drop.get("amount") for drop in opening.drops], [500, 50, 150, None])
        guids = {0x025000000000088B, 0x025000000000088C, 0x025000000000088D}
        credit_drops = opening.drops[:3]
        self.assertTrue(all(drop["unlock"] in guids and not drop["new"] for drop in credit_drops))
        dup_credits = sum(drop["credits"] for drop in opening.drops)
        self.assertEqual(profile.credits, dup_credits + 700)
        self.assertFalse(any(f"0x{guid:016X}" in profile.unlocked_items for guid in guids))

    def test_a_legendary_box_keeps_its_legendary_item(self):
        with patch.dict("ow174.services.lootbox.CREDIT_SHARE", {"Rare": 1.0, "Epic": 1.0, "Legendary": 1.0}):
            for _ in range(20):
                drops = self.open_box(Profile(), box_type=LEGENDARY).drops
                self.assertTrue(any(d["rarity"] == "Legendary" and "amount" not in d for d in drops))

    def test_a_wrecking_ball_box_holds_his_items_and_credits(self):
        types = set()
        profile = Profile()
        for _ in range(60):
            for drop in self.open_box(profile, box_type=WRECKING_BALL).drops:
                if "amount" not in drop:
                    unlock = self.items.get(drop["unlock"])
                    self.assertEqual(unlock.hero, "Wrecking Ball")
                    types.add(unlock.type)
        self.assertIn("Icon", types)
        rolled = ["Legendary", "Common", "Common", "Common"]
        with (
            patch("ow174.services.lootbox._roll_slot_rarities", return_value=rolled),
            patch.dict("ow174.services.lootbox.CREDIT_SHARE", {"Legendary": 1.0}),
        ):
            drops = self.open_box(profile, box_type=WRECKING_BALL).drops
        self.assertEqual(drops[0].get("amount"), 500)

    def test_icons_drop_although_they_have_no_price(self):
        icons = 0
        for _ in range(40):
            for drop in self.open_box(Profile(), box_type=0).drops:
                unlock = self.items.get(drop["unlock"])
                icons += "amount" not in drop and unlock.type == "Icon"
        self.assertGreater(icons, 0)

    def test_rarity_odds_per_box_follow_the_game_odds_screen(self):
        boxes = 40000
        seen = {"Common": 0, "Rare": 0, "Epic": 0, "Legendary": 0}
        for _ in range(boxes):
            for rarity in set(_roll_slot_rarities(always_legendary=False)):
                seen[rarity] += 1
        odds = {rarity: count / boxes for rarity, count in seen.items()}
        for rarity, expected in (("Common", 0.99), ("Rare", 0.95), ("Epic", 0.185), ("Legendary", 0.075)):
            self.assertAlmostEqual(odds[rarity], expected, delta=0.012, msg=rarity)

    def test_open_all_empties_the_stock_without_a_refill(self):
        profile = Profile()
        profile.loot_boxes = [{"id": number, "type": 0, "name": "Standard"} for number in range(1, 6)]
        opened, granted = self.engine.open_all(profile)
        self.assertEqual(opened, 5)
        self.assertEqual(profile.loot_boxes, [])
        self.assertEqual(len(set(granted)), len(granted))
        self.assertEqual(profile.stats["boxes_opened"], 5)

    def test_event_epic_and_legendary_skins_can_drop(self):
        rarities = set()
        profile = Profile()
        for _ in range(400):
            for drop in self.open_box(profile).drops:
                unlock = self.items.get(drop["unlock"])
                if drop["highlight"] and unlock.type == "Skin":
                    rarities.add(unlock.rarity)
        self.assertLessEqual({"Epic", "Legendary"}, rarities)


if __name__ == "__main__":
    unittest.main()
