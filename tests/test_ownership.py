import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.jam.codec import Schemas

AURISA = 0x250000000001149  # anniversary icon tagged with hero Orisa
PACHIMARI = 0x2500000000008E9  # icon with no hero tag


class OwnershipRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = ItemDB()
        cls.collection = Content(Schemas(), RetailTemplates(), cls.items).collection

    def grant(self, *guids, unlock_all=False):
        return Profile(unlocked_items=[hex(g) for g in guids], unlock_all=unlock_all)

    def first_by_type(self, kind):
        return next(
            g
            for g, u in sorted(self.items.unlocks.items())
            if u.type == kind and g in self.collection.hero_of
        )

    # Tested in game: the client reads a hero-tagged icon's ownership from its hero's list (only
    # there it is unlocked), and does not send the equip request when the account list has it too.
    def test_hero_tagged_icon_is_listed_only_under_its_hero(self):
        profile = self.grant(AURISA)
        hero = self.collection.hero_of[AURISA]
        self.assertIn(AURISA, self.collection.owned_for_hero(profile, hero))
        self.assertNotIn(AURISA, self.collection.owned_account(profile))
        self.assertTrue(self.collection.owns(profile, AURISA))

    def test_untagged_icon_still_account_level(self):
        self.assertIn(PACHIMARI, self.collection.owned_account(self.grant(PACHIMARI)))

    def test_hero_tagged_spray_is_listed_only_under_its_hero(self):
        spray = self.first_by_type("Spray")
        profile = self.grant(spray)
        self.assertIn(spray, self.collection.owned_for_hero(profile, self.collection.hero_of[spray]))
        self.assertNotIn(spray, self.collection.owned_account(profile))

    def test_unlock_all_lists_no_hero_tagged_item_under_the_account(self):
        profile = self.grant(unlock_all=True)
        owned = self.collection.owned_account(profile)
        self.assertFalse(set(owned) & set(self.collection.hero_of))
        self.assertIn(PACHIMARI, owned)
        self.assertIn(AURISA, self.collection.owned_for_hero(profile, self.collection.hero_of[AURISA]))

    def test_hero_skin_stays_with_its_hero(self):
        skin = self.first_by_type("Skin")
        profile = self.grant(skin)
        self.assertIn(skin, self.collection.owned_for_hero(profile, self.collection.hero_of[skin]))
        self.assertNotIn(skin, self.collection.owned_account(profile))
        self.assertTrue(self.collection.owns(profile, skin))

    def test_unlock_all_account_list_has_no_duplicates(self):
        owned = self.collection.owned_account(self.grant(unlock_all=True))
        self.assertEqual(len(owned), len(set(owned)))

    def test_equipping_a_hero_gallery_icon_sets_the_account_icon(self):
        hero = self.collection.hero_of[AURISA]
        profile = Profile()
        self.assertTrue(self.collection.equip(profile, hero, AURISA, 0))
        self.assertEqual(profile.icon_guid, AURISA)
        self.assertTrue(self.collection.equip(profile, 0, PACHIMARI, 0))
        self.assertEqual(profile.icon_guid, PACHIMARI)

    def test_non_icon_item_with_no_hero_is_still_rejected(self):
        skin = self.first_by_type("Skin")
        self.assertFalse(self.collection.equip(Profile(), 0, skin, 0))

    def test_not_granted_icon_is_not_owned(self):
        self.assertFalse(self.collection.owns(Profile(), AURISA))

    def test_items_added_after_the_capture_belong_to_their_hero(self):
        # Luchador and Dusk (Reaper) and Happi (Genji) came out after the 1.68 capture. Without a hero
        # the client never counted them as owned and hid them from the hero gallery.
        reaper = self.items.hero_by_name("Reaper")
        profile = self.grant(unlock_all=True)
        for guid in (0x0250000000004F53, 0x0250000000004DC7):
            self.assertEqual(self.collection.hero_of.get(guid), reaper, hex(guid))
            self.assertIn(guid, self.collection.owned_for_hero(profile, reaper))
        missing = [
            unlock.name
            for unlock in self.items.unlocks.values()
            if unlock.hero and unlock.name and unlock.guid not in self.collection.hero_of
        ]
        self.assertEqual(missing, [])

    def test_items_added_after_the_capture_are_in_their_hero_store(self):
        # The hero gallery only shows items from the hero's store list in 24900.
        catalog = self.collection.hero_catalog(self.grant(unlock_all=True))
        reaper = self.items.hero_by_name("Reaper")
        (store,) = [store for store in catalog["+0x98"] if store["+0x18"] == reaper]
        entries = {entry["+0x0"]: entry for entry in store["+0x0"]}
        luchador = entries[0x0250000000004F53]
        self.assertEqual((luchador["+0x14"], luchador["+0x16"]), (0, -1))  # no price, never in boxes
        # Tested in game: with the default items' flags (+0x10 0, +0x17 true) the hero's count stayed
        # at 0 with Luchador owned. Contenders Away (not sold either) counts.
        (contenders,) = [
            entries[u.guid]
            for u in self.items.unlocks.values()
            if (u.hero, u.name) == ("Reaper", "Contenders Away")
        ]
        self.assertEqual((luchador["+0x10"], luchador["+0x17"]), (contenders["+0x10"], contenders["+0x17"]))


if __name__ == "__main__":
    unittest.main()
