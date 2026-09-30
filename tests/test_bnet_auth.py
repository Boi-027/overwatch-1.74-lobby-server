"""Battle.net logon: the client logs in at once, with no login form, as the account its connection
belongs to."""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.bnet import protocol as P
from ow174.bnet.rpc_server import BNetRpcServer, Player


class FakeSession:
    def __init__(self):
        self.peer = ("127.0.0.1", 50123)
        self.logs, self.notifications = [], []

    def log(self, text):
        self.logs.append(text)

    async def send_response(self, header, body):
        pass

    async def send_notification(self, service_hash, method_id, body):
        self.notifications.append((service_hash, method_id, body))


class LogonTests(unittest.TestCase):
    def test_logon_completes_at_once_as_the_connections_account(self):
        header = P.Header()
        header.method_id = P.LOGON
        session = FakeSession()
        peers = []

        def player(peer):
            peers.append(peer)
            return Player(0x15FF2EDE, 0x1EF42EDE, "Researcher#1214")

        server = BNetRpcServer(player=player)
        asyncio.run(server._auth(session, header, P.LogonRequest().SerializeToString()))
        self.assertEqual(peers, [session.peer])
        ((service, method, body),) = session.notifications
        self.assertEqual((service, method), (P.AUTH_CLIENT_HASH, P.ON_LOGON_COMPLETE))
        result = P.LogonResult.FromString(body)
        self.assertEqual((result.account_id.low, result.battle_tag), (0x15FF2EDE, "Researcher#1214"))
        self.assertEqual(result.game_account_id[0].low, 0x1EF42EDE)


if __name__ == "__main__":
    unittest.main()
