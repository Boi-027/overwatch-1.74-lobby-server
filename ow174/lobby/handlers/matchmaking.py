"""Queueing for a game, and the group finder.

No real match is created: the game connection is still blocked (see docs/STATE.md). A queue request
starts a local game-server process that only records what it receives.
"""

import json

from ow174.content import passes
from ow174.content.queue import PASS_ROLES, PICKING, ROLE_NUMBERS, SEARCHING, wait_times
from ow174.content.ranked import ROLES
from ow174.jam.groups import GAME_REQUEST, GROUP_FINDER, GROUPS, MATCHMAKE, PASSES, QUEUE, QUEUE_WAITS
from ow174.jam.values import to_jsonable
from ow174.lobby.router import Router
from ow174.lobby.session import Session
from ow174.services.social import ANY_SLOT, DAMAGE_SLOT

routes = Router()

# What the client sends; see content/queue.py for the role screen.
ENTER_QUEUE = 44100  # a queue was picked (a role queue opens its role screen at once)
CANCEL_QUEUE = 44102  # {key}: the player left the queue (the client drops its own entry first)
SET_ROLES = 44103  # {role numbers}: Ready on the role screen, with the whole list
ACCEPT = 44104  # Accept on the group banner "pick a role now?"
DECLINE = 44105  # Decline on that banner
PASS_ROLE = 44106  # {role numbers}: the role to spend a priority pass on, sent after 44103
CHANGE_ROLES = 44107  # Change Role on the role screen, once the member is ready
PRACTICE_RANGE = (2, 4)  # a create-game request with kind 2 and flags 4


def _mode_guid(value: dict) -> int:
    return value["+0x78"]["+0x0"]["+0x0"]


def queue_joined(key: dict) -> dict:
    """44201: the server's entry for a queue the client asked to join. The client keeps a list of
    these (its queue system, 0x7FF789512330); 44100 alone only marks the join as pending, so
    without the entry the client never counts as queued. The entry starts with the key of the
    request."""
    return {
        "+0x78": {
            "+0x0": key,
            "+0x30": {"+0x0": 0, "+0x8": 0, "+0x10": 0},
            "+0x48": 0,
            "+0x50": 0.0,
            "+0x54": 0,
            "+0x55": False,
            "+0x56": False,
        }
    }


def queue_left(key: dict) -> dict:
    """44202: the client drops its entry for the queue and goes back. Reason 0 is "UserRequest"
    (the client's reason names are at 0x7FF789618000); the client only logs it."""
    return {"+0x78": key, "+0xA8": 0}


@routes.on(MATCHMAKE, ENTER_QUEUE)
def enter_queue(session: Session, value: dict) -> None:
    """The whole party joins the queue. A role queue first waits until every member pressed Ready
    on the role screen; the member who picked the queue is on that screen already."""
    shown = json.dumps(to_jsonable(value), ensure_ascii=False)[:300]
    session.log(f"[MM] Enter queue (44100) {shown}")
    key = value["+0x78"]
    party = session.server.social.party_of(session.account)
    _send_members(session, party, 44201, queue_joined(key))
    if session.server.content.arcade.has_roles(_mode_guid(value)):
        party.queue = key
        party.queue_state = PICKING
        # The role badge on each portrait shows these roles (0x7FF7898FC8D0); they are new each time.
        party.roles = {}
        party.accepted = {session.account.account_lo}
        party.ready = set()
        party.pass_roles = {}
        party.passes_taken = set()
        _send_members(session, party, 56200, wait_times(), QUEUE_WAITS)
        session.server.notify_party(party)
        session.log("[MM] Role check started")
    _allocate_game(session, _mode_guid(value), "queue")


@routes.on(MATCHMAKE, CANCEL_QUEUE)
def cancel_queue(session: Session, value: dict) -> None:
    party = session.server.social.party_of(session.account)
    _leave_queue(session, party, value["+0x78"], skip=session.account.account_lo)
    session.log("[MM] Cancel queue (44102)")


@routes.on(MATCHMAKE, ACCEPT)
def accept(session: Session, value: dict) -> None:
    party = session.server.social.party_of(session.account)
    if party.queue is None:
        session.log("[MM] Accept (44104) without a role check")
        return
    party.accepted.add(session.account.account_lo)
    session.server.notify_party(party)
    session.log("[MM] Accept (44104): on the role screen")


@routes.on(MATCHMAKE, DECLINE)
def decline(session: Session, value: dict) -> None:
    party = session.server.social.party_of(session.account)
    if party.queue is None:
        session.log("[MM] Decline (44105) without a role check")
        return
    _leave_queue(session, party, party.queue)
    session.log("[MM] Decline (44105): the party left the queue")


@routes.on(MATCHMAKE, SET_ROLES)
def set_roles(session: Session, value: dict) -> None:
    """Ready with these roles. An empty list (sent after Change Role in a group) takes them back."""
    party = session.server.social.party_of(session.account)
    me = session.account.account_lo
    roles = []
    for number in value.get("+0x78", []):
        if number in ROLE_NUMBERS and number not in roles:
            roles.append(number)
    party.roles[me] = roles
    # 44106 follows with the role for a priority pass, if any.
    party.pass_roles.pop(me, None)
    if roles:
        party.ready.add(me)
        party.accepted.add(me)
    else:
        party.ready.discard(me)
    session.log(f"[MM] Roles (44103): {_role_names(roles)}")
    if party.queue is not None:
        _update_queue(session, party)


@routes.on(MATCHMAKE, PASS_ROLE)
def pass_role(session: Session, value: dict) -> None:
    """The role to spend a priority pass on (empty for none), sent right after 44103. The client
    only offers it for one chosen role that takes passes, while the player has passes for this
    queue."""
    party = session.server.social.party_of(session.account)
    me = session.account.account_lo
    wanted = [number for number in value.get("+0x78", []) if ROLE_NUMBERS.get(number) in PASS_ROLES]
    if party.queue is None:
        session.log(f"[MM] Priority pass (44106) without a role check: {wanted}")
        return
    pool = session.server.social.pass_pool(party)
    if wanted and wanted[0] in party.roles.get(me, []) and passes.count(session.profile, pool) > 0:
        party.pass_roles[me] = wanted[0]
        session.log(f"[MM] Priority pass (44106) for {_role_names(wanted[:1])}")
    else:
        party.pass_roles.pop(me, None)
    if party.queue_state == SEARCHING:
        _take_passes(session, party)  # the search already started with 44103
    session.server.notify_party(party)


@routes.on(MATCHMAKE, CHANGE_ROLES)
def change_roles(session: Session, value: dict) -> None:
    """The member is not ready any more; the party waits for them again."""
    party = session.server.social.party_of(session.account)
    if party.queue is None:
        session.log("[MM] Change role (44107) without a role check")
        return
    party.ready.discard(session.account.account_lo)
    party.pass_roles.pop(session.account.account_lo, None)
    party.queue_state = PICKING
    _give_passes_back(session, party)
    session.server.notify_party(party)
    session.log("[MM] Change role (44107)")


def _update_queue(session: Session, party) -> None:
    """Starts the search once every member is ready, and sends the party state. The bot has no
    client to press Ready, so it counts as ready."""
    everyone = all(member.virtual or member.account_lo in party.ready for member in party.members)
    if everyone and party.queue_state != SEARCHING:
        party.queue_state = SEARCHING
        # The same key updates the client's queue entry, so its search timer starts at 0.
        _send_members(session, party, 44201, queue_joined(party.queue))
        _take_passes(session, party)
        session.log("[MM] Everyone is ready: searching")
    session.server.notify_party(party)


def _take_passes(session: Session, party) -> None:
    """The search holds one pass of each member who used one."""
    pool = session.server.social.pass_pool(party)
    for member in party.members:
        lo = member.account_lo
        if lo in party.pass_roles and lo not in party.passes_taken and passes.count(member.profile, pool) > 0:
            passes.change(member.profile, pool, -1)
            party.passes_taken.add(lo)
            _passes_changed(session, member)


def _give_passes_back(session: Session, party) -> None:
    """A search that ends without a match gives its passes back."""
    pool = session.server.social.pass_pool(party)
    for member in party.members:
        if member.account_lo in party.passes_taken:
            passes.change(member.profile, pool, 1)
            _passes_changed(session, member)
    party.passes_taken.clear()


def _passes_changed(session: Session, member) -> None:
    member.save()
    member_session = session.server.session_of(member.account_lo)
    if member_session:
        member_session.send(PASSES, 58501, passes.counts(member.profile))


def _leave_queue(session: Session, party, key: dict, skip: int | None = None) -> None:
    """Takes the party out of the queue. Members get 44202, so their client drops its queue entry
    and closes the role screens; the one who cancelled (skip) dropped it already."""
    _send_members(session, party, 44202, queue_left(key), skip=skip)
    matches = session.server.matches
    for member in party.members:
        member_session = session.server.session_of(member.account_lo)
        if matches is not None and member_session:
            matches.cancel(member_session.conn_id, mode=key["+0x0"]["+0x0"])
    if party.queue is not None:
        _give_passes_back(session, party)
        party.queue = None
        party.queue_state = 0
        party.accepted.clear()
        party.ready.clear()
        party.pass_roles.clear()
        session.server.notify_party(party)


def _send_members(
    session: Session, party, msg_id: int, value: dict, crc: int = QUEUE, skip: int | None = None
) -> None:
    for member in party.members:
        if member.account_lo == skip:
            continue
        member_session = session.server.session_of(member.account_lo)
        if member_session:
            member_session.send(crc, msg_id, value)


def _role_names(chosen: list[int]) -> str:
    return ", ".join(ROLES[ROLE_NUMBERS[number]] for number in chosen) or "none"


@routes.on(GAME_REQUEST, 24000)
def create_game(session: Session, value: dict) -> None:
    if (value.get("+0x78"), value.get("+0xA8")) == PRACTICE_RANGE:
        # The Practice Range request carries a creation kind, not a mode GUID.
        _allocate_game(session, 0, "practice")
    else:
        session.log(f"[MM] Unknown create-game request: {to_jsonable(value)}")


def _allocate_game(session: Session, mode: int, activity: str) -> None:
    matches = session.server.matches
    if matches is None:
        session.log("[MM] Game instances are off (--game-port 0)")
        return
    instance = matches.request(session.conn_id, session.account.name, mode, activity)
    session.log(
        f"[MM] {activity}: instance {instance.directory.name}, PID {instance.process.pid}, "
        f"UDP 127.0.0.1:{instance.port}, waiting for the game client"
    )


# The group finder. The client sends 52200-52205 (9529F0ED) and gets its answers in 52300-52302
# (BDDBF58A, a u32 in the frame header). It only searches once it has latency data, which the empty
# data center list sent at login (35500) gives it.


@routes.on(GROUP_FINDER, 52200)
def find_groups(session: Session, value: dict) -> None:
    """The open groups that pass the search filters, the player's own included: the client turns
    Join off for it (0x7FF78934B1D0 wants another party id)."""
    social = session.server.social
    wanted = value.get("+0x78", {})
    mine = social.party_of(session.account)
    found = [
        party
        for party in social.listed_groups()
        if social.matches_filters(party, wanted)
        and (party is mine or social.can_join(party, session.account))
    ]
    groups = [social.group(party) for party in found]
    session.send(GROUPS, 52300, {"+0x78": groups, "+0x90": []})
    session.log(f"[group] search: {len(groups)} groups")


@routes.on(GROUP_FINDER, 52204)
def watch_groups(session: Session, value: dict) -> None:
    """Groups the player picked in the group finder. The client keeps them in LFG +0x240
    (0x7FF78953C070), renews them every 2 minutes and drops them on a new search (52205)."""
    for party_id in value.get("+0x78", []):
        session.watched_groups.add((party_id["+0x0"], party_id["+0x8"]))


@routes.on(GROUP_FINDER, 52205)
def unwatch_groups(session: Session, value: dict) -> None:
    for party_id in value.get("+0x78", []):
        session.watched_groups.discard((party_id["+0x0"], party_id["+0x8"]))


@routes.on(GROUP_FINDER, 52201)
def list_group(session: Session, value: dict) -> None:
    """The leader lists the group (or updates the listing). 52301 is not an answer to this: the
    client only uses it to refresh a group it already found (0x7FF78953D0A0), so the leader gets
    the party state with the listing, marked as searching.

    Creating a group sends 52201 and, one tick later, the leader's slot types (52203): the client
    sets both flags together (0x7FF7899AD830). The party state waits for them, because a listing
    without the leader's slot opens "choose role(s)", and that screen does not close by itself when
    the slot arrives."""
    social = session.server.social
    party = social.party_of(session.account)
    created = party.listing is None
    party.listing = value["+0x78"]
    party.searching = True
    slots = list(party.listing.get("+0x80", []))
    session.log(f"[group] listed: name={party.listing.get('+0xA0')!r} slots={slots}")
    if not created:
        session.server.notify_party(party)
        session.log("[group] party state with the listing sent")


@routes.on(GROUP_FINDER, 52202)
def close_group(session: Session, value: dict) -> None:
    """The leader stops looking for players ("Done" on the group panel). A group with players in
    it keeps its listing, so the panel offers "Find more players"; a leader who is alone leaves
    the group finder. Players who picked the group get 52302 (notify_watchers)."""
    social = session.server.social
    party = social.party_of(session.account)
    if party.listing is None:
        return
    party.searching = False
    if len(party.members) == 1:
        party.listing = None
        party.slot_types.clear()
    session.server.notify_party(party)
    session.log("[group] search stopped" + ("" if party.listing else ", listing closed"))


@routes.on(GROUP_FINDER, 52203)
def group_roles(session: Session, value: dict) -> None:
    """The slot types a member of a listed group takes (ANY_SLOT .. DAMAGE_SLOT in social.py), from
    the slots picked on the "choose role(s)" screen. The client sends an empty list right after
    creating a group; the screen stays until the party state gives the member a slot."""
    social = session.server.social
    party = social.party_of(session.account)
    slot_types = []
    for slot_type in value.get("+0x78", []):
        if ANY_SLOT <= slot_type <= DAMAGE_SLOT and slot_type not in slot_types:
            slot_types.append(slot_type)
    party.slot_types[session.account.account_lo] = slot_types
    session.log(f"[group] slot types (52203): {slot_types}")
    if party.listing is not None:
        social.update_search(party)
        session.server.notify_party(party)
