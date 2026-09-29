"""Party invites, answers, kicks and leaving."""

from ow174.jam.groups import CHAT_IN, PARTY, PARTY_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session
from ow174.services.social import Party

routes = Router()

BOT_GREETING = "Hi! I'm in the group."


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
        session.send(CHAT_IN, 20400, social.chat_message(party.chat_channel, target, BOT_GREETING))
        return
    recipient = server.session_of(target.account_lo)
    if recipient:
        invitation = {"+0x78": social.player_record(session.account), "+0xE0": social.player_record(target)}
        recipient.send(PARTY, 20701, invitation)


@routes.on(PARTY_OUT, 22103)
def answer(session: Session, value: dict) -> None:
    social = session.server.social
    party = _party_inviting(session)
    if party is None:
        session.log(f"[<<<] Party answer without invite {value}")
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
