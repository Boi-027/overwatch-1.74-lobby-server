"""Matchmaking: parties that search a queue are put into full matches, and their games go to the
game server.

A queue card's rules (data/game_queues_174.json, tools/extract_game_queues.py) give its teams, the
players per team, the role slots of a role queue (two of each role per team) and its maps. A party
always plays on one team. A match starts once the waiting parties fill every team exactly and, in a
role queue, every player gets one of the roles they picked. Parties that waited longest go first.
For tests, a minimum number of players starts a match early with whoever is waiting.

What the client needs (read in IDA):
- 44202 {queue key, reason} drops the client's queue entry and stops the search timer. Every reason
  does that; reason 1 (QueuePopped) only differs in telemetry (0x7FF7896190D0). It goes first, or the
  menu keeps showing the search.
- 20600 then sends the game to the game server at once; its handler (0x7FF789685CD0) has no queue or
  menu check. +0x78 must be False: True means the struct is XOR-scrambled, and the address the client
  then reads is garbage (why our first try of 20600 never connected). The client dials the host text
  (+0x2E) and port (+0x2C), puts the connection id (+0x28) in every datagram header, and encrypts with
  the key at +0xAE; the server encrypts with the one at +0xCE. host2 (+0x6E) is only shown.
"""

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field

from ow174.game.content import PRACTICE_RANGE, game_map
from ow174.jam.groups import CHAT_IN, HANDOFF, QUEUE
from ow174.paths import DATA_DIR
from ow174.services.social import Party

log = logging.getLogger("ow174.lobby")

QUEUES_PATH = DATA_DIR / "game_queues_174.json"
QUEUE_POPPED = 1
ANY_ROLE = (2, 1, 3)  # tank, damage, support: what "All roles" picks
ROLE_NUMBERS = {0x0D80000000000850: 2, 0x0D8000000000084E: 1, 0x0D80000000000851: 3}  # role GUID -> number
SEARCH_LIMIT = 20000  # roster search steps per attempt
QUICK_PLAY = 0x06300000000000ED


@dataclass(frozen=True)
class Ruleset:
    """One way a card is played: its game mode and the players each team takes."""

    mode: int = 0
    mode_name: str = ""
    team_sizes: tuple[int, ...] = (6, 6)  # per team; free for all: one entry, the number of players
    free_for_all: bool = False


@dataclass(frozen=True)
class QueueRules:
    name: str = ""
    rulesets: tuple[Ruleset, ...] = (Ruleset(),)
    roles: dict = field(default_factory=dict)  # role number -> slots per team; empty = any roles
    maps: tuple = ()  # (map GUID, the modes it is played in)


def load_rules(path=QUEUES_PATH) -> dict[int, QueueRules]:
    """The queue cards' rules (data/game_queues_174.json, tools/extract_game_queues.py)."""
    if not path.is_file():
        return {}
    cards = json.loads(path.read_text(encoding="utf-8")).get("cards", {})
    return {int(key, 16): _card_rules(entry) for key, entry in cards.items() if entry.get("rulesets")}


def _card_rules(entry: dict) -> QueueRules:
    roles = {}
    for guid, count in (entry.get("roles") or {}).items():
        number = ROLE_NUMBERS.get(int(guid, 16))
        if number and int(count) > 0:
            roles[number] = int(count)
    maps = tuple(
        (int(item["map"], 16), tuple(int(mode, 16) for mode in item.get("modes") or []))
        for item in entry.get("maps") or []
    )
    rulesets = tuple(_ruleset(item) for item in entry["rulesets"])
    return QueueRules(entry.get("name") or "", rulesets, roles, maps)


def _ruleset(item: dict) -> Ruleset:
    free_for_all = bool(item.get("free_for_all"))
    if free_for_all:
        sizes = (int(item.get("players") or 8),)
    else:
        by_team = item.get("team_sizes") or {}
        sizes = tuple(int(by_team[team]) for team in ("blue", "red") if team in by_team) or (6, 6)
    return Ruleset(int(item["mode"], 16), item.get("mode_name") or "", sizes, free_for_all)


@dataclass
class Ticket:
    party: Party
    key: dict  # the queue key of the party's 44100
    card: int  # the queue card GUID
    since: float
    members: list = field(default_factory=list)  # (account, the roles they picked, best first)


@dataclass
class Roster:
    teams: list  # per team: [(ticket, account, role)]


def _match_roles(players: list, slots: dict) -> dict | None:
    """Give each player one of their roles within the slots: {index: role}, or None."""
    taken: dict[int, list[int]] = {role: [] for role in slots}

    def place(index: int, seen: set) -> bool:
        for role in players[index]:
            if role not in slots or role in seen:
                continue
            seen.add(role)
            if len(taken[role]) < slots[role]:
                taken[role].append(index)
                return True
            for other in list(taken[role]):
                taken[role].remove(other)
                if place(other, seen):
                    taken[role].append(index)
                    return True
                taken[role].append(other)
        return False

    for index in range(len(players)):
        if not place(index, set()):
            return None
    return {index: role for role, indexes in taken.items() for index in indexes}


def build_roster(tickets: list[Ticket], ruleset: Ruleset, roles: dict, minimum: int = 0) -> Roster | None:
    """Pick waiting parties that fill the teams (oldest first), or None when they can't yet.
    `minimum` > 0 takes any `minimum` players instead of full teams (tests)."""
    capacity = [1] * ruleset.team_sizes[0] if ruleset.free_for_all else list(ruleset.team_sizes)
    teams = len(capacity)
    goal = minimum or sum(capacity)
    if minimum:
        capacity = [max(size, -(-minimum // teams)) for size in capacity]  # let small tests fit
        roles = {}
    chosen: list[list[tuple]] = [[] for _ in range(teams)]
    steps = 0

    def fits(team: int, extra: list) -> bool:
        if len(chosen[team]) + len(extra) > capacity[team]:
            return False
        if not roles:
            return True
        picks = [member_roles or list(ANY_ROLE) for _, _, member_roles in chosen[team] + extra]
        return _match_roles(picks, roles) is not None

    def search(start: int, count: int) -> bool:
        nonlocal steps
        if count >= goal:
            return True
        for index in range(start, len(tickets)):
            steps += 1
            if steps > SEARCH_LIMIT:
                return False
            ticket = tickets[index]
            extra = [(ticket, account, picked) for account, picked in ticket.members]
            if ruleset.free_for_all:  # every player on a team of their own
                free = [team for team in range(teams) if not chosen[team]]
                if len(free) < len(extra):
                    continue
                for team, entry in zip(free, extra, strict=False):
                    chosen[team].append(entry)
                if search(index + 1, count + len(extra)):
                    return True
                for team in free[: len(extra)]:
                    chosen[team].clear()
                continue
            for team in sorted(range(teams), key=lambda t: len(chosen[t]) - capacity[t]):
                if fits(team, extra):
                    chosen[team].extend(extra)
                    if search(index + 1, count + len(extra)):
                        return True
                    del chosen[team][-len(extra) :]
        return False

    if not search(0, 0):
        return None
    result = []
    for team in chosen:
        picks = [member_roles or list(ANY_ROLE) for _, _, member_roles in team]
        given = _match_roles(picks, roles) if roles else {}
        result.append(
            [(ticket, account, given.get(index, 0)) for index, (ticket, account, _) in enumerate(team)]
        )
    return Roster(result)


def handoff_message(handoff, host: str, port: int) -> dict:
    """20600 for one player; `handoff` is what the game server made for them (game.server.Handoff)."""
    address = list(host.encode("ascii")[:63].ljust(64, b"\0"))
    return {
        "+0x78": False,
        "+0x80": {
            "+0x0": {"+0x0": handoff.match_id[0], "+0x8": handoff.match_id[1]},
            "+0x28": handoff.conn,
            "+0x2C": port,
            "+0x2E": address,
            "+0x6E": address,
            "+0xAE": list(handoff.key_in),
            "+0xCE": list(handoff.key_out),
            "+0xEE": False,
        },
    }


class Matchmaker:
    def __init__(self, server, minimum_players: int = 0) -> None:
        self.server = server
        self.minimum_players = minimum_players  # 0: full teams; more: tests start with that many
        self.forced_map: int | None = None  # a map Quick Play / Arcade must load; None = the card's pick
        self.rules = load_rules()
        self.tickets: list[Ticket] = []
        self.lock = threading.RLock()

    # --- the queue -----------------------------------------------------------------------------

    def search(self, party: Party, key: dict, card: int) -> None:
        """The party searches this queue now (every member ready)."""
        members = [(member, party.roles.get(member.account_lo, [])) for member in self._players(party)]
        with self.lock:
            self.tickets = [ticket for ticket in self.tickets if ticket.party is not party]
            self.tickets.append(Ticket(party, key, card, time.time(), members))
            log.info("[MM] %s's party (%d) searches card 0x%X", party.leader.name, len(members), card)
            self._try_match(card)

    def cancel(self, party: Party) -> None:
        with self.lock:
            self.tickets = [ticket for ticket in self.tickets if ticket.party is not party]

    def waiting(self) -> int:
        with self.lock:
            return sum(len(ticket.members) for ticket in self.tickets)

    def rules_of(self, card: int) -> QueueRules:
        """The card's rules; a card the data does not know plays like Quick Play."""
        return self.rules.get(card) or self.rules.get(QUICK_PLAY) or QueueRules()

    def _try_match(self, card: int) -> None:
        rules = self.rules_of(card)
        choice = self._pick_map(rules)
        if choice is None:
            log.info("[MM] Card 0x%X (%s): no map the server can host yet", card, rules.name)
            return
        chosen_map, ruleset = choice
        waiting = [ticket for ticket in self.tickets if ticket.card == card]
        roster = build_roster(waiting, ruleset, rules.roles, self.minimum_players)
        if roster is None:
            count = sum(len(ticket.members) for ticket in waiting)
            needed = self.minimum_players or sum(ruleset.team_sizes)
            log.info("[MM] Card 0x%X (%s): %d of %d players waiting", card, rules.name, count, needed)
            return
        used = {id(ticket) for team in roster.teams for ticket, _, _ in team}
        self.tickets = [ticket for ticket in self.tickets if id(ticket) not in used]
        self._start(chosen_map, roster)

    def _pick_map(self, rules: QueueRules):
        """A map of the card that the server can host, with the ruleset it is played in. If the dashboard
        forced a map, that one is used with any of the card's rulesets it supports; when the forced map
        can't be hosted in this card's modes, the card's own random pick is used so a match still starts."""
        if self.forced_map is not None:
            forced = self._map_choices([(self.forced_map, ())], rules.rulesets)
            if forced:
                return random.choice(forced)
            log.info(
                "[MM] Forced map 0x%X is not playable in %s; using the card's pick",
                self.forced_map,
                rules.name or "this card",
            )
        choices = self._map_choices(rules.maps, rules.rulesets)
        return random.choice(choices) if choices else None

    @staticmethod
    def _map_choices(maps, rulesets) -> list:
        """The (GameMap, ruleset) pairs the server can host from these maps. `modes` of () allows any
        ruleset mode (so a forced map is tried against every mode the card plays)."""
        choices = []
        for map_guid, modes in maps:
            for ruleset in rulesets:
                if modes and ruleset.mode not in modes:
                    continue
                found = game_map(map_guid, ruleset.mode, ruleset.mode_name, ruleset.free_for_all)
                if found is not None:
                    choices.append((found, ruleset))
        return choices

    def _players(self, party: Party) -> list:
        """The members who can play: online, not the bot."""
        return [
            member
            for member in party.members
            if not member.virtual and self.server.session_of(member.account_lo)
        ]

    # --- starting a match ----------------------------------------------------------------------

    def practice(self, session) -> None:
        """Training > Practice Range: a match of one on the Practice Range."""
        ticket = Ticket(
            self.server.social.party_of(session.account), {}, 0, time.time(), [(session.account, [])]
        )
        self._start(PRACTICE_RANGE, Roster([[(ticket, session.account, 0)]]), popped=False)

    def _start(self, game_map, roster: Roster, popped: bool = True) -> None:
        server = self.server
        game = server.game
        if game is None:
            log.warning("[MM] The game server is off (--game-port 0)")
            return
        entries = []  # (session, ticket, team)
        for team, members in enumerate(roster.teams):
            for ticket, account, _role in members:
                session = server.session_of(account.account_lo)
                if session is not None:
                    entries.append((session, ticket, team))
        if not entries:
            return
        players = [
            (s.account.account_lo, s.account.name, self._hero(s), team, s.tournament)
            for s, _, team in entries
        ]
        handoffs = game.create_match(game_map, players)
        tickets = {id(ticket): ticket for _, ticket, _ in entries}
        if popped:
            for ticket in tickets.values():
                self._end_search(ticket)
        channel = server.social.open_match_chat(handoffs[0].match_id, [s.account for s, _, _ in entries])
        for (session, ticket, _team), handoff in zip(entries, handoffs, strict=True):
            if popped:
                session.send(QUEUE, 44202, {"+0x78": ticket.key, "+0xA8": QUEUE_POPPED})
            session.send(CHAT_IN, 20402, {"+0x78": channel})
            host = server.settings.game_host or session.sock.getsockname()[0]
            session.send(HANDOFF, 20600, handoff_message(handoff, host, game.port))
            where = f"game server {host}:{game.port}, connection {handoff.conn}"
            session.log(f"[MM] Match found: {game_map.name}, {where}")

    def _end_search(self, ticket: Ticket) -> None:
        """The party is out of the queue; the passes it spent stay spent (a match was found)."""
        party = ticket.party
        party.queue = None
        party.queue_state = 0
        party.accepted.clear()
        party.ready.clear()
        party.pass_roles.clear()
        party.passes_taken.clear()
        self.server.notify_party(party)

    def _hero(self, session) -> int:
        """The hero picked for the menu in the dashboard; the game server takes Soldier: 76 for 0."""
        return self.server.content.menu_hero.picked(session.profile)
