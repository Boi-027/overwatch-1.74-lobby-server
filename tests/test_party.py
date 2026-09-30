"""Party invites between real players (20704 -> 22108/22109), leader transfer (22106) and the
player status that friends and party members see (27011)."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.accounts.profile import Profile, save_profile
from ow174.accounts.registry import Accounts
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content import Content
from ow174.content.presence import AWAY_FIELD, STATUS_AWAY, STATUS_BUSY, STATUS_OFFLINE, _key_group_field
from ow174.jam.codec import Schemas
from ow174.jam.groups import FRIENDS, PARTY
from ow174.jam.values import id16
from ow174.lobby.handlers.friends import set_status
from ow174.lobby.handlers.party import INVITE, accept_invite, decline_invite, invite, join_group, make_leader
from ow174.services.social import Social


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
        self.assertEqual(self.notified, [party])

    def test_accept_without_an_invite_does_nothing(self):
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertIsNot(self.social.party_of(self.beta.account), self.social.party_of(self.alpha.account))

    def test_decline_drops_the_invite(self):
        self.invite_beta()
        decline_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertEqual(self.social.party_of(self.alpha.account).invites, {})
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.assertEqual(self.social.party_of(self.alpha.account).members, [self.alpha.account])

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
        set_status(self.alpha, {"+0x78": 9})
        self.assertEqual(self.alpha.account.status, STATUS_AWAY)

    def test_friends_see_away_busy_and_appear_offline(self):
        self.alpha.account.status = STATUS_AWAY
        fields = [field for record in self.social.presence(self.alpha.account) for field in record["+0x20"]]
        away = [field["+0x30"] for field in fields if _key_group_field(field["+0x8"]) == AWAY_FIELD]
        self.assertIn(b"\x10\x01", away)
        self.alpha.account.status = STATUS_OFFLINE
        self.assertEqual(self.social.effective_status(self.alpha.account), STATUS_OFFLINE)
        self.schemas.encode(FRIENDS, 27113, {"+0x78": self.social.presence(self.alpha.account)})

    def test_party_members_show_their_status_on_the_portrait_ring(self):
        self.invite_beta()
        accept_invite(self.beta, {"+0x78": self.alpha.account.account})
        self.beta.account.status = STATUS_BUSY
        members = self.social.party_state(self.social.party_of(self.alpha.account))["+0x78"]["+0x0"]
        self.assertEqual([member["+0xE0"] for member in members], [5, 6])


if __name__ == "__main__":
    unittest.main()
