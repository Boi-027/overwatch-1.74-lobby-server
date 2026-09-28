from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from jam_codec import Schemas
from lobbyserv import LobbySession
from match_runtime import MatchManager
from storage import Profile

QUEUE = 0x1C6EC712
CUSTOM = 0xA6E53896


class LobbyMatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.matches = MatchManager(Path(self.tmp.name), base_port=0)
        self.addCleanup(self.matches.close)
        self.schemas = Schemas()
        server = SimpleNamespace(matches=self.matches, schemas=self.schemas, state_lock=threading.RLock())
        self.session = LobbySession(server, None, None, 5, 1)
        self.session.account = SimpleNamespace(name='Alpha', profile=Profile(player_name='Alpha'))
        self.session.logged_in = True
        self.session.log = lambda text: None
        self.session._record = lambda *args: None
        self.session.announce([QUEUE, CUSTOM])

    def test_captured_practice_request_allocates_real_server(self):
        self.session.dispatch(2, 0, bytes.fromhex('020004000000'))
        states = self.matches.snapshot()
        self.assertEqual(len(states), 1, '24000 must reach the instance allocator')
        self.assertEqual(states[0]['player'], 'Alpha')
        self.assertEqual(states[0]['state'], 'listening')

    def test_search_and_cancel_messages_start_then_stop_worker(self):
        # Captured Mystery Heroes request and cancellation have identical bodies.
        body = bytes.fromhex('0200000000003006000000000000000000000000000000000000000000000000000000004086f3004486f400')
        self.session.dispatch(1, 0, body)
        self.assertEqual(len(self.matches.snapshot()), 1)
        self.session.dispatch(1, 2, body)
        self.assertEqual(self.matches.snapshot(), [])


if __name__ == '__main__':
    unittest.main()
