"""Interface states (22207 -> 20812): what the client saves, such as "don't show again", comes back
at the next login."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile
from ow174.content.player import PlayerMessages
from ow174.lobby.handlers.settings import save_ux_state


class UxStateTests(unittest.TestCase):
    def test_a_saved_state_comes_back_in_20812(self):
        profile = Profile()
        session = SimpleNamespace(profile=profile, save=lambda: None, log=lambda *args: None)
        save_ux_state(session, {"+0x78": 17, "+0x7C": 2})
        save_ux_state(session, {"+0x78": 4, "+0x7C": 1})
        self.assertEqual(profile.ux_states, {"17": 2, "4": 1})
        states = PlayerMessages.ux_states(None, profile)["+0x78"]
        # The capture's states stay unless the client changed them.
        self.assertEqual(states, [{"+0x0": 2, "+0x4": 0}, {"+0x0": 1, "+0x4": 4}, {"+0x0": 2, "+0x4": 17}])


if __name__ == "__main__":
    unittest.main()
