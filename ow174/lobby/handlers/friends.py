"""Friends: requests by BattleTag, accepting or declining them, and removing a friend."""

from ow174.jam.groups import FRIENDS, FRIENDS_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

SEND_REQUEST = 27000  # {token, 1, BattleTag, message}; answered by 27110
ANSWER_REQUEST = 27001  # {token, inviter id, 0 = accept}; answered by 27111 ("Invitation accepted!")
REMOVE_FRIEND = 27002  # {token, friend id}; answered by 27112 ("Friend removed")
REQUEST_RECEIVED = 27107  # {request}: unlike the list (27100), it shows the "friend request" banner
REQUEST_RESULT = 27110  # {token, text, 0}: the client shows the text (0x7FF78960D700)
ANSWERED = 27111
REMOVED = 27112
OK, FAILED = 0, 1
# Texts (07C) the client shows for a friend request.
SENT = 0x0DE0000000000365  # "Invitation sent."
ALREADY_FRIENDS = 0x0DE00000000017B6  # "This player is already your friend."
BAD_NAME = 0x0DE0000000000360  # "Invalid name ... use the full BattleTag."
REQUEST_TEXTS = {"sent": SENT, "pending": SENT, "added": SENT, "already": ALREADY_FRIENDS}


@routes.on(FRIENDS_OUT, SEND_REQUEST)
def send_request(session: Session, value: dict) -> None:
    server = session.server
    tag = (value.get("+0x88") or "").strip()
    target, outcome = server.social.request_friend(session.account, tag)
    session.log(f"[friends] Request {value.get('+0x78')} to {tag!r}: {outcome}")
    text = REQUEST_TEXTS.get(outcome, BAD_NAME)
    session.send(FRIENDS, REQUEST_RESULT, {"+0x78": value.get("+0x78", 0), "+0x80": text, "+0x88": 0})
    if outcome == "sent":
        recipient = server.session_of(target.account_lo)
        if recipient:
            request = server.social.request_record(target, session.account)
            recipient.send(FRIENDS, REQUEST_RECEIVED, {"+0x78": request})
    elif outcome == "added":
        _refresh(session, target)


@routes.on(FRIENDS_OUT, ANSWER_REQUEST)
def answer_request(session: Session, value: dict) -> None:
    server = session.server
    inviter = server.accounts.by_id((value.get("+0x80") or {}).get("+0x0", 0))
    accept = value.get("+0x90", 0) == 0
    token = value.get("+0x78", 0)
    if inviter is None or inviter.name not in session.profile.friend_requests:
        session.send(FRIENDS, ANSWERED, {"+0x78": token, "+0x80": FAILED})
        session.log(f"[friends] Answer to an unknown request {value}")
        return
    if accept:
        server.social.accept_friend(session.account, inviter)
    else:
        server.social.decline_friend(session.account, inviter)
    session.send(FRIENDS, ANSWERED, {"+0x78": token, "+0x80": OK})
    session.log(f"[friends] {'Accepted' if accept else 'Declined'} {inviter.name}")
    _refresh(session, inviter)


@routes.on(FRIENDS_OUT, REMOVE_FRIEND)
def remove_friend(session: Session, value: dict) -> None:
    server = session.server
    friend = server.accounts.by_id((value.get("+0x80") or {}).get("+0x0", 0))
    token = value.get("+0x78", 0)
    if friend is None or friend.virtual or friend not in server.social.friends_of(session.account):
        session.send(FRIENDS, REMOVED, {"+0x78": token, "+0x80": FAILED})
        return
    server.social.remove_friend(session.account, friend)
    session.send(FRIENDS, REMOVED, {"+0x78": token, "+0x80": OK})
    session.log(f"[friends] Removed {friend.name}")
    _refresh(session, friend)


def _refresh(session: Session, other) -> None:
    """Send fresh friends lists to both players (the other one only when online)."""
    session.send_social()
    recipient = session.server.session_of(other.account_lo)
    if recipient:
        recipient.send_social()
