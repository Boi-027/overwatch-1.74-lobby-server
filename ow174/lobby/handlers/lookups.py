"""Requests that ask for information: player names, career profiles and the store."""

from ow174.catalog.regions import localize
from ow174.content import Identity
from ow174.jam.groups import NAME_QUERY, NAME_REPLY, SOCIAL_OUT, STORE, STORE_QUERY
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()


@routes.on(NAME_QUERY, 58202)
def player_names(session: Session, value: dict) -> None:
    names = []
    for player_id in value.get("+0x78") or []:
        account_lo = player_id.get("+0x0", 0)
        account = session.server.accounts.by_id(account_lo)
        name = account.name if account else f"Player{account_lo & 0xFFFF}"
        names.append({"+0x0": player_id, "+0x10": 0, "+0x18": name})
    session.send(NAME_REPLY, 58301, {"+0x78": names})


@routes.on(SOCIAL_OUT, 22206)
def career_profile(session: Session, value: dict) -> None:
    career = session.server.content.career
    target = value.get("+0x88") or {}
    account_lo = target.get("+0x0", 0)
    account = session.server.accounts.by_id(account_lo)
    if account is None:
        # An unknown player is shown with the requester's own profile.
        messages = career.profile(session.profile, session.ident, target)
        shown_name = hex(account_lo)
    else:
        identity = Identity.create(account.account_lo, session.channel.seq)
        messages = career.profile(account.profile, identity, target)
        shown_name = account.name
    session.send_all(messages)
    session.log(f"[>>>] Career profile for {shown_name}")


@routes.on(STORE_QUERY, 26500)
def store(session: Session, value: dict) -> None:
    for msg_id, message in session.server.templates.all(STORE):
        session.send(STORE, msg_id, localize(message, session.profile.region))
