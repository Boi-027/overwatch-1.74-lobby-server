import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile, save_profile
from ow174.accounts.registry import Account
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content, Identity
from ow174.jam.codec import Schemas
from ow174.jam.groups import PROGRESSION_IN
from ow174.lobby.handlers.gallery import purchase
from ow174.lobby.session import Session
from ow174.services.shop import ShopService


class PurchasePacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = ItemDB()
        cls.schemas = Schemas()
        cls.content = Content(cls.schemas, RetailTemplates(), cls.items)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        profile = Profile(credits=2000, comp_points=6000, league_tokens=500)
        self.account = Account("PurchaseProbe", 123, profile, Path(self.tmp.name) / "profile.json")
        save_profile(self.account.profile, self.account.path)
        self.session = object.__new__(Session)
        self.session.account = self.account
        self.session.ident = Identity.create(123, 1)
        self.session.server = SimpleNamespace(
            content=self.content,
            items=self.items,
            shop=ShopService(self.content.collection, self.items),
            state_lock=threading.RLock(),
        )
        self.sent = []
        self.session.send = lambda crc, mid, value: self.sent.append((crc, mid, value)) or True
        self.session.log = lambda text, level=None: None

    def test_owl_purchase_pushes_new_token_balance_in_real_progression_schema(self):
        purchase(self.session, 0x02500000000013C3)
        self.assertEqual(self.account.profile.credits, 2000)
        self.assertEqual(self.account.profile.league_tokens, 400)
        full = next((v for c, m, v in self.sent if (c, m) == (PROGRESSION_IN, 24300)), None)
        self.assertIsNotNone(full, "Client must receive updated wallet, not only a granted skin")
        decoded = self.schemas.decode(PROGRESSION_IN, 24300, self.schemas.encode(PROGRESSION_IN, 24300, full))
        self.assertEqual(decoded["+0x78"]["+0x6C"], 2000)
        self.assertEqual(decoded["+0x78"]["+0x74"], 400)

    def test_a_purchase_unlocks_the_item_unlock_first(self):
        purchase(self.session, 0x02500000000013C3)
        (unlock,) = [v for c, m, v in self.sent if (c, m) == (PROGRESSION_IN, 24306)]
        self.assertEqual(unlock["+0x78"], 0x02500000000013C3)
        self.assertEqual(unlock["+0x80"], self.content.collection.hero_of[0x02500000000013C3])

    def test_a_team_skin_purchase_also_unlocks_its_partner(self):
        purchase(self.session, 0x02500000000013C3)
        away = self.items.team_skin_pair(0x02500000000013C3)
        (granted,) = [v for c, m, v in self.sent if m == 24901]
        self.assertEqual(granted["+0x80"], away)
        self.assertTrue(self.content.collection.owns(self.account.profile, away))

    def test_rejected_purchase_does_not_change_disk_or_emit_unlock(self):
        self.account.profile.league_tokens = 0
        save_profile(self.account.profile, self.account.path)
        before = self.account.path.read_bytes()
        purchase(self.session, 0x02500000000013C3)
        self.assertEqual(self.account.path.read_bytes(), before)
        self.assertFalse(self.content.collection.owns(self.account.profile, 0x02500000000013C3))
        self.assertFalse(any(m in (24306, 24901) for c, m, v in self.sent))


if __name__ == "__main__":
    unittest.main()
