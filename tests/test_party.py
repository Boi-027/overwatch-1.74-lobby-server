"""Party invites between real players (20704 -> 22108/22109) and the pending tile they leave in the
inviter's party panel, leader transfer (22106) and the player status that friends see (27011)."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile, save_profile
from ow174.accounts.registry import Accounts
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.content.player import INVITEE, MEMBER
from ow174.content.presence import (
    APPEAR_OFFLINE_FIELD,
    AWAY_FIELD,
    BUSY_FIELD,
    GAME_ACCOUNT_ONLINE,
    OFFLINE,
    ONLINE_BOOL,
    STATUS_AWAY,
    STATUS_BUSY,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    _key_group_field,
)
from ow174.jam.codec import Schemas
from ow174.jam.groups import FRIENDS, PARTY
from ow174.jam.values import id16
from ow174.lobby.handlers.friends import set_status
from ow174.lobby.handlers.party import (
    INVITE,
    INVITE_CANCELLED,
    accept_invite,
    decline_invite,
    invite,
    join_group,
    kick,
    make_leader,
)
from ow174.lobby.server import LobbyServer
from ow174.services.social import INVITE_SECONDS, Social

GAME_ONLINE = _key_group_field(GAME_ACCOUNT_ONLINE)


class PartyTests(unittest.TestCase):
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
        self.notified = []
        self.presence_sent = []
        self.server = SimpleNamespace(
            social=self.social,
            notify_party=self.notified.append,
            notify_presence=self.presence_sent.append,
            session_of=lambda account_lo: self.social.sessions.get(account_lo),
        )
        self.alpha, self.alpha_sent = self.session("Alpha")
        self.beta, self.beta_sent = self.session("Beta")
        self.timers = []  # invite timeouts, run by hand instead of after 25 s
        patcher = mock.patch("ow174.lobby.handlers.party.threading", SimpleNamespace(Timer=self.timer))
        patcher.start()
        self.addCleanup(patcher.stop)

    def timer(self, interval, function):
        timer = SimpleNamespace(interval=interval, function=function, daemon=False, start=lambda: None)
        self.timers.append(timer)
        return timer

    def session(self, name):
        sent = []
        account = self.accounts.get(name)
        session = SimpleNamespace(
            account=account,
            profile=account.profile,
            server=self.server,
            log=lambda *args: None,
            send=lambda crc, msg_id, value: sent.append((crc, msg_id, value)),
        )
        self.social.sessions[account.account_lo] = session
        return session, sent

    def invite_beta(self):
        invite(self.alpha, {"+0x78": self.beta.account.account})
        ((crc, msg_id, value),) = self.beta_sent
        self.assertEqual((crc, msg_id), (PARTY, INVITE))
        self.schemas.encode(PARTY, INVITE, value)
        return value

    def test_an_invite_shows_the_join_popup_with_the_inviter(self):
        value = self.invite_beta()
        self.assertEqual(value["+0x78"], self.alpha.account.account)
        self.assertEqual(value["+0x88"]["+0x0"], self.alpha.account.account)

    def test_accept_joins_the_inviters_party(self):
        self.invite_beta()
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        party = self.social.party_of(self.alpha.account)
        self.assertEqual(party.members, [self.alpha.account, self.beta.account])
        self.assertEqual(self.notified, [party, party])  # the pending tile, then the member
        self.assertEqual(
            [member["+0xE0"] for member in self.social.party_state(party)["+0x78"]["+0x0"]], [MEMBER, MEMBER]
        )

    def test_accept_without_an_invite_does_nothing(self):
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertIsNot(self.social.party_of(self.beta.account), self.social.party_of(self.alpha.account))

    def test_the_inviter_sees_a_pending_tile(self):
        self.invite_beta()
        party = self.social.party_of(self.alpha.account)
        self.assertEqual(self.notified, [party])
        state = self.social.party_state(party)
        members = state["+0x78"]["+0x0"]
        self.assertEqual([member["+0xE0"] for member in members], [MEMBER, INVITEE])
        # The time left in ms; the client adds its own clock to it (0x7FF7895444D4).
        self.assertTrue(24_000 < members[1]["+0xB8"] <= INVITE_SECONDS * 1000)
        self.schemas.encode(PARTY, 20700, state)
        self.assertEqual(len(self.social.group(party)["+0x0"]), 1)  # the group finder lists members only

    def test_an_unanswered_invite_times_out(self):
        self.invite_beta()
        party = self.social.party_of(self.alpha.account)
        (timer,) = self.timers
        self.assertEqual(timer.interval, INVITE_SECONDS)
        timer.function()
        self.assertEqual(party.invites, {})
        self.assertEqual(self.notified, [party, party])
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertEqual(party.members, [self.alpha.account])

    def test_a_new_invite_is_not_dropped_by_the_old_timeout(self):
        self.invite_beta()
        self.beta_sent.clear()
        self.invite_beta()
        self.timers[0].function()
        self.assertIn(self.beta.account.account_lo, self.social.party_of(self.alpha.account).invites)

    def test_decline_drops_the_invite(self):
        self.invite_beta()
        decline_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertEqual(self.social.party_of(self.alpha.account).invites, {})
        self.assertEqual(len(self.notified), 2)  # the pending tile goes away
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertEqual(self.social.party_of(self.alpha.account).members, [self.alpha.account])

    def test_the_cross_on_the_pending_tile_cancels_the_invite(self):
        # The cross sends a kick (22105) with the invitee's id.
        self.invite_beta()
        self.beta_sent.clear()
        kick(self.alpha, {"+0x78": self.beta.account.account})
        party = self.social.party_of(self.alpha.account)
        self.assertEqual(party.invites, {})
        self.assertEqual(self.notified, [party, party])  # the pending tile goes away
        ((crc, msg_id, value),) = self.beta_sent
        self.assertEqual((crc, msg_id), (PARTY, INVITE_CANCELLED))
        self.assertEqual(value, {"+0x78": self.alpha.account.account})  # the inviter's id, as in 20704
        self.schemas.encode(PARTY, INVITE_CANCELLED, value)
        self.timers[0].function()  # the old timeout finds nothing to drop
        self.assertEqual(self.notified, [party, party])
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertEqual(party.members, [self.alpha.account])

    def test_a_pending_invite_does_not_take_over_a_group_finder_join(self):
        self.invite_beta()
        gamma, _ = self.session("Gamma")
        group = self.social.party_of(gamma.account)
        group.listing, group.searching = {"+0x80": [1]}, True
        join_group(self.beta, {"+0x78": id16(0, 0), "+0x88": id16(*group.party_id)})
        self.assertIs(self.social.party_of(self.beta.account), group)

    def test_only_the_leader_hands_the_party_over(self):
        self.invite_beta()
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        party = self.social.party_of(self.alpha.account)
        make_leader(self.beta, {"+0x78": self.alpha.account.account})
        self.assertIs(party.leader, self.alpha.account)
        make_leader(self.alpha, {"+0x78": self.beta.account.account})
        self.assertIs(party.leader, self.beta.account)
        self.assertEqual(party.members, [self.beta.account, self.alpha.account])

    def test_the_status_is_kept_and_sent_to_friends(self):
        set_status(self.alpha, {"+0x78": STATUS_AWAY})
        self.assertEqual(self.alpha.account.status, STATUS_AWAY)
        self.assertEqual(self.presence_sent, [self.alpha.account])
        self.assertEqual(self.notified, [self.social.party_of(self.alpha.account)])  # refreshes the cards
        set_status(self.alpha, {"+0x78": 9})
        self.assertEqual(self.alpha.account.status, STATUS_AWAY)

    @staticmethod
    def bools(records, key):
        """The values of one presence field, (group, field), in every record that has it."""
        return [
            field["+0x30"]
            for record in records
            for field in record["+0x20"]
            if _key_group_field(field["+0x8"]) == key
        ]

    def test_friends_see_away_busy_and_appear_offline(self):
        for status, field in ((STATUS_AWAY, AWAY_FIELD), (STATUS_BUSY, BUSY_FIELD)):
            self.alpha.account.status = status
            self.assertEqual(self.bools(self.social.presence(self.alpha.account), field), [ONLINE_BOOL])
        self.alpha.account.status = STATUS_OFFLINE
        records = self.social.presence(self.alpha.account)
        self.assertEqual(self.bools(records, GAME_ONLINE), [OFFLINE, OFFLINE])
        self.assertEqual(self.bools(records, APPEAR_OFFLINE_FIELD), [OFFLINE])  # only their own copy says why
        self.schemas.encode(FRIENDS, 27113, {"+0x78": records})

    def test_the_own_copy_keeps_the_picked_status(self):
        # The client shows its own status from its own presence (0x7FF7896145E0), not from the pick.
        self.alpha.account.status = STATUS_OFFLINE
        records = self.social.own_presence(self.alpha.account)
        self.assertEqual(self.bools(records, APPEAR_OFFLINE_FIELD), [ONLINE_BOOL])
        self.assertEqual(self.bools(records, GAME_ONLINE), [ONLINE_BOOL, ONLINE_BOOL])
        self.alpha.account.status = STATUS_ONLINE
        records = self.social.own_presence(self.alpha.account)
        for field in (AWAY_FIELD, BUSY_FIELD, APPEAR_OFFLINE_FIELD):
            self.assertEqual(self.bools(records, field), [OFFLINE])

    def test_a_status_change_reaches_the_player_and_their_friends(self):
        self.alpha.account.profile.friends = ["Beta"]
        self.alpha.account.status = STATUS_OFFLINE
        LobbyServer.notify_presence(self.server, self.alpha.account)
        (own,) = [value["+0x78"] for _, msg_id, value in self.alpha_sent if msg_id == 27113]
        (seen,) = [value["+0x78"] for _, msg_id, value in self.beta_sent if msg_id == 27113]
        self.assertEqual(self.bools(own, APPEAR_OFFLINE_FIELD), [ONLINE_BOOL])
        self.assertEqual(self.bools(seen, GAME_ONLINE), [OFFLINE, OFFLINE])

    def test_friends_get_the_presence_and_card_but_not_a_whole_friends_list(self):
        # After a login, a logout or an icon change. A whole list (27100) made the client say every
        # online friend was entering the game again.
        self.alpha.account.profile.friends = ["Beta"]
        LobbyServer.notify_friends(self.server, self.alpha.account)
        self.assertEqual([msg_id for _, msg_id, _ in self.beta_sent], [27113, 20809])
        for crc, msg_id, value in self.beta_sent:
            self.schemas.encode(crc, msg_id, value)
        self.assertEqual(self.alpha_sent, [])

    def test_a_status_does_not_change_party_membership(self):
        # +0xE0 is membership: 1-4 would make an away leader an invitee in the client's eyes.
        self.invite_beta()
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.alpha.account.status = STATUS_AWAY
        self.beta.account.status = STATUS_BUSY
        members = self.social.party_state(self.social.party_of(self.alpha.account))["+0x78"]["+0x0"]
        self.assertEqual([member["+0xE0"] for member in members], [5, 5])


if __name__ == "__main__":
    unittest.main()
