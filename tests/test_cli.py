"""Picking the mode when START.bat is double-clicked, and joining someone else's server."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174 import cli
from ow174.launcher import LaunchError


class AskModeTests(unittest.TestCase):
    def ask(self, *answers):
        with patch("builtins.input", side_effect=answers), patch("builtins.print"):
            return cli.ask_mode()

    def test_enter_picks_retail(self):
        self.assertEqual(self.ask(""), "retail")

    def test_numbers_pick_the_modes(self):
        self.assertEqual(self.ask("1"), "retail")
        self.assertEqual(self.ask(" 2 "), "tournament")
        self.assertEqual(self.ask("3"), "server")
        self.assertEqual(self.ask("4"), "join")

    def test_anything_else_asks_again(self):
        self.assertEqual(self.ask("tournament", "9", "2"), "tournament")

    def test_a_mode_on_the_command_line_is_not_asked(self):
        self.assertEqual(cli.parse_args(["--mode", "tournament"]).mode, "tournament")
        self.assertIsNone(cli.parse_args([]).mode)


class JoinTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.saved = Path(temp.name) / "server_address.txt"

    def ask(self, *answers):
        with patch("builtins.input", side_effect=answers), patch("builtins.print"):
            return cli.ask_server(self.saved)

    def test_server_addresses(self):
        self.assertTrue(cli.is_server_address("1.2.3.4:12357"))
        self.assertTrue(cli.is_server_address("lobby.example.org:3724"))
        for text in ("", "1.2.3.4", ":12357", "1.2.3.4:port", "1.2.3.4:0", "1.2.3.4:70000", "a b:1"):
            self.assertFalse(cli.is_server_address(text), text)

    def test_the_address_is_remembered_and_enter_reuses_it(self):
        self.assertEqual(self.ask("nope", "1.2.3.4:12357"), "1.2.3.4:12357")
        self.assertEqual(self.ask(""), "1.2.3.4:12357")

    def test_join_starts_only_the_game_on_that_server(self):
        args = SimpleNamespace(server="1.2.3.4:12357", game_exe=None, locale="auto")
        with (
            patch.object(cli, "find_game", return_value=Path("Overwatch.exe")),
            patch.object(cli, "close_running_copy"),
            patch.object(cli, "start_game") as start_game,
            patch.object(cli, "LobbyServer") as lobby,
        ):
            cli.join(args)
        start_game.assert_called_once_with(
            Path("Overwatch.exe"), ["--tank_TournamentMode", "--lobbyServer=1.2.3.4:12357"], "auto"
        )
        lobby.assert_not_called()

    def test_a_bad_address_on_the_command_line_is_refused(self):
        args = SimpleNamespace(server="1.2.3.4", game_exe=None, locale="auto")
        with self.assertRaises(LaunchError):
            cli.join(args)


if __name__ == "__main__":
    unittest.main()
