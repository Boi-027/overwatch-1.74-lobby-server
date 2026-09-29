import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content, Identity
from ow174.content.menu_hero import MENU_HERO_KEY, PVE_NPC_BASE, PVE_NPCS, pve_character
from ow174.jam.codec import Schemas
from ow174.jam.groups import CONFIG


class MenuNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = Content(Schemas(), RetailTemplates(), ItemDB())

    def config_entries(self, profile):
        messages = self.content.retail.at_login(profile, Identity.create(123, 1))
        return next(v for c, m, v in messages if (c, m) == (CONFIG, 36600))["+0x78"]

    def test_npc_choice_adds_the_menu_hero_override(self):
        entries = self.config_entries(Profile(lobby_hero="Talon Sniper"))
        override = [e for e in entries if e["+0x0"] == MENU_HERO_KEY]
        self.assertEqual(override, [{"+0x0": MENU_HERO_KEY, "+0x8": "0x02e00000000001b8"}])

    def test_regular_hero_and_random_add_no_override(self):
        for choice in ("random", "none", "Genji"):
            entries = self.config_entries(Profile(lobby_hero=choice))
            self.assertFalse([e for e in entries if e["+0x0"] == MENU_HERO_KEY], choice)

    def test_menu_npc_is_case_insensitive_and_unknown_is_none(self):
        self.assertEqual(pve_character(Profile(lobby_hero="b.o.b.")), PVE_NPC_BASE | 0x21D)
        self.assertIsNone(pve_character(Profile(lobby_hero="Nobody")))

    def test_every_npc_guid_is_in_the_pve_range(self):
        for name, ident in PVE_NPCS.items():
            self.assertEqual((PVE_NPC_BASE | ident) >> 48, 0x02E0, name)

    def test_live_messages_carry_the_menu_override_so_dashboard_changes_apply_at_once(self):
        ident = Identity.create(123, 1)
        profile = Profile(lobby_hero="Talon Trooper")
        messages = self.content.live_messages(profile, ident)
        config = next(v for c, m, v in messages if (c, m) == (CONFIG, 36600))
        self.assertIn({"+0x0": MENU_HERO_KEY, "+0x8": "0x02e00000000001ac"}, config["+0x78"])
        profile.lobby_hero = "random"
        messages = self.content.live_messages(profile, ident)
        config = next(v for c, m, v in messages if (c, m) == (CONFIG, 36600))
        self.assertFalse([e for e in config["+0x78"] if e["+0x0"] == MENU_HERO_KEY])

    def test_party_state_still_uses_a_real_hero_for_an_npc_choice(self):
        hero = self.content.menu_hero.choose(Profile(lobby_hero="Talon Sniper"))
        self.assertIn(hero, self.content.collection.default_loadouts)


if __name__ == "__main__":
    unittest.main()
