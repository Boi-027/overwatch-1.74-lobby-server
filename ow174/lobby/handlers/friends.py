"""Friends: requests by BattleTag, accepting or declining them, and removing a friend."""

from ow174.content.presence import STATUS_NAMES
from ow174.jam.groups import FRIENDS, FRIENDS_OUT, LOBBY
from ow174.lobby.router import Router
from ow174.lobby.session import FRIEND_CARDS, Session

routes = Router()

SEND_REQUEST = 27000  # {token, 1, BattleTag, message}; answered by 27110
ANSWER_REQUEST = 27001  # {token, inviter id, 0 = accept}; answered by 27111 ("Invitation accepted!")
REMOVE_FRIEND = 27002  # {token, friend id}; answered by 27112 ("Friend removed")
SET_STATUS = 27011  # {status}: the status dropdown, 1 online, 2 away, 3 busy, 4 appear offline
# Changes to the friends list go one at a time. A whole list (27100) makes the client take every
# online friend as just come online and announce each again.
FRIEND_ADDED = 27105  # {friend entry} (0x7FF789611AA0)
FRIEND_REMOVED = 27106  # {friend id} (0x7FF789614C40)
REQUEST_RECEIVED = 27107  # {request}: unlike the list (27100), it shows the "friend request" banner
REQUEST_REMOVED = 27108  # {inviter id}: takes a request off the list (0x7FF789614EB0)
PRESENCE = 27113
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
    elif outcome == "added":  # they had asked first
        session.send(FRIENDS, REQUEST_REMOVED, {"+0x78": target.account})
        _befriend(session, target)


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
    session.send(FRIENDS, REQUEST_REMOVED, {"+0x78": inviter.account})
    session.log(f"[friends] {'Accepted' if accept else 'Declined'} {inviter.name}")
    if accept:
        _befriend(session, inviter)


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
    session.send(FRIENDS, FRIEND_REMOVED, {"+0x78": friend.account})
    other = server.session_of(friend.account_lo)
    if other:
        other.send(FRIENDS, FRIEND_REMOVED, {"+0x78": session.account.account})
    session.log(f"[friends] Removed {friend.name}")


@routes.on(FRIENDS_OUT, SET_STATUS)
def set_status(session: Session, value: dict) -> None:
    """The status dropdown: remember it and send the presence again, so the player's own client and
    their friends show it."""
    status = value.get("+0x78", 1)
    if status not in STATUS_NAMES:
        session.log(f"[friends] Unknown status {status}")
        return
    server = session.server
    server.social.set_status(session.account, status)
    server.notify_presence(session.account)
    # The party panel tiles, the player's own card among them, read the status only when a party
    # state arrives (0x7FF7898FD14E), so the card kept its old colour until the menu was rebuilt.
    server.notify_party(server.social.party_of(session.account))
    session.log(f"[friends] Status -> {STATUS_NAMES[status]}")


def _befriend(session: Session, other) -> None:
    """Show two new friends each other, the other one only when online: the list entry, the
    presence and the friends' cards."""
    server = session.server
    social = server.social
    for me, friend in ((session.account, other), (other, session.account)):
        recipient = session if me is session.account else server.session_of(me.account_lo)
        if recipient is None:
            continue
        recipient.send(FRIENDS, FRIEND_ADDED, {"+0x78": social.friend_entry(friend)})
        recipient.send(FRIENDS, PRESENCE, {"+0x78": social.presence(friend)})
        recipient.send(LOBBY, FRIEND_CARDS, {"+0x78": social.friend_cards(me)})
