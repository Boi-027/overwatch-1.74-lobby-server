"""Two retail games on one PC: the Battle.net emulator and the lobby give each game its own account."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile, save_profile
from ow174.accounts.registry import Accounts
from ow174.bnet.service import RPC_PORT
from ow174.launcher import LaunchError, retail
from ow174.lobby.handlers.login import SIGNED_IN_ELSEWHERE, _take_over_account
from ow174.lobby.server import LobbyServer

LOBBY_PORT = 3724


class FakeGame:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = 1


class RetailGamesTests(unittest.TestCase):
    def setUp(self):
        self.owners = {}  # (local port, remote port) -> PID
        self.arguments = []
        for patch in (
            mock.patch.object(retail, "start_game", self.start_game),
            mock.patch.object(retail, "inject_relay", return_value=0x180000000),
            mock.patch.object(
                retail, "connection_owner", lambda local, remote: self.owners.get((local, remote))
            ),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.games = retail.RetailGames(Path("Overwatch.exe"), Path("relay.dll"), "auto", 5.0)

    def start_game(self, game, arguments, locale):
        self.arguments.append(arguments)
        return FakeGame(1000 + len(self.arguments))

    def connect(self, game, port):
        self.owners[(port, RPC_PORT)] = game.pid

    def test_a_second_game_plays_the_account_it_was_started_for(self):
        first, second = self.games.start(), self.games.start("Jxinzi")
        self.connect(first, 50001)
        self.connect(second, 50002)
        self.assertIsNone(self.games.account_of(50001, RPC_PORT))
        self.assertEqual(self.games.account_of(50002, RPC_PORT), "Jxinzi")
        self.assertEqual(self.games.second_accounts(), {"Jxinzi"})

    def test_only_a_second_game_is_kept_out_of_exclusive_fullscreen(self):
        self.games.start()
        self.games.start("Jxinzi")
        self.assertNotIn("--tank_NoFullScreen=1", self.arguments[0])
        self.assertIn("--tank_NoFullScreen=1", self.arguments[1])

    def test_a_game_without_the_relay_is_closed(self):
        no_relay = mock.patch.object(retail, "inject_relay", side_effect=LaunchError("no relay"))
        with no_relay, self.assertRaises(LaunchError):
            self.games.start("Jxinzi")
        self.assertEqual(self.games.second_accounts(), set())

    def test_a_closed_second_game_frees_its_account(self):
        second = self.games.start("Jxinzi")
        self.connect(second, 50002)
        second.returncode = 0
        self.assertIsNone(self.games.account_of(50002, RPC_PORT))
        self.assertEqual(self.games.second_accounts(), set())


class LoginAccountTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        save_profile(Profile(), root / "template.json")
        self.accounts = Accounts(root / "profiles", root / "template.json")
        self.main = self.accounts.get("Main")
        self.second = self.accounts.get("Second")
        second_game = {(50002, LOBBY_PORT): "Second"}  # the second game's lobby connection
        self.server = SimpleNamespace(
            accounts=self.accounts,
            games=SimpleNamespace(
                account_of=lambda peer_port, server_port: second_game.get((peer_port, server_port)),
                second_accounts=lambda: {"Second"},
            ),
            selected=None,
            social=SimpleNamespace(sessions={}),
            dashboard_account=lambda: self.main,
        )

    def session(self):
        return SimpleNamespace(server=self.server, channel=SimpleNamespace(seq=1))

    def test_a_second_game_plays_its_account_and_the_first_the_dashboards(self):
        self.assertIs(LobbyServer.game_account(self.server, 50002, LOBBY_PORT), self.second)
        self.assertIs(LobbyServer.game_account(self.server, 50001, LOBBY_PORT), self.main)

    def test_the_dashboard_stays_on_the_first_game(self):
        _take_over_account(self.session(), self.main)
        _take_over_account(self.session(), self.second)
        self.assertIs(self.server.selected, self.main)
        self.assertEqual(set(self.server.social.sessions), {self.main.account_lo, self.second.account_lo})

    def test_a_new_login_drops_the_old_one_with_the_reason(self):
        # The old game shows "signed in on another device" instead of "lost connection".
        kicked = []
        old = self.session()
        old.log = lambda *args: None
        old.kick = kicked.append
        _take_over_account(old, self.main)
        _take_over_account(self.session(), self.main)
        self.assertEqual(kicked, [SIGNED_IN_ELSEWHERE])


if __name__ == "__main__":
    unittest.main()
