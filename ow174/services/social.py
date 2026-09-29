"""Friends, parties and chat for several players on one lobby server.

Everyone on the server is friends with everyone else, and a virtual friend ("Bot") is always online.
It joins parties it is invited to and answers in chat, so the social screens can be tried with one
client.

A chat channel is {"+0x0": id, "+0x10": type, "+0x14": index}. The types used here are 4 for a
party (the id is the party id) and 7 for General. Whispers (type 3) use their own messages.
"""

import os
import threading
import time
from dataclasses import dataclass, field

from ow174.accounts.registry import Account, Accounts
from ow174.content import Content, Identity
from ow174.jam.values import id16

CHANNEL_GROUP, CHANNEL_GENERAL = 4, 7
GENERAL_CHANNEL_ID = (0x8B0C, 0xCCCC00000F995BE6)  # the id the retail server used for General
# A party entity id carries this type and tag in its high bytes, like the one in Identity.create.
PARTY_ENTITY_TYPE = 0x1D << 40
ID_HIGH_TAG = 1 << 56


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
        return [account for account in self.online() if account.account_lo != me.account_lo]

    def player_record(self, account: Account) -> dict:
        identity = Identity.for_account(account.account_lo)
        return self._content.player.record(account.profile, identity)

    def presence(self, account: Account) -> list[dict]:
        return self._content.presence.records(account.profile, account.account_lo)

    def friends_state(self, me: Account) -> dict:
        """Message 27100: the friends list and everyone's presence, including the player's own."""
        now = int(time.time())
        friends = self.friends_of(me)
        friend_entries = []
        presence_records = list(self.presence(me))
        for friend in friends:
            friend_entries.append({"+0x0": friend.account, "+0x10": now, "+0x18": 0})
            presence_records += self.presence(friend)
        return {"+0x78": friend_entries, "+0x90": [], "+0xA8": presence_records, "+0xC0": []}

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
        for party in self.parties.values():
            if id16(*party.party_id) == channel.get("+0x0"):
                return list(party.members)
        return []

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
        members = []
        for member in party.members:
            members.append((member.profile, Identity.for_account(member.account_lo)))
        state = self._content.player.party_state_for(members, party.party_id, party.entity)
        state["+0x78"]["+0x60"] = id16(*party.party_id)
        return state

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
        """Remove the account from its party. Returns the party the others stay in, or None."""
        with self._lock:
            party = self.parties.pop(account.account_lo, None)
            if party is None:
                return None
            if account in party.members:
                party.members.remove(account)
            if party.members and party.leader is account:
                party.leader = party.members[0]
            return party if party.members else None
