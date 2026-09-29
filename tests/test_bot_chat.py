"""The bot only talks when the player turns "Bot talks" on in the dashboard."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile
from ow174.jam.groups import CHAT_IN, FRIENDS
from ow174.lobby.handlers import chat


def make_session(bot_chat):
    player = SimpleNamespace(name="Alpha", account_lo=1, account={"+0x0": 1}, virtual=False)
    bot = SimpleNamespace(name="Bot", account_lo=2, account={"+0x0": 2}, virtual=True)
    player_session = MagicMock()
    social = MagicMock()
    social.accounts.bot = bot
    social.accounts.by_id.side_effect = {1: player, 2: bot}.get
    social.channel_members.return_value = [player, bot]
    social.chat_message.side_effect = lambda channel, sender, text, *rest: {"from": sender.name, "text": text}
    server = MagicMock(social=social)
    server.session_of.side_effect = lambda account_lo: player_session if account_lo == 1 else None
    session = MagicMock(server=server, account=player, profile=Profile(bot_chat=bot_chat))
    return session, player_session


class BotChatTests(unittest.TestCase):
    def channel_texts(self, bot_chat):
        session, player_session = make_session(bot_chat)
        chat.chat(session, {"+0x78": {"+0x10": 5}, "+0x90": "hi"})
        return [
            call.args[2]["text"] for call in player_session.send.call_args_list if call.args[0] == CHAT_IN
        ]

    def whisper_replies(self, bot_chat):
        session, _ = make_session(bot_chat)
        chat.whisper(session, {"+0x78": 9, "+0x80": {"+0x0": 2}, "+0xA0": "hi"})
        return [call for call in session.send.call_args_list if call.args[:2] == (FRIENDS, 27117)]

    def test_the_bot_is_quiet_by_default(self):
        self.assertFalse(Profile().bot_chat)
        self.assertEqual(self.channel_texts(bot_chat=False), ["hi"])
        self.assertEqual(self.whisper_replies(bot_chat=False), [])

    def test_the_bot_answers_when_the_switch_is_on(self):
        texts = self.channel_texts(bot_chat=True)
        self.assertEqual(texts[0], "hi")
        self.assertIn("I hear you", texts[1])
        self.assertEqual(len(self.whisper_replies(bot_chat=True)), 1)


if __name__ == "__main__":
    unittest.main()
