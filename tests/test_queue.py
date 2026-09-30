"""Joining a queue. The whole party gets a 44201 entry that starts with the request's key. A role
queue also runs a role check in the party state: the entry's state and each member's choice, which
the role screen, the group banner and the search timer read."""

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
from ow174.content.passes import POOLS
from ow174.content.queue import PICKING, SEARCHING
from ow174.content.ranked import DAMAGE, SUPPORT, TANK
from ow174.jam.codec import Schemas
from ow174.jam.groups import MATCHMAKE, PARTY, PASSES, QUEUE, QUEUE_WAITS
from ow174.lobby.handlers.matchmaking import (
    accept,
    cancel_queue,
    change_roles,
    decline,
    enter_queue,
    pass_role,
    queue_joined,
    set_roles,
)
from ow174.services.social import Social


def request(card: int) -> dict:
    """A 44100 as the client sends it (competitive role queue is card 1B3, its open queue 1B4)."""
    return {
        "+0x78": {
            "+0x0": {"+0x0": 0x0630000000000000 | card, "+0x8": 0, "+0x10": 0, "+0x18": 0},
            "+0x20": 0,
            "+0x24": 15959616,
            "+0x28": 16025156,
        }
    }


ROLE_QUEUE = request(0x1B3)
OPEN_QUEUE = request(0x1B4)


class QueueTests(unittest.TestCase):
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
        self.sessions = {}
        self.searches = []
        matchmaker = SimpleNamespace(
            search=lambda party, key, card: self.searches.append(card), cancel=lambda party: None
        )
        self.server = SimpleNamespace(
            social=self.social,
            notify_party=lambda party: None,
            matchmaker=matchmaker,
            content=self.content,
            session_of=self.sessions.get,
        )
        self.alpha = self.session("Alpha")

    def session(self, name):
        account = self.accounts.get(name)
        sent = []
        session = SimpleNamespace(
            account=account,
            profile=account.profile,
            server=self.server,
            conn_id=len(self.sessions) + 1,
            log=lambda *args: None,
            send=lambda crc, msg_id, value: sent.append((crc, msg_id, value)),
            sent=sent,
        )
        self.sessions[account.account_lo] = session
        return session

    def party_state(self):
        """The party state as the client decodes it."""
        party = self.social.party_of(self.alpha.account)
        state = self.social.party_state(party)
        return self.schemas.decode(PARTY, 20700, self.schemas.encode(PARTY, 20700, state))["+0x78"]

    def entry(self):
        (entry,) = self.party_state()["+0x18"]
        return entry

    def choice(self, index):
        (choice,) = self.party_state()["+0x0"][index]["+0xC8"]
        return choice

    @staticmethod
    def messages(session, msg_id, group=QUEUE):
        return [value for crc, number, value in session.sent if (crc, number) == (group, msg_id)]

    def test_the_entry_carries_the_request_key(self):
        decoded = self.schemas.decode(MATCHMAKE, 44100, self.schemas.encode(MATCHMAKE, 44100, OPEN_QUEUE))
        joined = queue_joined(decoded["+0x78"])
        decoded = self.schemas.decode(QUEUE, 44201, self.schemas.encode(QUEUE, 44201, joined))
        self.assertEqual(decoded["+0x78"]["+0x0"], OPEN_QUEUE["+0x78"])

    def test_a_queue_without_roles_has_no_role_check(self):
        enter_queue(self.alpha, OPEN_QUEUE)
        self.assertEqual(len(self.messages(self.alpha, 44201)), 1)
        self.assertEqual(self.party_state()["+0x18"], [])

    def test_a_role_queue_starts_with_a_role_check(self):
        self.social.party_of(self.alpha.account).roles[self.alpha.account.account_lo] = [2]
        enter_queue(self.alpha, ROLE_QUEUE)
        # The client counts as queued at once; the entry's state holds the search back.
        (joined,) = self.messages(self.alpha, 44201)
        self.assertEqual(joined["+0x78"]["+0x0"], ROLE_QUEUE["+0x78"])
        entry = self.entry()
        self.assertEqual(entry["+0x18"], ROLE_QUEUE["+0x78"])
        self.assertEqual(entry["+0x54"], PICKING)
        # Priority passes only for damage.
        pass_roles = [role["+0x0"] for role in entry["+0x0"]["+0x0"]["+0x0"] if role["+0x39"]]
        self.assertEqual(pass_roles, [DAMAGE])
        # The role cards get their waits: the flex card reads role 0, damage has one with a pass.
        (waits,) = self.messages(self.alpha, 56200, QUEUE_WAITS)
        decoded = self.schemas.decode(QUEUE_WAITS, 56200, self.schemas.encode(QUEUE_WAITS, 56200, waits))
        records = {role["+0x0"]: role for role in decoded["+0x78"]["+0x0"]["+0x0"]}
        self.assertEqual(set(records), {0, DAMAGE, TANK, SUPPORT})
        self.assertGreater(records[DAMAGE]["+0x18"], records[DAMAGE]["+0x20"])
        self.assertGreater(records[DAMAGE]["+0x20"], 0)
        self.assertEqual(records[TANK]["+0x20"], 0)
        # The one who picked the queue is on the role screen: no banner, not ready yet, and no roles
        # from the last queue on the portrait badge.
        choice = self.choice(0)
        self.assertTrue(choice["+0x68"])
        self.assertFalse(choice["+0x69"])
        self.assertEqual(choice["+0x8"], [])

    def test_ready_starts_the_search_and_change_role_stops_it(self):
        enter_queue(self.alpha, ROLE_QUEUE)
        set_roles(self.alpha, {"+0x78": [2, 1, 2, 9]})
        self.assertEqual(self.choice(0)["+0x8"], [2, 1])
        self.assertTrue(self.choice(0)["+0x69"])
        self.assertEqual(self.entry()["+0x54"], SEARCHING)
        # The same key again: the client's search timer starts over.
        self.assertEqual(len(self.messages(self.alpha, 44201)), 2)
        change_roles(self.alpha, {})
        self.assertFalse(self.choice(0)["+0x69"])
        self.assertEqual(self.entry()["+0x54"], PICKING)
        set_roles(self.alpha, {"+0x78": [3]})
        self.assertEqual(self.entry()["+0x54"], SEARCHING)

    def test_a_group_waits_for_everyone(self):
        beta = self.session("Beta")
        self.social.join(beta.account, self.social.party_of(self.alpha.account))
        enter_queue(self.alpha, ROLE_QUEUE)
        self.assertEqual(len(self.messages(beta, 44201)), 1)
        self.assertFalse(self.choice(1)["+0x68"])  # Beta gets the banner
        set_roles(self.alpha, {"+0x78": [1]})
        self.assertEqual(self.entry()["+0x54"], PICKING)
        accept(beta, {})
        self.assertTrue(self.choice(1)["+0x68"])
        set_roles(beta, {"+0x78": [3]})
        self.assertEqual(self.entry()["+0x54"], SEARCHING)
        # Change Role in a group is followed by an empty list.
        change_roles(beta, {})
        set_roles(beta, {"+0x78": []})
        self.assertEqual(self.choice(1)["+0x8"], [])
        self.assertEqual(self.entry()["+0x54"], PICKING)

    def test_decline_takes_the_party_out_of_the_queue(self):
        beta = self.session("Beta")
        self.social.join(beta.account, self.social.party_of(self.alpha.account))
        enter_queue(self.alpha, ROLE_QUEUE)
        decline(beta, {})
        self.assertEqual(self.party_state()["+0x18"], [])
        for session in (self.alpha, beta):
            (left,) = self.messages(session, 44202)
            self.assertEqual(left, {"+0x78": ROLE_QUEUE["+0x78"], "+0xA8": 0})
            self.schemas.encode(QUEUE, 44202, left)

    def test_a_priority_pass_is_held_by_the_search_and_given_back(self):
        profile = self.alpha.account.profile
        profile.priority_passes = {"competitive": 2}
        enter_queue(self.alpha, ROLE_QUEUE)
        self.assertEqual(self.choice(0)["+0x0"], 2)
        # Ready: the roles, then the pass role. A solo search already started with the roles.
        set_roles(self.alpha, {"+0x78": [1]})
        pass_role(self.alpha, {"+0x78": [1]})
        self.assertEqual(self.choice(0)["+0x38"], [1])
        self.assertEqual(profile.priority_passes["competitive"], 1)
        (counts,) = self.messages(self.alpha, 58501, PASSES)
        self.assertIn({"+0x0": POOLS["competitive"], "+0x8": 1}, counts["+0x78"]["+0x0"])
        self.schemas.encode(PASSES, 58501, counts)
        cancel_queue(self.alpha, {"+0x78": ROLE_QUEUE["+0x78"]})
        self.assertEqual(profile.priority_passes["competitive"], 2)

    def test_no_pass_without_passes_or_for_a_role_not_chosen(self):
        enter_queue(self.alpha, ROLE_QUEUE)
        set_roles(self.alpha, {"+0x78": [1]})
        pass_role(self.alpha, {"+0x78": [1]})
        self.assertEqual(self.choice(0)["+0x38"], [])
        self.alpha.account.profile.priority_passes = {"competitive": 3}
        change_roles(self.alpha, {})
        set_roles(self.alpha, {"+0x78": [1]})
        pass_role(self.alpha, {"+0x78": [2]})
        self.assertEqual(self.choice(0)["+0x38"], [])
        # Tank takes no passes, even when chosen.
        change_roles(self.alpha, {})
        set_roles(self.alpha, {"+0x78": [2]})
        pass_role(self.alpha, {"+0x78": [2]})
        self.assertEqual(self.choice(0)["+0x38"], [])
        self.assertEqual(self.alpha.account.profile.priority_passes["competitive"], 3)

    def test_cancel_takes_the_others_out_too(self):
        beta = self.session("Beta")
        self.social.join(beta.account, self.social.party_of(self.alpha.account))
        enter_queue(self.alpha, ROLE_QUEUE)
        cancel_queue(self.alpha, {"+0x78": ROLE_QUEUE["+0x78"]})
        self.assertEqual(self.party_state()["+0x18"], [])
        self.assertEqual(self.messages(self.alpha, 44202), [])  # its client dropped the entry itself
        self.assertEqual(len(self.messages(beta, 44202)), 1)


if __name__ == "__main__":
    unittest.main()
