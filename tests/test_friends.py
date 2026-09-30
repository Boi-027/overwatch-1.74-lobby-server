"""Friends are added by BattleTag and saved in both profiles; nobody is a friend by default."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile, battle_tag, save_profile
from ow174.accounts.registry import Accounts
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.content.presence import GAME_ACCOUNT_ONLINE, OFFLINE
from ow174.content.presence import _encode_varint as _varint
from ow174.jam.codec import Schemas
from ow174.jam.groups import FRIENDS, LOBBY
from ow174.lobby.handlers import friends
from ow174.services.social import Social


class FriendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = Schemas()
        cls.content = Content(cls.schemas, RetailTemplates(), ItemDB())

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        save_profile(Profile(), root / "template.json")
        self.accounts = Accounts(root / "profiles", root / "template.json")
        self.social = Social(self.accounts, self.content)
        self.alpha = self.accounts.get("Alpha")
        self.beta = self.accounts.get("Beta")

    def names(self, account):
        return [friend.name for friend in self.social.friends_of(account)]

    def test_battle_tags_are_stable_and_found_in_any_letter_case(self):
        tag = self.beta.battle_tag
        self.assertRegex(tag, r"^Beta#\d{4}$")
        self.assertEqual(tag, battle_tag("Beta", self.beta.account_lo))
        self.assertIs(self.accounts.by_battle_tag(tag.upper()), self.beta)
        self.assertIsNone(self.accounts.by_battle_tag("Beta#0000"))
        self.assertIsNone(self.accounts.by_battle_tag("Nobody#1234"))

    def test_only_the_bot_is_a_friend_at_first(self):
        self.assertEqual(self.names(self.alpha), ["Bot"])

    def test_friends_online_get_their_cards_sent(self):
        # Without a card the friends list shows only the BattleTag from presence.
        self.alpha.profile.friends = ["Beta"]
        cards = [card["+0x0"]["+0x0"]["+0x0"] for card in self.social.friend_cards(self.alpha)]
        self.assertEqual(cards, [self.accounts.bot.account_lo])  # Beta is offline
        self.social.sessions[self.beta.account_lo] = MagicMock()
        cards = self.social.friend_cards(self.alpha)
        self.assertEqual(
            [card["+0x0"]["+0x0"]["+0x0"] for card in cards],
            [self.accounts.bot.account_lo, self.beta.account_lo],
        )
        self.schemas.encode(LOBBY, 20809, {"+0x78": cards})

    def test_a_request_waits_for_the_other_player(self):
        target, outcome = self.social.request_friend(self.alpha, self.beta.battle_tag)
        self.assertEqual((target, outcome), (self.beta, "sent"))
        self.assertEqual(self.beta.profile.friend_requests, ["Alpha"])
        (request,) = self.social.friends_state(self.beta)["+0x90"]
        self.assertEqual(request["+0x30"], self.alpha.battle_tag)
        self.assertNotIn("Beta", self.names(self.alpha))

    def test_a_request_back_means_yes_and_is_saved(self):
        self.social.request_friend(self.alpha, self.beta.battle_tag)
        _, outcome = self.social.request_friend(self.beta, self.alpha.battle_tag)
        self.assertEqual(outcome, "added")
        self.assertIn("Beta", self.names(self.alpha))
        self.assertIn("Alpha", self.names(self.beta))
        self.assertEqual(self.beta.profile.friend_requests, [])
        reloaded = Accounts(self.accounts.directory, self.accounts.template)
        self.assertEqual(reloaded.get("Alpha").profile.friends, ["Beta"])

    def test_bad_requests_are_refused(self):
        self.assertEqual(self.social.request_friend(self.alpha, "Nobody#1234")[1], "unknown")
        self.assertEqual(self.social.request_friend(self.alpha, self.alpha.battle_tag)[1], "self")
        self.assertEqual(self.social.request_friend(self.alpha, self.accounts.bot.battle_tag)[1], "already")

    def test_removing_a_friend_removes_it_on_both_sides(self):
        self.social.accept_friend(self.alpha, self.beta)
        self.social.remove_friend(self.alpha, self.beta)
        self.assertEqual(self.names(self.alpha), ["Bot"])
        self.assertEqual(self.names(self.beta), ["Bot"])

    def test_presence_shows_the_battle_tag_and_the_nickname(self):
        # The game builds "Name#1234 (you)" from the presence BattleTag field.
        self.alpha.profile.player_name = "Jeff"
        values = b"".join(
            field["+0x30"] for record in self.social.presence(self.alpha) for field in record["+0x20"]
        )
        self.assertIn(self.alpha.battle_tag.encode(), values)
        self.assertIn(b"Jeff", values)
        self.assertNotIn(b"XXXXXXXX", values)

    def test_the_battle_tag_follows_the_nickname_and_keeps_its_digits(self):
        # The client shows the part before '#' as the name, so the player card carries the whole tag.
        digits = self.alpha.battle_tag.split("#")[1]
        self.alpha.profile.player_name = "Jeff"
        self.assertEqual(self.alpha.battle_tag, f"Jeff#{digits}")
        self.assertEqual(self.social.player_record(self.alpha)["+0x40"], f"Jeff#{digits}")
        self.assertIs(self.accounts.by_battle_tag(f"jeff#{digits}"), self.alpha)

    def test_offline_friends_have_presence_marked_offline(self):
        # Without presence the client counts an offline friend but does not list them.
        self.social.accept_friend(self.alpha, self.beta)
        records = self.social.friends_state(self.alpha)["+0xA8"]
        self.assertIn(self.beta.account_lo, [record["+0x0"]["+0x0"] for record in records])
        flags = set()
        for record in self.social.presence(self.beta):
            for field in record["+0x20"]:
                if field["+0x8"] == GAME_ACCOUNT_ONLINE:
                    flags.add(field["+0x30"])
        self.assertEqual(flags, {OFFLINE})

    def test_presence_times_are_the_last_login_or_logout(self):
        # Tested in game: with the capture's times an offline friend showed as "offline (4 years)".
        self.beta.profile.last_online = 1_700_000_000
        values = [field["+0x30"] for record in self.social.presence(self.beta) for field in record["+0x20"]]
        self.assertIn(b"\x18" + _varint(1_700_000_000 * 1_000_000), values)
        self.assertNotIn(b"\x18" + _varint(1643255908866797), values)

    def test_an_account_never_logged_in_counts_from_its_profile_file(self):
        # With "now" it showed as "offline (4 minutes)" counted from the viewer's own login.
        self.beta.profile.last_online = 0
        self.beta.created = 1_600_000_000
        values = [field["+0x30"] for record in self.social.presence(self.beta) for field in record["+0x20"]]
        self.assertIn(b"\x18" + _varint(1_600_000_000 * 1_000_000), values)

    def test_offline_presence_leaves_out_online_friends(self):
        self.social.accept_friend(self.alpha, self.beta)
        ids = {record["+0x0"]["+0x0"] for record in self.social.offline_presence(self.alpha)}
        self.assertIn(self.beta.account_lo, ids)
        self.assertNotIn(self.accounts.bot.account_lo, ids)

    def test_a_party_has_one_leader(self):
        party = self.social.party_of(self.alpha)
        self.social.join(self.beta, party)
        members = self.social.party_state(party)["+0x78"]["+0x0"]
        self.assertEqual([member["+0xE2"] for member in members], [True, False])

    def session_of(self, account):
        server = SimpleNamespace(
            social=self.social, accounts=self.accounts, session_of=lambda account_lo: None
        )
        return MagicMock(server=server, account=account, profile=account.profile)

    def online(self, *accounts):
        """Sessions for accounts that are all online, so each sees what the other is sent."""
        sessions = {}
        server = SimpleNamespace(social=self.social, accounts=self.accounts, session_of=sessions.get)
        for account in accounts:
            sessions[account.account_lo] = MagicMock(server=server, account=account, profile=account.profile)
            self.social.sessions[account.account_lo] = sessions[account.account_lo]
        return [sessions[account.account_lo] for account in accounts]

    def sent(self, session):
        messages = [(call.args[1], call.args[2]) for call in session.send.call_args_list]
        for msg_id, value in messages:
            self.schemas.encode(FRIENDS if msg_id != 20809 else LOBBY, msg_id, value)
        return messages

    def test_accepting_in_game_makes_both_friends(self):
        self.social.request_friend(self.alpha, self.beta.battle_tag)
        session = self.session_of(self.beta)
        friends.answer_request(session, {"+0x78": 6, "+0x80": self.alpha.account, "+0x90": 0})
        session.send.assert_any_call(FRIENDS, 27111, {"+0x78": 6, "+0x80": 0})
        self.assertIn("Alpha", self.names(self.beta))
        self.assertIn("Beta", self.names(self.alpha))

    def test_answering_a_request_that_does_not_exist_fails(self):
        session = self.session_of(self.beta)
        friends.answer_request(session, {"+0x78": 7, "+0x80": self.alpha.account, "+0x90": 0})
        session.send.assert_any_call(FRIENDS, 27111, {"+0x78": 7, "+0x80": 1})
        self.assertNotIn("Alpha", self.names(self.beta))

    def test_a_request_in_game_is_answered_and_shown_to_the_other_player(self):
        sessions = {}
        server = SimpleNamespace(
            social=self.social, accounts=self.accounts, session_of=lambda account_lo: sessions.get(account_lo)
        )
        alpha = MagicMock(server=server, account=self.alpha, profile=self.alpha.profile)
        beta = MagicMock(server=server, account=self.beta, profile=self.beta.profile)
        sessions[self.beta.account_lo] = beta
        friends.send_request(alpha, {"+0x78": 3, "+0x80": 1, "+0x88": self.beta.battle_tag, "+0xB0": ""})
        alpha.send.assert_any_call(FRIENDS, 27110, {"+0x78": 3, "+0x80": friends.SENT, "+0x88": 0})
        (call,) = [c for c in beta.send.call_args_list if c.args[1] == 27107]
        self.assertEqual(call.args[2]["+0x78"]["+0x30"], self.alpha.battle_tag)
        self.schemas.encode(FRIENDS, 27107, call.args[2])
        friends.send_request(alpha, {"+0x78": 4, "+0x80": 1, "+0x88": "Nobody#1234", "+0xB0": ""})
        alpha.send.assert_any_call(FRIENDS, 27110, {"+0x78": 4, "+0x80": friends.BAD_NAME, "+0x88": 0})

    def test_removing_in_game_answers_27112(self):
        self.social.accept_friend(self.alpha, self.beta)
        session = self.session_of(self.alpha)
        friends.remove_friend(session, {"+0x78": 8, "+0x80": self.beta.account})
        session.send.assert_any_call(FRIENDS, 27112, {"+0x78": 8, "+0x80": 0})
        self.assertEqual(self.names(self.alpha), ["Bot"])

    # A whole friends list (27100) makes the client announce every online friend again, so changes
    # go one friend at a time.
    def test_a_friend_removed_leaves_both_lists_one_entry_at_a_time(self):
        self.social.accept_friend(self.alpha, self.beta)
        alpha, beta = self.online(self.alpha, self.beta)
        friends.remove_friend(alpha, {"+0x78": 8, "+0x80": self.beta.account})
        self.assertIn((27106, {"+0x78": self.beta.account}), self.sent(alpha))
        self.assertEqual(self.sent(beta), [(27106, {"+0x78": self.alpha.account})])
        self.assertNotIn(27100, [msg_id for msg_id, _ in self.sent(alpha)])

    def test_an_accepted_request_adds_each_friend_to_the_other(self):
        self.social.request_friend(self.alpha, self.beta.battle_tag)
        alpha, beta = self.online(self.alpha, self.beta)
        friends.answer_request(beta, {"+0x78": 6, "+0x80": self.alpha.account, "+0x90": 0})
        beta_got = [msg_id for msg_id, _ in self.sent(beta)]
        self.assertEqual(beta_got, [27111, 27108, 27105, 27113, 20809])  # the request goes, the friend comes
        self.assertIn((27105, {"+0x78": self.social.friend_entry(self.alpha)}), self.sent(beta))
        self.assertEqual([msg_id for msg_id, _ in self.sent(alpha)], [27105, 27113, 20809])
        self.assertIn((27105, {"+0x78": self.social.friend_entry(self.beta)}), self.sent(alpha))

    def test_a_declined_request_only_leaves_the_list(self):
        self.social.request_friend(self.alpha, self.beta.battle_tag)
        alpha, beta = self.online(self.alpha, self.beta)
        friends.answer_request(beta, {"+0x78": 6, "+0x80": self.alpha.account, "+0x90": 1})
        self.assertEqual([msg_id for msg_id, _ in self.sent(beta)], [27111, 27108])
        self.assertEqual(self.sent(alpha), [])


if __name__ == "__main__":
    unittest.main()
