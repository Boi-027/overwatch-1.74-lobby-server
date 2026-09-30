"""Party invites and their answers, joining from the group finder, leader transfer, kicks and leaving."""

import threading

from ow174.jam.groups import CHAT_IN, LOBBY, PARTY, PARTY_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session
from ow174.services.social import INVITE_SECONDS

routes = Router()

BOT_GREETING = "Hi! I'm in the group."
MAX_PARTY_SIZE = 6  # a team; a merge that would make a bigger party is not asked or done
# "X wants to merge groups" (05A/066F): 20703 {+0x78 the asking leader's card, +0xE0 their group}
# puts a request on the client's list for 20 seconds (0x7FF789762200). Yes answers 22110 and No or
# the timeout 22111, each with the asking group's party id (seen in game; not the leader's id).
MERGE_REQUEST = 20703
# 20704 {inviter id, inviter card} shows "Join X's group?" with Accept (22108) and Decline (22109),
# each with the inviter's id.
INVITE = 20704
# 20705 {+0x78 inviter id} takes that invite off the invitee's list again (0x7FF789D2F380, the list
# 20704 adds to).
INVITE_CANCELLED = 20705
# The client sends 22102 whether or not the leader lets members invite (0x7FF7897652D0 checks only
# a full party). A member who may not invite suggests the player to the leader instead: 20701
# {+0x78 the member's card, +0xE0 the player's card} shows the leader "X suggests Y for the group"
# with Invite, which sends the leader's own 22102 for Y (01B/20AE, 0x7FF7899E1B90).
SUGGESTION = 20701
# 20803 {+0x78 code} shows the client's party error of that code (table 0x7FF78B529290).
PARTY_ERROR = 20803
CANNOT_SUGGEST = 14  # "Unable to suggest the player for the group."
INVITE_PLAYER = 22102
JOIN_GROUP = 22103
INVITE_GROUP = 22104
KICK = 22105
MAKE_LEADER = 22106
LEAVE = 22107
ACCEPT_INVITE = 22108
DECLINE_INVITE = 22109
MERGE_ACCEPT = 22110
MERGE_DECLINE = 22111


def merge_request(social, leader, group) -> dict:
    return {"+0x78": social.player_record(leader), "+0xE0": social.group(group)}


def _account_at(social, value: dict):
    """The account whose id a party message carries at +0x78."""
    return social.accounts.by_id((value.get("+0x78") or {}).get("+0x0", 0))


def _sender_party(social, value: dict) -> tuple:
    """The player at +0x78 who invited or asked to merge, and that player's party."""
    player = _account_at(social, value)
    return player, social.parties.get(player.account_lo) if player else None


@routes.on(PARTY_OUT, INVITE_PLAYER)
def invite(session: Session, value: dict) -> None:
    server = session.server
    social = server.social
    target = _account_at(social, value)
    if target is None or target is session.account:
        session.log(f"[<<<] Party invite for unknown player {value}")
        return
    mine = social.party_of(session.account)
    if not social.may_invite(mine, session.account):
        _suggest(session, mine, target)
        return
    party = social.invite(session.account, target)
    session.log(f"[<<<] Party invite -> {target.name}")
    if target.virtual:  # the bot accepts at once
        social.join(target, party)
        if party.listing is not None:
            # Players pick their group slot (52203); a member without one shows the group finder
            # icon on their party tile, so the bot takes a free slot itself.
            social.take_free_slot(party, target)
        server.notify_party(party)
        if session.profile.bot_chat:
            session.send(CHAT_IN, 20400, social.chat_message(party.chat_channel, target, BOT_GREETING))
        return
    recipient = server.session_of(target.account_lo)
    if recipient:
        inviter = social.player_record(session.account)
        recipient.send(PARTY, INVITE, {"+0x78": inviter["+0x0"], "+0x88": inviter})
    # The party panel shows the invitee as a pending tile until the answer or the timeout.
    server.notify_party(party)
    _expire_later(session, party, party.invites[target.account_lo])


def _suggest(session: Session, party, target) -> None:
    social = session.server.social
    leader = session.server.session_of(party.leader.account_lo)
    if leader is None:
        session.send(LOBBY, PARTY_ERROR, {"+0x78": CANNOT_SUGGEST})
        session.log(f"[<<<] Party invite -> {target.name}: not suggested, {party.leader.name} is offline")
        return
    suggestion = {"+0x78": social.player_record(session.account), "+0xE0": social.player_record(target)}
    leader.send(PARTY, SUGGESTION, suggestion)
    session.log(f"[<<<] Party invite -> {target.name}, suggested to {party.leader.name} (no member invites)")


def _expire_later(session: Session, party, pending) -> None:
    def expire() -> None:
        if session.server.social.expire_invite(party, pending):
            session.server.notify_party(party)
            session.log(f"[<<<] {pending.invitee.name} did not answer the party invite in time")

    timer = threading.Timer(INVITE_SECONDS, expire)
    timer.daemon = True
    timer.start()


@routes.on(PARTY_OUT, ACCEPT_INVITE)
def accept_invite(session: Session, value: dict) -> None:
    """Accept on the invite popup: join the inviter's party."""
    server = session.server
    social = server.social
    inviter, party = _sender_party(social, value)
    if party is None or session.account.account_lo not in party.invites:
        session.log(f"[<<<] Invite accepted without an invite {value}")
        return
    social.join(session.account, party)
    server.notify_party(party)
    session.log(f"[<<<] Joined {inviter.name}'s party")


@routes.on(PARTY_OUT, DECLINE_INVITE)
def decline_invite(session: Session, value: dict) -> None:
    """Decline on the invite popup: drop the invite and its pending tile."""
    inviter, party = _sender_party(session.server.social, value)
    if party is not None and party.invites.pop(session.account.account_lo, None) is not None:
        session.server.notify_party(party)
        session.log(f"[<<<] Declined {inviter.name}'s invite")


@routes.on(PARTY_OUT, JOIN_GROUP)
def join_group(session: Session, value: dict) -> None:
    """Join in the group finder: the group's party id in +0x88 and no inviter (0x7FF78975FAB0)."""
    social = session.server.social
    group = social.party_by_id(value.get("+0x88"))
    if group is None or group.listing is None or not group.searching:
        session.log(f"[<<<] Join for a group that is not looking for players {value}")
        return
    if not social.can_join(group, session.account):
        session.log(f"[group] Join refused: outside {group.leader.name}'s group endorsement or rating limits")
        return
    social.join(session.account, group)
    session.server.notify_party(group)
    session.log(f"[group] Joined {group.leader.name}'s group from the group finder")


@routes.on(PARTY_OUT, INVITE_GROUP)
def invite_group(session: Session, value: dict) -> None:
    """Invite Group in the group finder: while the player's group is looking for players, another
    listed group can be asked to join it whole (0x7FF78975FB10, which checks nothing itself). The
    bot's group says yes at once; a player's leader gets "X wants to merge groups" (20703)."""
    server = session.server
    social = server.social
    target = social.party_by_id(value.get("+0x88"))
    party = social.party_of(session.account)
    if target is None or target is party:
        session.log(f"[<<<] Invite group for an unknown group {value}")
        return
    if len(party.members) + len(target.members) > MAX_PARTY_SIZE:
        session.log(f"[group] Invite group -> {target.leader.name}'s group does not fit")
        return
    if not social.may_invite(party, session.account):
        session.log(f"[group] Invite group -> {target.leader.name}'s group refused: only the leader invites")
        return
    if all(member.virtual for member in target.members):
        social.merge_into(target, party)
        server.notify_party(party)
        server.notify_watchers(target)  # left empty, so closed
        session.log(f"[group] Invite group: {target.leader.name}'s group joined")
        return
    leader = server.session_of(target.leader.account_lo)
    if leader is None:
        session.log(f"[group] Invite group -> {target.leader.name} is offline")
        return
    party.merge_invites.add(target.leader.account_lo)
    leader.send(PARTY, MERGE_REQUEST, merge_request(social, session.account, party))
    session.log(f"[group] Invite group -> asked {target.leader.name} to merge")


@routes.on(PARTY_OUT, MERGE_ACCEPT)
def accept_merge(session: Session, value: dict) -> None:
    """Yes on "X wants to merge groups": the player's party moves into X's group."""
    server = session.server
    social = server.social
    group = social.party_by_id(value.get("+0x78"))
    me = session.account.account_lo
    if group is None or me not in group.merge_invites:
        session.log(f"[group] Merge accepted without a request {value}")
        return
    group.merge_invites.discard(me)
    mine = social.party_of(session.account)
    if mine is group or len(mine.members) + len(group.members) > MAX_PARTY_SIZE:
        session.log(f"[group] Merge into {group.leader.name}'s group no longer fits")
        return
    social.merge_into(mine, group)
    server.notify_party(group)
    server.notify_watchers(mine)  # left empty, so closed
    session.log(f"[group] Merged into {group.leader.name}'s group")


@routes.on(PARTY_OUT, MERGE_DECLINE)
def decline_merge(session: Session, value: dict) -> None:
    group = session.server.social.party_by_id(value.get("+0x78"))
    if group is not None:
        group.merge_invites.discard(session.account.account_lo)
    session.log(f"[group] Merge declined ({group.leader.name if group else 'unknown group'})")


@routes.on(PARTY_OUT, MAKE_LEADER)
def make_leader(session: Session, value: dict) -> None:
    """The leader hands the party to another member (+0x78). The 20700 leader flag (+0xE2) goes to
    the first member, so the new leader moves to the front."""
    server = session.server
    social = server.social
    party = social.party_of(session.account)
    target = _account_at(social, value)
    if party.leader is not session.account or target is session.account or target not in party.members:
        return
    party.members.remove(target)
    party.members.insert(0, target)
    party.leader = target
    server.notify_party(party)
    session.log(f"[<<<] {target.name} is now the group leader")


@routes.on(PARTY_OUT, KICK)
def kick(session: Session, value: dict) -> None:
    """Remove from group. The cross on a pending tile sends it too, with the invitee's id (the client
    only checks that the sender leads, 0x7FF789762FA0), and cancels that invite."""
    server = session.server
    social = server.social
    party = social.party_of(session.account)
    target = _account_at(social, value)
    if target is None or party.leader is not session.account:
        return
    pending = social.cancel_invite(party, target)
    if pending is not None:
        server.notify_party(party)
        recipient = server.session_of(target.account_lo)
        if recipient:
            recipient.send(PARTY, INVITE_CANCELLED, {"+0x78": pending.inviter.account})
        session.log(f"[<<<] Cancelled the party invite to {target.name}")
        return
    if target not in party.members:
        session.log(f"[<<<] Kick for a player not in the group {value}")
        return
    social.leave(target)
    server.notify_party(party)
    kicked = server.session_of(target.account_lo)
    if kicked:
        kicked.send_all(kicked.party_messages())
    session.log(f"[<<<] Kicked {target.name}")


@routes.on(PARTY_OUT, LEAVE)
def leave(session: Session, value: dict) -> None:
    server = session.server
    party = server.social.leave(session.account)
    if party:
        server.notify_party(party)
    session.send_all(session.party_messages())
    session.log("[<<<] Left the party")
