"""A join asks the host for its player's BattleTag on the lobby port before the game starts."""

import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile, save_profile
from ow174.accounts.registry import Accounts, account_id_for
from ow174.jam.handshake import HELLO_CLIENT
from ow174.lobby.battle_tag_query import answer_query, ask_battle_tag
from ow174.lobby.server import LobbyServer


class QueryTests(unittest.TestCase):
    def test_a_join_gets_the_battle_tag_from_the_lobby_port(self):
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        answered = []

        def serve():
            connection, _ = listener.accept()
            with connection:
                answered.append(answer_query(connection, lambda name: f"{name.upper()}#1234"))

        thread = threading.Thread(target=serve)
        thread.start()
        self.assertEqual(ask_battle_tag("127.0.0.1", listener.getsockname()[1], "Jinxzi"), "JINXZI#1234")
        thread.join(5)
        self.assertEqual(answered, [True])

    def test_a_game_is_left_to_the_handshake(self):
        lobby, game = socket.socketpair()
        self.addCleanup(lobby.close)
        self.addCleanup(game.close)
        game.sendall(HELLO_CLIENT)
        self.assertFalse(answer_query(lobby, lambda name: "unused"))
        self.assertEqual(lobby.recv(len(HELLO_CLIENT)), HELLO_CLIENT)  # nothing was read

    def test_an_unreachable_host_raises(self):
        listener = socket.create_server(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        with self.assertRaises(OSError):
            ask_battle_tag("127.0.0.1", port, "Jinxzi")

    def test_an_older_server_gives_no_answer(self):
        # It takes the question for a game's hello, finds it wrong and closes the connection.
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)

        def serve():
            connection, _ = listener.accept()
            with connection:
                connection.recv(17)

        thread = threading.Thread(target=serve)
        thread.start()
        self.assertEqual(ask_battle_tag("127.0.0.1", listener.getsockname()[1], "Jinxzi"), "")
        thread.join(5)


class BattleTagOfTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        save_profile(Profile(), root / "template.json")
        self.accounts = Accounts(root / "profiles", root / "template.json")
        self.server = SimpleNamespace(accounts=self.accounts)

    def test_a_player_gets_the_nickname_the_host_gave_them(self):
        self.accounts.get("Jinxzi").profile.player_name = "XQC"
        tag = LobbyServer.battle_tag_of(self.server, "Jinxzi")
        self.assertEqual(tag, f"XQC#{1000 + account_id_for('Jinxzi') % 9000}")

    def test_a_new_name_is_not_saved_by_asking(self):
        tag = LobbyServer.battle_tag_of(self.server, "Newcomer")
        self.assertEqual(tag, f"Newcomer#{1000 + account_id_for('Newcomer') % 9000}")
        self.assertNotIn("Newcomer", self.accounts.all_saved())


if __name__ == "__main__":
    unittest.main()
