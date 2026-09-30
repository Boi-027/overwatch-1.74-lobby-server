"""The group finder: a listed group (52201) looks for players and shows up in searches
(52200 -> 52300) until its leader stops the search (52202)."""

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
from ow174.content.ranked import COMPETITIVE_CTF, LUCIO_CUP
from ow174.jam.codec import Schemas
from ow174.jam.groups import GROUP_FINDER, GROUPS, LOBBY, PARTY
from ow174.jam.values import id16
from ow174.lobby.handlers.matchmaking import (
    close_group,
    find_groups,
    group_roles,
    list_group,
    unwatch_groups,
    watch_groups,
)
from ow174.lobby.handlers.party import accept_merge, invite, invite_group, join_group, merge_request
from ow174.lobby.server import LobbyServer
from ow174.services.social import Social


class GroupFinderTests(unittest.TestCase):
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
        self.closed = []
        self.server = SimpleNamespace(
            social=self.social,
            notify_party=self.notified.append,
            notify_watchers=self.closed.append,
            session_of=lambda account_lo: self.social.sessions.get(account_lo),
        )

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
        return session, sent

    def list(self, leader, name="Tanks wanted"):
        listing = self.schemas.empty(GROUP_FINDER, 52201)
        listing["+0x78"]["+0xA0"] = name
        listing["+0x78"]["+0x80"] = [1, 1]
        list_group(leader, listing)
        return self.social.party_of(leader.account)

    def search(self, session, sent):
        sent.clear()
        find_groups(session, {"+0x78": {}})
        ((crc, msg_id, found),) = sent
        self.assertEqual((crc, msg_id), (GROUPS, 52300))
        self.schemas.encode(GROUPS, 52300, found)
        return [group["+0x30"][0]["+0xA0"] for group in found["+0x78"]]

    def test_a_listed_group_is_found_until_its_search_stops(self):
        leader, leader_sent = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        party = self.list(leader)
        # 52301 only refreshes a group the client found in a search; the leader gets the party state.
        self.assertEqual(leader_sent, [])
        state = self.social.party_state(party)
        self.assertEqual(state["+0x78"]["+0x30"][0]["+0xA0"], "Tanks wanted")
        self.assertTrue(state["+0x78"]["+0x9A"])
        self.schemas.encode(PARTY, 20700, state)

        self.assertEqual(self.search(seeker, seeker_sent), ["Tanks wanted"])
        # The leader finds the own group too; the client turns Join off for it.
        self.assertEqual(self.search(leader, leader_sent), ["Tanks wanted"])

        # A leader who is alone leaves the group finder.
        close_group(leader, {})
        self.assertIsNone(party.listing)
        self.assertEqual(self.social.party_state(party)["+0x78"]["+0x30"], [])
        self.assertEqual(self.search(seeker, seeker_sent), [])

    def test_search_filters_pick_the_groups(self):
        leader, _ = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        party = self.list(leader)  # "Tanks wanted", two free slots of any role, quick play
        party.listing.update({"+0x76": 1, "+0x77": False, "+0x70": 2})
        seeker.account.profile.endorsement_level = 2  # meets the group's own requirement

        def found(**filters):
            wanted = self.schemas.empty(GROUP_FINDER, 52200)["+0x78"]
            wanted.update({f"+0x{key[1:].upper()}": value for key, value in filters.items()})
            seeker_sent.clear()
            find_groups(seeker, {"+0x78": wanted})
            ((_, _, value),) = seeker_sent
            return len(value["+0x78"])

        self.assertEqual(found(), 1)
        self.assertEqual(found(x8=1, xC=1), 1)  # quick play
        self.assertEqual(found(x8=2, xC=2), 0)  # competitive
        party.listing["+0x76"] = 4  # arcade: row 3 in the type list, code 4 in the listing (seen in game)
        self.assertEqual(found(x8=3, xC=4), 1)
        party.listing["+0x76"] = 1
        self.assertEqual(found(xD=1), 1)  # roles assigned: no
        self.assertEqual(found(xD=2), 0)  # roles assigned: yes
        self.assertEqual(found(xF=2), 1)  # the group asks for endorsement 2
        self.assertEqual(found(xF=3), 0)
        self.assertEqual(found(x10=2), 0)  # at least two players
        self.assertEqual(found(x12=2), 1)  # two free slots a tank can take
        self.assertEqual(found(x12=3), 0)
        self.assertEqual(found(x18=[4]), 1)  # a free damage slot
        self.assertEqual(found(x30=[{"+0x0": "TANKS"}]), 1)
        self.assertEqual(found(x30=[{"+0x0": "healers"}]), 0)

    def test_a_picked_group_sends_its_changes_until_it_closes(self):
        leader, _ = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        seeker.watched_groups = set()
        self.social.sessions[seeker.account.account_lo] = seeker
        party = self.list(leader)
        group_roles(leader, {"+0x78": [2]})
        party_id = id16(*party.party_id)
        watch_groups(seeker, {"+0x78": [party_id]})

        LobbyServer.notify_watchers(self.server, party)
        ((crc, msg_id, value),) = seeker_sent
        self.assertEqual((crc, msg_id), (GROUPS, 52301))
        self.schemas.encode(GROUPS, 52301, value)

        seeker_sent.clear()
        close_group(leader, {})
        LobbyServer.notify_watchers(self.server, party)
        self.assertEqual(seeker_sent, [(GROUPS, 52302, {"+0x78": party_id})])
        self.assertEqual(seeker.watched_groups, set())

    def test_a_group_left_empty_closes_for_the_players_who_picked_it(self):
        seeker, seeker_sent = self.session("Beta")
        seeker.watched_groups = set()
        self.social.sessions[seeker.account.account_lo] = seeker
        party = self.social.list_bot_group(
            self.social.bot_listing("Bot's group", 0x06300000000000ED, 1, [2, 2])
        )
        watch_groups(seeker, {"+0x78": [id16(*party.party_id)]})
        self.assertIs(self.social.remove_bot_group(), party)
        self.assertIsNone(party.listing)
        LobbyServer.notify_watchers(self.server, party)
        self.assertEqual(seeker_sent, [(GROUPS, 52302, {"+0x78": id16(*party.party_id)})])

    def test_an_unpicked_group_sends_nothing(self):
        leader, _ = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        seeker.watched_groups = set()
        self.social.sessions[seeker.account.account_lo] = seeker
        party = self.list(leader)
        watch_groups(seeker, {"+0x78": [id16(*party.party_id)]})
        unwatch_groups(seeker, {"+0x78": [id16(*party.party_id)]})
        LobbyServer.notify_watchers(self.server, party)
        self.assertEqual(seeker_sent, [])

    def test_a_new_group_is_sent_with_the_leaders_slot(self):
        # Creating a group sends 52201, then the leader's slot types (52203). A party state between
        # them would open "choose role(s)" for a leader who already picked a role.
        leader, _ = self.session("Alpha")
        party = self.list(leader)
        self.assertEqual(self.notified, [])
        group_roles(leader, {"+0x78": [1]})
        self.assertEqual(self.notified, [party])
        (slot,) = self.social.party_state(party)["+0x78"]["+0x0"][0]["+0xA0"]
        self.assertEqual(slot["+0x10"], [1])
        # Editing the listing sends no slot types, so the party state goes out at once.
        self.list(leader, "Edited")
        self.assertEqual(self.notified, [party, party])

    def bot_group(self, slots=(2, 2, 4, 4, 3, 3)):
        listing = self.social.bot_listing("Bot's group", 0x06300000000000ED, 1, list(slots))
        return self.social.list_bot_group(listing)

    def test_the_bot_group_is_found_and_joined(self):
        seeker, seeker_sent = self.session("Beta")
        group = self.bot_group()
        bot = self.accounts.bot
        self.assertEqual(group.slot_types[bot.account_lo], [2])  # the leader takes the first slot
        self.assertEqual(self.search(seeker, seeker_sent), ["Bot's group"])
        # Join in the group finder: no inviter, the group's party id in +0x88.
        join_group(seeker, {"+0x78": {"+0x0": 0, "+0x8": 0}, "+0x88": id16(*group.party_id), "+0x98": True})
        self.assertIs(self.social.party_of(seeker.account), group)
        self.assertEqual(group.members, [bot, seeker.account])
        self.assertEqual(self.notified, [group])

    def test_invite_group_brings_the_bot_group_in(self):
        leader, _ = self.session("Alpha")
        party = self.list(leader)
        group_roles(leader, {"+0x78": [1]})
        group = self.bot_group()
        invite_group(leader, {"+0x78": {"+0x0": 0, "+0x8": 0}, "+0x88": id16(*group.party_id)})
        bot = self.accounts.bot
        self.assertIs(self.social.party_of(bot), party)
        self.assertEqual(party.slot_types[bot.account_lo], [1])
        # Two slots, both filled: the group stops looking and offers to play.
        self.assertFalse(party.searching)
        self.assertEqual(self.social.listed_groups(), [])
        # The bot's group is left empty, so it closes for the players who picked it.
        self.assertEqual(self.closed, [group])
        self.assertIsNone(group.listing)

    def test_invite_group_asks_a_players_leader_and_yes_merges(self):
        leader, _ = self.session("Alpha")
        other, other_sent = self.session("Gamma")
        self.social.sessions[other.account.account_lo] = other
        party = self.list(leader)
        theirs = self.list(other, "Healers")
        invite_group(leader, {"+0x78": {"+0x0": 0, "+0x8": 0}, "+0x88": id16(*theirs.party_id)})
        ((crc, msg_id, request),) = other_sent
        self.assertEqual((crc, msg_id), (PARTY, 20703))
        self.assertEqual(request["+0x78"]["+0x0"], leader.account.account)  # "Alpha wants to merge groups"
        self.schemas.encode(PARTY, 20703, request)
        self.assertEqual(party.members, [leader.account])  # nothing moves before the answer
        accept_merge(other, {"+0x78": id16(*party.party_id)})  # Yes names the asking group
        self.assertEqual(party.members, [leader.account, other.account])
        self.assertEqual(self.closed, [theirs])  # their group closes for the players who picked it

    def test_groups_too_big_together_are_not_asked(self):
        leader, _ = self.session("Alpha")
        other, other_sent = self.session("Gamma")
        self.social.sessions[other.account.account_lo] = other
        party = self.list(leader)
        theirs = self.list(other, "Healers")
        for name in ("B", "C", "D"):
            self.social.join(self.accounts.get(name), party)
        for name in ("E", "F"):
            self.social.join(self.accounts.get(name), theirs)
        invite_group(leader, {"+0x78": {"+0x0": 0, "+0x8": 0}, "+0x88": id16(*theirs.party_id)})
        self.assertEqual(other_sent, [])
        self.assertEqual(party.merge_invites, set())

    def test_a_group_asking_a_higher_endorsement_is_hidden_and_refused(self):
        leader, leader_sent = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        party = self.list(leader)
        party.listing["+0x70"] = 3  # the seeker has 1; the client shows it and lets them join
        self.assertEqual(self.search(seeker, seeker_sent), [])
        self.assertEqual(self.search(leader, leader_sent), ["Tanks wanted"])  # the leader still sees it
        join_group(seeker, {"+0x78": id16(0, 0), "+0x88": id16(*party.party_id)})
        self.assertEqual(party.members, [leader.account])

    def test_a_lucio_cup_group_is_found_by_its_card_and_spread(self):
        # Game type 3 is a competitive Arcade card; the search names the card in +0x0.
        leader, _ = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        party = self.list(leader)
        party.listing.update({"+0x76": 3, "+0x68": LUCIO_CUP, "+0x74": 150})
        leader.account.profile.ratings = {"lucio": 2500}
        seeker.account.profile.ratings = {"lucio": 2600, "tank": 4000}

        def search(card):
            seeker_sent.clear()
            find_groups(seeker, {"+0x78": {"+0xC": 3, "+0x0": card}})
            return [group["+0x30"][0]["+0xA0"] for group in seeker_sent[0][2]["+0x78"]]

        self.assertEqual(search(LUCIO_CUP), ["Tanks wanted"])
        self.assertEqual(search(COMPETITIVE_CTF), [])
        seeker.account.profile.ratings["lucio"] = 2700  # the spread counts the Lucio Cup rating
        self.assertEqual(search(LUCIO_CUP), [])

    def test_a_competitive_group_takes_players_within_its_rating_spread(self):
        leader, _ = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        party = self.list(leader)
        party.listing.update({"+0x76": 2, "+0x74": 150})  # competitive, +/- 150
        leader.account.profile.ratings = {"tank": 2500, "damage": 2100, "support": 2000}
        seeker.account.profile.ratings = {"tank": 1800, "damage": 2600, "support": 2400}
        self.assertEqual(self.search(seeker, seeker_sent), ["Tanks wanted"])  # 2600 vs 2500
        seeker.account.profile.ratings["damage"] = 2700
        self.assertEqual(self.search(seeker, seeker_sent), [])
        join_group(seeker, {"+0x78": id16(0, 0), "+0x88": id16(*party.party_id)})
        self.assertEqual(party.members, [leader.account])

    def test_a_member_who_may_not_invite_suggests_the_player_to_the_leader(self):
        leader, leader_sent = self.session("Alpha")
        member, member_sent = self.session("Beta")
        self.social.sessions[leader.account.account_lo] = leader
        party = self.social.party_of(leader.account)  # the switch is a Social option: any party
        self.social.join(member.account, party)
        self.assertTrue(self.social.party_state(party)["+0x78"]["+0x94"])  # on until saved
        leader.account.profile.settings["+0x10D"] = {"+0x0": False}  # the leader's switch, saved by 22202
        self.assertFalse(self.social.party_state(party)["+0x78"]["+0x94"])  # the members see no "Invite"
        invite(member, {"+0x78": self.accounts.bot.account})
        self.assertNotIn(self.accounts.bot, party.members)
        ((crc, msg_id, suggestion),) = leader_sent
        self.assertEqual((crc, msg_id), (PARTY, 20701))  # "Beta suggests the bot", with Invite
        self.assertEqual(suggestion["+0x78"]["+0x0"], member.account.account)
        self.assertEqual(suggestion["+0xE0"]["+0x0"], self.accounts.bot.account)
        self.schemas.encode(PARTY, 20701, suggestion)
        self.assertEqual(member_sent, [])
        invite(leader, {"+0x78": self.accounts.bot.account})  # Invite on the suggestion
        self.assertIn(self.accounts.bot, party.members)

    def test_a_suggestion_the_leader_cannot_get_shows_an_error(self):
        leader, _ = self.session("Alpha")
        member, member_sent = self.session("Beta")
        party = self.social.party_of(leader.account)
        self.social.join(member.account, party)
        leader.account.profile.settings["+0x10D"] = {"+0x0": False}
        invite(member, {"+0x78": self.accounts.bot.account})
        self.assertEqual(member_sent, [(LOBBY, 20803, {"+0x78": 14})])  # "Unable to suggest the player"
        self.schemas.encode(LOBBY, 20803, member_sent[0][2])

    def test_the_bot_invited_into_a_listed_group_takes_a_slot(self):
        # Without a slot its party tile shows the group finder icon ("no role picked").
        leader, _ = self.session("Alpha")
        party = self.list(leader)
        invite(leader, {"+0x78": self.accounts.bot.account})
        self.assertEqual(party.slot_types[self.accounts.bot.account_lo], [1])

    def test_yes_on_a_merge_request_moves_the_party_into_the_bot_group(self):
        player, _ = self.session("Alpha")
        group = self.bot_group()
        bot = self.accounts.bot
        request = merge_request(self.social, bot, group)
        self.schemas.encode(PARTY, 20703, request)
        # Unasked, a yes does nothing.
        accept_merge(player, {"+0x78": id16(*group.party_id)})
        self.assertIsNot(self.social.party_of(player.account), group)
        group.merge_invites.add(player.account.account_lo)
        mine = self.social.party_of(player.account)
        accept_merge(player, {"+0x78": id16(*group.party_id)})
        self.assertIs(self.social.party_of(player.account), group)
        self.assertEqual(self.closed, [mine])
        self.assertEqual(group.slot_types[player.account.account_lo], [2])  # the second tank slot
        self.assertEqual(group.merge_invites, set())

    def test_a_group_with_players_keeps_its_listing_when_the_search_stops(self):
        leader, _ = self.session("Alpha")
        seeker, seeker_sent = self.session("Beta")
        party = self.list(leader)
        self.social.join(self.accounts.get("Gamma"), party)
        close_group(leader, {})
        state = self.social.party_state(party)
        self.assertEqual(state["+0x78"]["+0x30"][0]["+0xA0"], "Tanks wanted")
        self.assertFalse(state["+0x78"]["+0x9A"])
        self.assertEqual(self.search(seeker, seeker_sent), [])
        self.list(leader)
        self.assertEqual(self.search(seeker, seeker_sent), ["Tanks wanted"])

    def test_a_member_gets_a_slot_for_the_chosen_slot_types(self):
        # Right after creating the group the client sends no slot types, and asks the leader for
        # them until the party state gives the leader a slot.
        leader, _ = self.session("Alpha")
        listing = self.schemas.empty(GROUP_FINDER, 52201)
        listing["+0x78"]["+0x80"] = [1, 1, 1, 1, 1, 1]
        list_group(leader, listing)
        group_roles(leader, {"+0x78": []})
        party = self.social.party_of(leader.account)
        self.assertEqual(self.social.party_state(party)["+0x78"]["+0x0"][0]["+0xA0"], [])
        group_roles(leader, {"+0x78": [1, 9]})
        state = self.social.party_state(party)
        (slot,) = state["+0x78"]["+0x0"][0]["+0xA0"]
        self.assertEqual((slot["+0x8"], slot["+0x10"]), (1, [1]))
        decoded = self.schemas.decode(PARTY, 20700, self.schemas.encode(PARTY, 20700, state))
        self.assertEqual(decoded["+0x78"]["+0x0"][0]["+0xA0"][0]["+0x8"], 1)
        close_group(leader, {})
        self.assertEqual(self.social.party_state(party)["+0x78"]["+0x0"][0]["+0xA0"], [])


if __name__ == "__main__":
    unittest.main()
