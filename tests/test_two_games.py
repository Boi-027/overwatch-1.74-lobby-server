"""Retail games with their own accounts: a second game on this PC, and games on other PCs that join
with their own Battle.net emulator."""

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
from ow174.bnet.session_key import session_key
from ow174.launcher import LaunchError, retail
from ow174.lobby.handlers.login import (
    INCOMPATIBLE_CLIENT,
    SIGNED_IN_ELSEWHERE,
    _take_over_account,
    login,
    login_name,
)
from ow174.lobby.server import LobbyServer


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
        second_game = {(50002, RPC_PORT): "Second"}  # the second game's Battle.net connection
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

    def session(self, local=True):
        kicked = []
        return SimpleNamespace(
            server=self.server,
            channel=SimpleNamespace(seq=1),
            local=local,
            log=lambda *args: None,
            kick=kicked.append,
            kicked=kicked,
        )

    def test_a_second_game_plays_its_account_and_the_first_the_dashboards(self):
        self.assertIs(LobbyServer.game_account(self.server, 50002, RPC_PORT), self.second)
        self.assertIs(LobbyServer.game_account(self.server, 50001, RPC_PORT), self.main)

    def test_a_login_names_its_account(self):
        # The tournament frontend sends the typed name; the retail one passes on the session key.
        self.assertEqual(login_name({"+0x78": " Main "}), "Main")
        self.assertEqual(login_name({"+0x78": "", "+0xB8": {"+0x20": list(session_key("Second"))}}), "Second")
        self.assertEqual(login_name({"+0x78": "", "+0xB8": {"+0x20": list(range(1, 65))}}), "")

    def test_the_dashboard_follows_only_the_game_on_this_pc(self):
        _take_over_account(self.session(), self.main)
        _take_over_account(self.session(), self.second)  # the second game
        _take_over_account(self.session(local=False), self.accounts.get("Friend"))  # another PC
        self.assertIs(self.server.selected, self.main)
        self.assertEqual(len(self.server.social.sessions), 3)

    def test_a_game_from_another_pc_without_a_name_is_refused(self):
        # An older join would play the dashboard's account and drop the host's own game.
        remote = self.session(local=False)
        login(remote, {"+0x78": "", "+0xB8": {"+0x20": list(range(1, 65))}})
        self.assertEqual(remote.kicked, [INCOMPATIBLE_CLIENT])
        self.assertEqual(self.server.social.sessions, {})

    def test_a_new_login_drops_the_old_one_with_the_reason(self):
        # The old game shows "signed in on another device" instead of "lost connection".
        old = self.session()
        _take_over_account(old, self.main)
        _take_over_account(self.session(), self.main)
        self.assertEqual(old.kicked, [SIGNED_IN_ELSEWHERE])

    def test_play_as_reconnects_only_the_game_on_this_pc(self):
        dropped = []

        def game(name, local):
            account = self.accounts.get(name)
            return SimpleNamespace(
                local=local, account=account, log=lambda *args: None, disconnect=lambda: dropped.append(name)
            )

        self.server.sessions = [game("Main", True), game("Second", True), game("Friend", False)]
        LobbyServer.reconnect_own_game(self.server)
        self.assertEqual(dropped, ["Main"])


if __name__ == "__main__":
    unittest.main()
