"""Battle.net logon: the client logs in at once, with no login form, as the account its connection
belongs to, and goes on to the lobby the emulator names."""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.bnet import protocol as P
from ow174.bnet.rpc_server import BNetRpcServer, Player
from ow174.bnet.session_key import name_in_key, session_key


class FakeSession:
    def __init__(self):
        self.peer = ("127.0.0.1", 50123)
        self.logs, self.notifications, self.responses = [], [], []

    def log(self, text):
        self.logs.append(text)

    async def send_response(self, header, body):
        self.responses.append(body)

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
            return Player(0x15FF2EDE, 0x1EF42EDE, "Researcher#1214", "Researcher")

        server = BNetRpcServer(player=player)
        asyncio.run(server._auth(session, header, P.LogonRequest().SerializeToString()))
        self.assertEqual(peers, [session.peer])
        ((service, method, body),) = session.notifications
        self.assertEqual((service, method), (P.AUTH_CLIENT_HASH, P.ON_LOGON_COMPLETE))
        result = P.LogonResult.FromString(body)
        self.assertEqual((result.account_id.low, result.battle_tag), (0x15FF2EDE, "Researcher#1214"))
        self.assertEqual(result.game_account_id[0].low, 0x1EF42EDE)
        # The client passes the key on to the lobby, which reads the account name from it.
        self.assertEqual(len(result.session_key), 64)
        self.assertEqual(name_in_key(result.session_key), "Researcher")

    def test_the_referral_sends_the_client_to_the_lobby_it_was_given(self):
        header = P.Header()
        header.method_id = P.PROCESS_TASK
        session = FakeSession()
        server = BNetRpcServer(player=None, lobby="192.168.1.20:12357")
        asyncio.run(server._game_utilities(session, header, P.ProcessTaskRequest().SerializeToString()))
        (body,) = session.responses
        attributes = {a.name: a.value for a in P.ProcessTaskResponse.FromString(body).result}
        self.assertEqual(attributes["response_type"].string_value, "ReferralInfo")
        self.assertEqual(attributes["hostv4"].string_value, "192.168.1.20:12357")


class SessionKeyTests(unittest.TestCase):
    def test_a_name_goes_through_the_key_and_back(self):
        for name in ("Jinxzi", "Игрок_2", "a" * 58):
            with self.subTest(name=name):
                key = session_key(name)
                self.assertEqual((len(key), name_in_key(key)), (64, name))

    def test_other_keys_carry_no_name(self):
        self.assertIsNone(name_in_key(bytes(range(1, 65))))  # what the emulator sent before
        self.assertIsNone(name_in_key(b""))
        with self.assertRaises(ValueError):
            session_key("a" * 59)


if __name__ == "__main__":
    unittest.main()
