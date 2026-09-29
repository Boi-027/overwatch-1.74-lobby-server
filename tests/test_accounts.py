"""Accounts: the name typed at login picks the profile file; the nickname inside it can differ."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile, save_profile
from ow174.accounts.registry import Accounts


class AccountTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.template = self.root / "template.json"
        save_profile(Profile(player_name="Researcher"), self.template)

    def accounts(self):
        return Accounts(self.root / "profiles", self.template)

    def test_a_new_account_is_named_after_the_login_name(self):
        self.assertEqual(self.accounts().get("Alpha").profile.player_name, "Alpha")

    def test_a_changed_nickname_survives_the_next_login(self):
        account = self.accounts().get("Alpha")
        account.profile.player_name = "NewNick"
        account.save()
        again = self.accounts().get("Alpha")
        self.assertEqual(again.profile.player_name, "NewNick")
        self.assertEqual(again.name, "Alpha")


if __name__ == "__main__":
    unittest.main()
