"""
Accounts, friends, parties and chat for several players on one lobby server.

Every typed name is an account with its own profile (profiles/<name>.json) and
a stable account id. Everyone on the server is friends with everyone else, and
a virtual friend ("Bot") is always online: it joins parties it is invited to
and answers in chat, so the social screens can be tried with one client.

Wire formats (data/schemas_174.txt):
  chat channel  {id16, +0x10 type, +0x14 index}. Types from the retail capture and
                the client's chat code: 3 Battle.net whisper (sent as 27004, not
                21700), 4 group (id = the party id), 7 General (the retail id below)
  chat member   {player record (0x68), +0x68 u32}
  20402 joined {channel} (retail: General after the player record, the group
        channel after the party state), 20403 joined [channels], 20404 left {channel},
  20401 user list {channel, members} (answer to 21701), 20400 message {channel, sender, text, flags}
  21700 (client) send {channel, text, flags}
  20700 party state, 20701 invite {inviter record, invitee record}
  22102 (client) invite {target}, 22103 answer {inviter, party, accept}, 22107 leave
"""

import os
import shutil
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

try:
    from storage import Profile, load_or_create_profile, save_profile
    from jam_codec import id16, clone
except ImportError:
    from .storage import Profile, load_or_create_profile, save_profile
    from .jam_codec import id16, clone

CHANNEL_WHISPER, CHANNEL_GROUP, CHANNEL_GENERAL = 3, 4, 7
GENERAL_CHANNEL_ID = (0x8B0C, 0xCCCC00000F995BE6)  # the retail server's General channel
BOT_NAME = "Bot"


def account_id_for(name: str) -> int:
    return 0x10000000 | (zlib.crc32(name.strip().lower().encode("utf-8")) & 0x0FFFFFFF)


def random_id16() -> tuple:
    r = lambda: int.from_bytes(os.urandom(8), "little")
    return r(), r()


@dataclass(eq=False)
class Account:
    name: str
    account_lo: int
    profile: Profile
    path: Path = None
    virtual: bool = False

    @property
    def account(self) -> dict:
        return id16(self.account_lo)

    def save(self):
        if self.path and not self.virtual:
            save_profile(self.profile, self.path)


@dataclass(eq=False)
class Party:
    party_id: tuple
    entity: tuple
    leader: Account
    members: list = field(default_factory=list)
    invites: dict = field(default_factory=dict)  # invitee account_lo -> inviter Account

    @property
    def chat_channel(self) -> dict:
        return {"+0x0": id16(*self.party_id), "+0x10": CHANNEL_GROUP, "+0x14": 0}


class Accounts:
    """Profiles by name; the legacy single profile seeds new accounts."""

    def __init__(self, profiles_dir: Path, template_path: Path):
        self.dir, self.template = profiles_dir, template_path
        self.dir.mkdir(parents=True, exist_ok=True)
        self.by_name = {}
        self.lock = threading.Lock()
        bot = Profile(player_name=BOT_NAME, level=250, endorsement_level=5)
        self.bot = Account(BOT_NAME, account_id_for(BOT_NAME), bot, virtual=True)

    def path_for(self, name: str) -> Path:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_ ").strip() or "player"
        return self.dir / f"{safe}.json"

    def get(self, name: str) -> Account:
        key = name.strip().lower()
        with self.lock:
            if key in self.by_name:
                return self.by_name[key]
            path = self.path_for(name)
            if not path.exists() and self.template.exists():
                shutil.copyfile(self.template, path)
            profile = load_or_create_profile(path)
            profile.player_name = name.strip()
            save_profile(profile, path)
            acc = Account(profile.player_name, account_id_for(profile.player_name), profile, path)
            self.by_name[key] = acc
            return acc

    def all_saved(self) -> list:
        names = {p.stem for p in self.dir.glob("*.json")}
        return sorted(names | {a.name for a in self.by_name.values()}, key=str.lower)

    def by_id(self, account_lo: int):
        if account_lo == self.bot.account_lo:
            return self.bot
        return next((a for a in self.by_name.values() if a.account_lo == account_lo), None)


class Social:
    """Online presence, parties and chat channels shared by every session."""

    def __init__(self, accounts: Accounts, content):
        self.accounts, self.content = accounts, content
        self.sessions = {}      # account_lo -> LobbySession (logged in)
        self.parties = {}       # account_lo -> Party
        self.general = {"+0x0": id16(*GENERAL_CHANNEL_ID), "+0x10": CHANNEL_GENERAL, "+0x14": 0}
        self.lock = threading.RLock()

    # ------------------------------------------------------------ presence

    def online(self) -> list:
        return [s.account for s in self.sessions.values()] + [self.accounts.bot]

    def friends_of(self, me: Account) -> list:
        return [a for a in self.online() if a.account_lo != me.account_lo]

    def player_record(self, acc: Account) -> dict:
        return self.content.player_record(acc.profile, identity_for(acc))

    def presence(self, acc: Account) -> list:
        return self.content.presence_records(acc.profile, acc.account_lo)

    def friends_state(self, me: Account) -> dict:
        now = int(time.time())
        friends = self.friends_of(me)
        return {
            "+0x78": [{"+0x0": f.account, "+0x10": now, "+0x18": 0} for f in friends],
            "+0x90": [],
            "+0xA8": [r for a in [me] + friends for r in self.presence(a)],
            "+0xC0": [],
        }

    # ------------------------------------------------------------ chat

    def member(self, acc: Account) -> dict:
        return {"+0x0": self.player_record(acc), "+0x68": 0}

    def who(self, channel: dict) -> dict:
        """20401: the channel's user list (the answer to the client's 21701)."""
        return {"+0x78": channel, "+0x90": [self.member(a) for a in self.channel_members(channel)]}

    def chat_message(self, channel: dict, sender: Account, text: str, flags: int = 0) -> dict:
        return {"+0x78": channel, "+0x90": self.member(sender), "+0x100": text, "+0x128": flags}

    def channel_members(self, channel: dict) -> list:
        if channel.get("+0x10") == CHANNEL_GROUP:
            party = next((p for p in self.parties.values() if id16(*p.party_id) == channel.get("+0x0")), None)
            return list(party.members) if party else []
        return self.online()

    # ------------------------------------------------------------ parties

    def party_of(self, acc: Account) -> Party:
        with self.lock:
            party = self.parties.get(acc.account_lo)
            if party is None:
                seq = int.from_bytes(os.urandom(4), "little")
                entity = ((seq | (0x1D << 40) | (1 << 56)), int.from_bytes(os.urandom(8), "little"))
                party = Party(random_id16(), entity, acc, [acc])
                self.parties[acc.account_lo] = party
            return party

    def party_state(self, party: Party) -> dict:
        state = self.content.party_state_for(
            [(m.profile, identity_for(m)) for m in party.members],
            party.party_id, party.entity)
        state["+0x78"]["+0x60"] = id16(*party.party_id)
        return state

    def invite(self, inviter: Account, target: Account) -> Party:
        party = self.party_of(inviter)
        party.invites[target.account_lo] = inviter
        return party

    def join(self, acc: Account, party: Party):
        with self.lock:
            old = self.parties.get(acc.account_lo)
            if old is not None and old is not party:
                self.leave(acc)
            if acc not in party.members:
                party.members.append(acc)
            party.invites.pop(acc.account_lo, None)
            self.parties[acc.account_lo] = party

    def leave(self, acc: Account) -> Party:
        """Remove acc from its party; returns the party the others stay in (or None)."""
        with self.lock:
            party = self.parties.pop(acc.account_lo, None)
            if party is None:
                return None
            if acc in party.members:
                party.members.remove(acc)
            if party.members and party.leader is acc:
                party.leader = party.members[0]
            return party if party.members else None


def identity_for(acc: Account):
    try:
        from content import Identity
    except ImportError:
        from .content import Identity
    return Identity(acc.account_lo, (acc.account_lo, 1), (0, 0), (0, 0))
