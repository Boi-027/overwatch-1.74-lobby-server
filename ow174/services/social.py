"""Friends, parties and chat for several players on one lobby server.

Friends are added by BattleTag and saved in both profiles. A virtual friend ("Bot") is in every
friends list and always online; it joins parties it is invited to, so the social screens can be
tried with one client.

A chat channel is {"+0x0": id, "+0x10": type, "+0x14": index}. The types used here are 4 for a
party (the id is the party id) and 7 for General. Whispers (type 3) use their own messages.
"""

import os
import threading
from dataclasses import dataclass, field

from ow174.accounts.registry import Account, Accounts
from ow174.content import Content, Identity, passes
from ow174.content.presence import STATUS_AWAY, STATUS_BUSY, STATUS_OFFLINE, STATUS_ONLINE
from ow174.content.queue import group_slot, queue_entry, role_choice
from ow174.jam.values import id16

CHANNEL_GROUP, CHANNEL_GENERAL = 4, 7
GENERAL_CHANNEL_ID = (0x8B0C, 0xCCCC00000F995BE6)  # the id the retail server used for General
# A party entity id carries this type and tag in its high bytes, like the one in Identity.create.
PARTY_ENTITY_TYPE = 0x1D << 40
ID_HIGH_TAG = 1 << 56
# Group finder slot types (LFG enum table 0x7FF78B566690).
ANY_SLOT, TANK_SLOT, SUPPORT_SLOT, DAMAGE_SLOT = 1, 2, 3, 4
# Group finder filters (52200 +0x78), found by changing one filter at a time on the client's filter
# screen (05A/0668). The client does not filter the found groups itself. +0xC repeats the game type
# of +0x8 (1 quick play, 2 competitive in game); +0x0 u64 and +0x11 bool are unknown.
YES_NO_FILTERS = (("+0xD", "+0x77"), ("+0xE", "+0x78"))  # roles assigned, voice chat: 0 any, 1 no, 2 yes
FREE_SLOT_FILTERS = (("+0x12", TANK_SLOT), ("+0x13", DAMAGE_SLOT), ("+0x14", SUPPORT_SLOT))
# The party member portrait ring colour (party_member +0xE0): 5 online (green), 4 away (yellow),
# 6 busy (red), confirmed live. Anything else shows green.
PORTRAIT_STATUS = {STATUS_ONLINE: 5, STATUS_AWAY: 4, STATUS_BUSY: 6, STATUS_OFFLINE: 5}


def _random_u64() -> int:
    return int.from_bytes(os.urandom(8), "little")


def _random_id16() -> tuple[int, int]:
    return _random_u64(), _random_u64()


def _random_party_entity() -> tuple[int, int]:
    sequence = int.from_bytes(os.urandom(4), "little")
    return sequence | PARTY_ENTITY_TYPE | ID_HIGH_TAG, _random_u64()


@dataclass(eq=False)
class Party:
    party_id: tuple
    entity: tuple
    leader: Account
    members: list[Account] = field(default_factory=list)
    invites: dict[int, Account] = field(default_factory=dict)  # invitee account_lo -> inviter
    listing: dict | None = None  # the group finder entry the leader made (52201), None when not listed
    searching: bool = False  # the listing is open in the group finder (party state +0x9A)
    slot_types: dict[int, list[int]] = field(default_factory=dict)  # account_lo -> group slots taken (52203)
    queue: dict | None = None  # the key of the role queue the party is in, else None
    queue_state: int = 0  # the queue entry state (content.queue: PICKING, STARTING, SEARCHING)
    roles: dict[int, list[int]] = field(default_factory=dict)  # account_lo -> chosen role numbers
    accepted: set[int] = field(default_factory=set)  # account_lo of members on the role screen
    ready: set[int] = field(default_factory=set)  # account_lo of members who pressed Ready
    pass_roles: dict[int, int] = field(default_factory=dict)  # account_lo -> role a pass is used for
    passes_taken: set[int] = field(default_factory=set)  # account_lo whose pass this search holds
    merge_invites: set[int] = field(default_factory=set)  # account_lo of leaders asked to merge in (20703)

    @property
    def chat_channel(self) -> dict:
        return {"+0x0": id16(*self.party_id), "+0x10": CHANNEL_GROUP, "+0x14": 0}


class Social:
    """Online presence, parties and chat channels shared by every session."""

    def __init__(self, accounts: Accounts, content: Content) -> None:
        self.accounts = accounts
        self._content = content
        self.sessions: dict[int, object] = {}  # account_lo -> Session of a logged-in client
        self.parties: dict[int, Party] = {}  # account_lo -> Party
        self.general = {"+0x0": id16(*GENERAL_CHANNEL_ID), "+0x10": CHANNEL_GENERAL, "+0x14": 0}
        self._lock = threading.RLock()

    # --- presence ------------------------------------------------------------------------------

    def online(self) -> list[Account]:
        accounts = []
        for session in self.sessions.values():
            accounts.append(session.account)
        accounts.append(self.accounts.bot)
        return accounts

    def friends_of(self, me: Account) -> list[Account]:
        """The bot and the saved accounts in the player's friends list."""
        saved = {name.lower(): name for name in self.accounts.all_saved()}
        friends = [self.accounts.bot]
        for name in me.profile.friends:
            if name.lower() in saved:
                friends.append(self.accounts.get(saved[name.lower()]))
        return friends

    def player_record(self, account: Account) -> dict:
        identity = Identity.for_account(account.account_lo)
        return self._content.player.record(account.profile, identity)

    def offline_presence(self, me: Account) -> list[dict]:
        """Presence records of the player's friends who are offline."""
        records = []
        for friend in self.friends_of(me):
            if not self.is_online(friend):
                records += self.presence(friend)
        return records

    def presence(self, account: Account) -> list[dict]:
        return self._content.presence.records(
            account.profile, account.account_lo, self.effective_status(account), account.created
        )

    def is_online(self, account: Account) -> bool:
        return account.virtual or account.account_lo in self.sessions

    def effective_status(self, account: Account) -> int:
        """How friends see the account: offline when not connected, else its chosen status (which
        may be appear-offline). The bot is always online."""
        if account.virtual:
            return STATUS_ONLINE
        if account.account_lo not in self.sessions:
            return STATUS_OFFLINE
        return account.status

    def set_status(self, account: Account, status: int) -> None:
        account.status = status

    def friends_state(self, me: Account) -> dict:
        """Message 27100: friends, incoming friend requests, and presence.

        Offline friends get presence too, marked offline: without it the client leaves them out.
        """
        friend_entries = []
        presence_records = list(self.presence(me))
        for friend in self.friends_of(me):
            # +0x10 is not a time: with the current time in it every friend showed as a favorite.
            friend_entries.append({"+0x0": friend.account, "+0x10": 0, "+0x18": 0})
            presence_records += self.presence(friend)
        return {
            "+0x78": friend_entries,
            "+0x90": self._requests_for(me),
            "+0xA8": presence_records,
            "+0xC0": [],
        }

    def _requests_for(self, me: Account) -> list[dict]:
        """Incoming friend requests as 27100 invitation records."""
        return [self.request_record(me, self.accounts.get(name)) for name in me.profile.friend_requests]

    @staticmethod
    def request_record(me: Account, inviter: Account) -> dict:
        """One friend request to `me` (27100 +0x90, 27107)."""
        return {
            "+0x0": inviter.account,
            "+0x10": me.account,
            "+0x20": inviter.account_lo,  # the request id: one request per inviter
            "+0x28": 0,
            "+0x30": inviter.battle_tag,
            "+0x58": me.battle_tag,
        }

    # --- friend requests -----------------------------------------------------------------------

    def request_friend(self, me: Account, tag: str) -> tuple[Account | None, str]:
        """Ask the owner of a BattleTag to be friends. Returns (target, outcome): "sent", "added"
        (they had asked first), "pending" (asked before), or why it failed: "unknown", "self",
        "already" (the bot is always a friend)."""
        target = self.accounts.by_battle_tag(tag)
        if target is None:
            return None, "unknown"
        if target.account_lo == me.account_lo:
            return target, "self"
        if target in self.friends_of(me):
            return target, "already"
        with self._lock:
            if me.name in target.profile.friend_requests:
                return target, "pending"
            if target.name in me.profile.friend_requests:
                # They asked first: a request back means yes.
                self.accept_friend(me, target)
                return target, "added"
            target.profile.friend_requests.append(me.name)
            target.save()
        return target, "sent"

    def accept_friend(self, me: Account, inviter: Account) -> None:
        """Make two accounts friends and drop the request between them."""
        with self._lock:
            for account, other in ((me, inviter), (inviter, me)):
                profile = account.profile
                profile.friend_requests = [
                    n for n in profile.friend_requests if n.lower() != other.name.lower()
                ]
                if other.name not in profile.friends:
                    profile.friends.append(other.name)
                account.save()

    def decline_friend(self, me: Account, inviter: Account) -> None:
        with self._lock:
            me.profile.friend_requests = [
                n for n in me.profile.friend_requests if n.lower() != inviter.name.lower()
            ]
            me.save()

    def remove_friend(self, me: Account, friend: Account) -> None:
        with self._lock:
            for account, other in ((me, friend), (friend, me)):
                account.profile.friends = [
                    n for n in account.profile.friends if n.lower() != other.name.lower()
                ]
                account.save()

    # --- chat ----------------------------------------------------------------------------------

    def member(self, account: Account) -> dict:
        return {"+0x0": self.player_record(account), "+0x68": 0}

    def who(self, channel: dict) -> dict:
        """Message 20401: the channel's member list, the answer to the client's 21701."""
        members = [self.member(account) for account in self.channel_members(channel)]
        return {"+0x78": channel, "+0x90": members}

    def chat_message(self, channel: dict, sender: Account, text: str, flags: int = 0) -> dict:
        return {"+0x78": channel, "+0x90": self.member(sender), "+0x100": text, "+0x128": flags}

    def channel_members(self, channel: dict) -> list[Account]:
        if channel.get("+0x10") != CHANNEL_GROUP:
            return self.online()
        party = self.party_by_id(channel.get("+0x0"))
        return list(party.members) if party else []

    # --- parties -------------------------------------------------------------------------------

    def party_of(self, account: Account) -> Party:
        """The account's party, creating a party of one on first use."""
        with self._lock:
            party = self.parties.get(account.account_lo)
            if party is None:
                party = Party(_random_id16(), _random_party_entity(), account, [account])
                self.parties[account.account_lo] = party
            return party

    def party_state(self, party: Party) -> dict:
        """20700 for the party, with the role queue entry and choices while it is in one."""
        members = []
        for member in party.members:
            members.append((member.profile, Identity.for_account(member.account_lo)))
        state = self._content.player.party_state_for(members, party.party_id, party.entity)
        state["+0x78"]["+0x60"] = id16(*party.party_id)
        # The member portrait rings show each member's status (online/away/busy).
        for record, member in zip(state["+0x78"]["+0x0"], party.members, strict=True):
            record["+0xE0"] = PORTRAIT_STATUS.get(self.effective_status(member), 5)
        if party.listing is not None:
            state["+0x78"]["+0x30"] = [party.listing]
            # +0x9A on: the group is looking for players. The client then refreshes its search
            # (0x7FF78975F2A0), and the leader's panel button reads "Done" and closes the search
            # with 52202 (graphs 01B/164B, 01B/1661). Off, the panel offers "Find more players".
            state["+0x78"]["+0x9A"] = party.searching
            for record, member in zip(state["+0x78"]["+0x0"], party.members, strict=True):
                slot_types = party.slot_types.get(member.account_lo)
                if slot_types:
                    record["+0xA0"] = [group_slot(slot_types)]
        if party.queue is not None:
            state["+0x78"]["+0x18"] = [queue_entry(party.queue, party.queue_state)]
            pool = self.pass_pool(party)
            for record, member in zip(state["+0x78"]["+0x0"], party.members, strict=True):
                lo = member.account_lo
                choice = role_choice(
                    party.roles.get(lo, []),
                    lo in party.accepted,
                    lo in party.ready,
                    passes.count(member.profile, pool),
                    party.pass_roles.get(lo, 0),
                )
                record["+0xC8"] = [choice]
        return state

    # --- group finder --------------------------------------------------------------------------

    def group(self, party: Party) -> dict:
        """A listed party as the group finder shows it (52300): its party state."""
        return self.party_state(party)["+0x78"]

    def listed_groups(self) -> list[Party]:
        """Parties whose listing is open in the group finder."""
        with self._lock:
            parties = dict.fromkeys(self.parties.values())  # one entry per party, not per member
        return [party for party in parties if party.listing is not None and party.searching]

    def free_slot_types(self, party: Party) -> list[int]:
        """The listed group's slots nobody fills yet, as slot types."""
        free = list(party.listing.get("+0x80", [])) if party.listing else []
        for member in party.members:
            taken = party.slot_types.get(member.account_lo)
            if taken and taken[0] in free:
                free.remove(taken[0])
        return free

    def pass_pool(self, party: Party) -> int:
        """The priority pass pool of the queue the party is in (content/passes.py)."""
        return self._content.arcade.pass_pool(party.queue["+0x0"]["+0x0"])

    def matches_filters(self, party: Party, wanted: dict) -> bool:
        """Whether a listed group passes the filters of a search: +0x8 game type, +0xD roles
        assigned, +0xE voice chat, +0xF minimum endorsement the group asks for, +0x10 minimum
        players, +0x12/+0x13/+0x14 free tank/damage/support slots, +0x15 free slots of any role,
        +0x18 slot types that must be free, +0x30 texts to find in the name or the creator."""
        listing = party.listing or {}
        game_type = wanted.get("+0x8", 0)
        if game_type and listing.get("+0x76", 0) not in (0, game_type):
            return False
        for key, flag in YES_NO_FILTERS:
            if wanted.get(key, 0) and bool(listing.get(flag)) != (wanted[key] == 2):
                return False
        if listing.get("+0x70", 0) < wanted.get("+0xF", 0) or len(party.members) < wanted.get("+0x10", 0):
            return False
        free = self.free_slot_types(party)
        for key, slot_type in FREE_SLOT_FILTERS:
            if sum(1 for free_type in free if free_type in (slot_type, ANY_SLOT)) < wanted.get(key, 0):
                return False
        if len(free) < wanted.get("+0x15", 0):
            return False
        if not all(slot_type in free or ANY_SLOT in free for slot_type in wanted.get("+0x18", [])):
            return False
        creator = listing.get("+0x0", {}).get("+0x40", "").split("#")[0]
        names = f"{listing.get('+0xA0', '')} {creator}".casefold()
        return all(entry.get("+0x0", "").casefold() in names for entry in wanted.get("+0x30", []))

    def update_search(self, party: Party) -> None:
        """A full group stops looking for players; the client then offers to play (0x7FF78934B390)."""
        if party.listing is not None and not self.free_slot_types(party):
            party.searching = False

    def take_free_slot(self, party: Party, account: Account) -> None:
        """A member joining a listed group takes its first free slot."""
        free = self.free_slot_types(party)
        if free:
            party.slot_types[account.account_lo] = [free[0]]
        self.update_search(party)

    def bot_listing(self, name: str, queue_card: int, game_type: int, slot_types: list[int]) -> dict:
        """A group finder listing (52201) of the bot, shaped like the ones the client makes: +0x0
        the leader's card, +0x68 the queue card, +0x76 the game type, +0x80 the slot types."""
        return {
            "+0x0": self.player_record(self.accounts.bot),
            "+0x68": queue_card,
            "+0x70": 1,
            "+0x74": 150,
            "+0x76": game_type,
            "+0x77": True,
            "+0x78": False,
            "+0x79": True,
            "+0x80": list(slot_types),
            "+0x98": False,
            "+0xA0": name,
        }

    def list_bot_group(self, listing: dict) -> Party:
        """The bot leads a listed group of its own, so the group finder has someone else's group."""
        bot = self.accounts.bot
        with self._lock:
            party = self.parties.get(bot.account_lo)
            if party is not None and party.leader is not bot:
                self.leave(bot)
            party = self.party_of(bot)
            party.listing = listing
            party.searching = True
            party.slot_types = {}
            self.take_free_slot(party, bot)
            return party

    def merge_into(self, party: Party, into: Party) -> None:
        """Every member of a party moves into a listed group and takes a free slot."""
        with self._lock:
            for member in list(party.members):
                self.join(member, into)
                self.take_free_slot(into, member)

    def remove_bot_group(self) -> Party | None:
        """The bot leaves whatever group it is in. Returns that group (closed when the bot was alone)."""
        return self.leave(self.accounts.bot)

    def party_by_id(self, party_id: dict) -> Party | None:
        with self._lock:
            for party in self.parties.values():
                if id16(*party.party_id) == party_id:
                    return party
        return None

    def invite(self, inviter: Account, target: Account) -> Party:
        party = self.party_of(inviter)
        party.invites[target.account_lo] = inviter
        return party

    def join(self, account: Account, party: Party) -> None:
        with self._lock:
            old = self.parties.get(account.account_lo)
            if old is not None and old is not party:
                self.leave(account)
            if account not in party.members:
                party.members.append(account)
            party.invites.pop(account.account_lo, None)
            self.parties[account.account_lo] = party

    def leave(self, account: Account) -> Party | None:
        """Remove the account from its party and return that party, or None when it had none. A
        party left empty closes its group finder listing, so notify_party tells the players who
        picked it."""
        with self._lock:
            party = self.parties.pop(account.account_lo, None)
            if party is None:
                return None
            if account in party.members:
                party.members.remove(account)
            party.slot_types.pop(account.account_lo, None)
            if party.members and party.leader is account:
                party.leader = party.members[0]
            if not party.members:
                party.listing = None
                party.searching = False
            return party
