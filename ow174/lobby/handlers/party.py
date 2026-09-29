"""Party invites, answers, kicks and leaving."""

from ow174.jam.groups import CHAT_IN, PARTY, PARTY_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session
from ow174.services.social import Party

routes = Router()

BOT_GREETING = "Hi! I'm in the group."
# "X wants to merge groups" (05A/066F): 20703 {+0x78 the asking leader's card, +0xE0 their group}
# puts a request on the client's list for 20 seconds (0x7FF789762200), 20705 {+0x78 id} takes it
# off. Yes answers 22110 and No or the timeout 22111, each with the asking leader's id (01B/1666).
MERGE_REQUEST = 20703
INVITE_GROUP = 22104
MERGE_ACCEPT = 22110
MERGE_DECLINE = 22111


def merge_request(social, leader, group) -> dict:
    return {"+0x78": social.player_record(leader), "+0xE0": social.group(group)}


def _asking_group(social, value: dict) -> tuple:
    """The leader who asked to merge (22110/22111 +0x78) and that leader's group."""
    leader = social.accounts.by_id((value.get("+0x78") or {}).get("+0x0", 0))
    return leader, social.parties.get(leader.account_lo) if leader else None


@routes.on(PARTY_OUT, 22102)
def invite(session: Session, value: dict) -> None:
    server = session.server
    social = server.social
    target = social.accounts.by_id((value.get("+0x78") or {}).get("+0x0", 0))
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
        invitation = {"+0x78": social.player_record(session.account), "+0xE0": social.player_record(target)}
        recipient.send(PARTY, 20701, invitation)


@routes.on(PARTY_OUT, 22103)
def answer(session: Session, value: dict) -> None:
    """Accept or decline an invite. Join in the group finder sends the same message with the
    group's party id in +0x88 and no inviter (0x7FF78975FAB0)."""
    social = session.server.social
    party = _party_inviting(session)
    if party is None:
        group = social.party_by_id(value.get("+0x88"))
        if group is None or group.listing is None or not group.searching:
            session.log(f"[<<<] Party answer without invite {value}")
            return
        social.join(session.account, group)
        session.server.notify_party(group)
        session.log(f"[group] Joined {group.leader.name}'s group from the group finder")
        return
    if value.get("+0x98"):
        social.join(session.account, party)
        session.server.notify_party(party)
        session.log(f"[<<<] Joined {party.leader.name}'s party")
    else:
        party.invites.pop(session.account.account_lo, None)
        session.log(f"[<<<] Declined {party.leader.name}'s party")


def _party_inviting(session: Session) -> Party | None:
    """A party that has invited this player. Several members share one Party, so each is checked once."""
    account_lo = session.account.account_lo
    for party in set(session.server.social.parties.values()):
        if account_lo in party.invites:
            return party
    return None


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
    leader, group = _asking_group(social, value)
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
    social = session.server.social
    leader, group = _asking_group(social, value)
    if group is not None:
        group.merge_invites.discard(session.account.account_lo)
    session.log(f"[group] Merge declined ({leader.name if leader else 'unknown'})")


@routes.on(PARTY_OUT, 22105)
def kick(session: Session, value: dict) -> None:
    server = session.server
    social = server.social
    party = social.party_of(session.account)
    target = social.accounts.by_id((value.get("+0x78") or {}).get("+0x0", 0))
    if target is None or party.leader is not session.account or target not in party.members:
        return
    social.leave(target)
    server.notify_party(party)
    kicked = server.session_of(target.account_lo)
    if kicked:
        kicked.send_all(kicked.party_messages())
    session.log(f"[<<<] Kicked {target.name}")


@routes.on(PARTY_OUT, 22107)
def leave(session: Session, value: dict) -> None:
    server = session.server
    party = server.social.leave(session.account)
    if party:
        server.notify_party(party)
    session.send_all(session.party_messages())
    session.log("[<<<] Left the party")
