"""The lobby hands queue and Practice Range requests to the matchmaker."""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.accounts.profile import Profile
from ow174.jam.codec import Schemas
from ow174.lobby.handlers import build_router
from ow174.lobby.session import Session

MATCHMAKE = 0x1C6EC712
CUSTOM = 0xA6E53896


class LobbyMatchTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.schemas = Schemas()
        account = SimpleNamespace(name="Alpha", profile=Profile(player_name="Alpha"), account_lo=1)
        self.party = SimpleNamespace(queue=None, roles={}, members=[account])
        matchmaker = SimpleNamespace(
            search=lambda party, key, card: self.calls.append(("search", card)),
            cancel=lambda party: self.calls.append(("cancel",)),
            practice=lambda session: self.calls.append(("practice", session.account.name)),
        )
        server = SimpleNamespace(
            matchmaker=matchmaker,
            schemas=self.schemas,
            state_lock=threading.RLock(),
            router=build_router(),
            recorder=SimpleNamespace(record=lambda *args: None),
            social=SimpleNamespace(party_of=lambda account: self.party),
            notify_party=lambda party: None,
            content=SimpleNamespace(arcade=SimpleNamespace(has_roles=lambda card: False)),
            session_of=lambda account_lo: self.session,
        )
        self.session = Session(server, None, None, 5)
        self.session.account = account
        self.session.logged_in = True
        self.session.log = lambda *args: None
        self.session.send = lambda *args: True
        self.session.announce([MATCHMAKE, CUSTOM])

    def test_the_practice_range_request_starts_a_practice_match(self):
        self.session.dispatch(2, 0, bytes.fromhex("020004000000"))
        self.assertEqual(self.calls, [("practice", "Alpha")])

    def test_a_queue_without_roles_searches_at_once_and_cancel_stops_it(self):
        # A captured Mystery Heroes request (card 0x0630000000000002); its cancel has the same body.
        body = bytes.fromhex(
            "0200000000003006000000000000000000000000000000000000000000000000000000004086f3004486f400"
        )
        self.session.dispatch(1, 0, body)
        self.session.dispatch(1, 2, body)
        self.assertEqual(self.calls, [("search", 0x0630000000000002), ("cancel",)])


if __name__ == "__main__":
    unittest.main()
