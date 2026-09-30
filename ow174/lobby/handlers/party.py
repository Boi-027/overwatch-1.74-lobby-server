"""Party invites and their answers, joining from the group finder, leader transfer, kicks and leaving."""

from ow174.jam.groups import CHAT_IN, PARTY, PARTY_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

BOT_GREETING = "Hi! I'm in the group."
# "X wants to merge groups" (05A/066F): 20703 {+0x78 the asking leader's card, +0xE0 their group}
# puts a request on the client's list for 20 seconds (0x7FF789762200), 20705 {+0x78 id} takes it
# off. Yes answers 22110 and No or the timeout 22111, each with the asking leader's id (01B/1666).
MERGE_REQUEST = 20703
# 20704 {inviter id, inviter card} shows "Join X's group?" with Accept (22108) and Decline (22109),
# each with the inviter's id. 20701 {card, card} is "X suggests Y", with a button that never joins.
INVITE = 20704
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
    party = social.invite(session.account, target)
    session.log(f"[<<<] Party invite -> {target.name}")
    if target.virtual:  # the bot accepts at once
        social.join(target, party)
        server.notify_party(party)
        if session.profile.bot_chat:
            session.send(CHAT_IN, 20400, social.chat_message(party.chat_channel, target, BOT_GREETING))
        return
    recipient = server.session_of(target.account_lo)
    if recipient:
        inviter = social.player_record(session.account)
        recipient.send(PARTY, INVITE, {"+0x78": inviter["+0x0"], "+0x88": inviter})


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
    """Decline on the invite popup: drop the invite."""
    inviter, party = _sender_party(session.server.social, value)
    if party is not None and party.invites.pop(session.account.account_lo, None) is not None:
        session.log(f"[<<<] Declined {inviter.name}'s invite")


@routes.on(PARTY_OUT, JOIN_GROUP)
def join_group(session: Session, value: dict) -> None:
    """Join in the group finder: the group's party id in +0x88 and no inviter (0x7FF78975FAB0)."""
    social = session.server.social
    group = social.party_by_id(value.get("+0x88"))
    if group is None or group.listing is None or not group.searching:
        session.log(f"[<<<] Join for a group that is not looking for players {value}")
        return
    social.join(session.account, group)
    session.server.notify_party(group)
    session.log(f"[group] Joined {group.leader.name}'s group from the group finder")


@routes.on(PARTY_OUT, INVITE_GROUP)
def invite_group(session: Session, value: dict) -> None:
    """Invite Group in the group finder: while the player's group is looking for players, another
    listed group can be asked to join it whole (0x7FF78975FB10). The bot's group says yes at once;
    other players' groups are not asked yet."""
    server = session.server
    social = server.social
    target = social.party_by_id(value.get("+0x88"))
    party = social.party_of(session.account)
    if target is None or target is party:
        session.log(f"[<<<] Invite group for an unknown group {value}")
        return
    if not all(member.virtual for member in target.members):
        session.log(f"[group] Invite group -> {target.leader.name}'s group (not asked: a player's group)")
        return
    social.merge_into(target, party)
    server.notify_party(party)
    server.notify_watchers(target)  # left empty, so closed
    session.log(f"[group] Invite group: {target.leader.name}'s group joined")


@routes.on(PARTY_OUT, MERGE_ACCEPT)
def accept_merge(session: Session, value: dict) -> None:
    """Yes on "X wants to merge groups": the player's party moves into X's group."""
    server = session.server
    social = server.social
    leader, group = _sender_party(social, value)
    me = session.account.account_lo
    if group is None or me not in group.merge_invites:
        session.log(f"[group] Merge accepted without a request {value}")
        return
    group.merge_invites.discard(me)
    mine = social.party_of(session.account)
    social.merge_into(mine, group)
    server.notify_party(group)
    server.notify_watchers(mine)  # left empty, so closed
    session.log(f"[group] Merged into {leader.name}'s group")


@routes.on(PARTY_OUT, MERGE_DECLINE)
def decline_merge(session: Session, value: dict) -> None:
    leader, group = _sender_party(session.server.social, value)
    if group is not None:
        group.merge_invites.discard(session.account.account_lo)
    session.log(f"[group] Merge declined ({leader.name if leader else 'unknown'})")


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
    server = session.server
    social = server.social
    party = social.party_of(session.account)
    target = _account_at(social, value)
    if target is None or party.leader is not session.account or target not in party.members:
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
