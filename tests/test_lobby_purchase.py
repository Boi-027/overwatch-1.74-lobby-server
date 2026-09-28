from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from lobbyserv import LobbySession
from content import LobbyContent, Identity, PROGRESSION_IN
from items import ItemDB
from jam_codec import Schemas
from retail import RetailCapture
from shop import ShopService
from social import Account
from storage import Profile, save_profile


class PurchasePacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = ItemDB()
        cls.schemas = Schemas()
        cls.content = LobbyContent(cls.schemas, RetailCapture(cls.schemas), cls.items)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.account = Account('PurchaseProbe', 123, Profile(credits=2000, comp_points=6000, league_tokens=500),
                               Path(self.tmp.name) / 'profile.json')
        save_profile(self.account.profile, self.account.path)
        self.session = object.__new__(LobbySession)
        self.session.account = self.account
        self.session.ident = Identity.create(123, 1)
        self.session.srv = SimpleNamespace(content=self.content, items=self.items,
                    shop=ShopService(self.content, self.items), state_lock=threading.RLock())
        self.sent = []
        self.session.send = lambda crc, mid, value: self.sent.append((crc, mid, value)) or True
        self.session.log = lambda text: None

    def test_owl_purchase_pushes_new_token_balance_in_real_progression_schema(self):
        self.session.purchase(0x02500000000013C3)
        self.assertEqual(self.account.profile.credits, 2000)
        self.assertEqual(self.account.profile.league_tokens, 400)
        full = next((v for c, m, v in self.sent if (c, m) == (PROGRESSION_IN, 24300)), None)
        self.assertIsNotNone(full, 'Client must receive updated wallet, not only a granted skin')
        decoded = self.schemas.decode(PROGRESSION_IN, 24300, self.schemas.encode(PROGRESSION_IN, 24300, full))
        self.assertEqual(decoded['+0x78']['+0x6C'], 2000)
        self.assertEqual(decoded['+0x78']['+0x74'], 400)

    def test_rejected_purchase_does_not_change_disk_or_emit_unlock(self):
        self.account.profile.league_tokens = 0
        save_profile(self.account.profile, self.account.path)
        before = self.account.path.read_bytes()
        self.session.purchase(0x02500000000013C3)
        self.assertEqual(self.account.path.read_bytes(), before)
        self.assertFalse(self.content.owns(self.account.profile, 0x02500000000013C3))
        self.assertFalse(any(m == 24901 for c, m, v in self.sent))


if __name__ == '__main__':
    unittest.main()
