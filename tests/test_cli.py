"""Picking the mode when START.bat is double-clicked."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174 import cli


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

    def test_anything_else_asks_again(self):
        self.assertEqual(self.ask("tournament", "9", "2"), "tournament")

    def test_a_mode_on_the_command_line_is_not_asked(self):
        self.assertEqual(cli.parse_args(["--mode", "tournament"]).mode, "tournament")
        self.assertIsNone(cli.parse_args([]).mode)


if __name__ == "__main__":
    unittest.main()
