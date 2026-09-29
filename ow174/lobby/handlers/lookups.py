"""Requests that ask for information: player names, career profiles, leaderboards, the store and
Overwatch League live matches."""

from ow174.catalog.regions import localize
from ow174.content import Identity
from ow174.content.leaderboard import Player
from ow174.jam.groups import (
    LEADERBOARD_OUT,
    NAME_QUERY,
    NAME_REPLY,
    OWL_LIVE,
    OWL_POLL,
    PROFILES,
    SOCIAL_OUT,
    STORE,
    STORE_QUERY,
)
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

# Retail's answer to 42600 (from the ProCore research): no live match.
NO_OWL_MATCH = {"+0x78": True, "+0x80": []}


@routes.on(NAME_QUERY, 58202)
def player_names(session: Session, value: dict) -> None:
    names = []
    for player_id in value.get("+0x78") or []:
        account_lo = player_id.get("+0x0", 0)
        account = session.server.accounts.by_id(account_lo)
        name = account.name if account else f"Player{account_lo & 0xFFFF}"
        names.append({"+0x0": player_id, "+0x10": 0, "+0x18": name})
    session.send(NAME_REPLY, 58301, {"+0x78": names})


@routes.on(OWL_POLL, 42600)
def owl_live_matches(session: Session, value: dict) -> None:
    session.send(OWL_LIVE, 43200, NO_OWL_MATCH)


@routes.on(SOCIAL_OUT, 22206)
def career_profile(session: Session, value: dict) -> None:
    career = session.server.content.career
    target = value.get("+0x88") or {}
    account_lo = target.get("+0x0", 0)
    account = session.server.accounts.by_id(account_lo)
    if account is None:
        # An unknown player only gets a profile status (39001).
        messages = career.profile(session.profile, session.ident, target)
        shown_name = hex(account_lo)
    else:
        identity = Identity.create(account.account_lo, session.channel.seq)
        messages = career.profile(account.profile, identity, target, value.get("+0x78"))
        shown_name = account.name
    session.send_all(messages)
    session.log(f"[>>>] Career profile for {shown_name}")


@routes.on(LEADERBOARD_OUT, 39100)
def leaderboard(session: Session, value: dict) -> None:
    request = value.get("+0x78") or {}
    players = session.server.leaderboard_players()
    me = Player(session.account.name, session.profile, session.ident)
    friends = {name.lower() for name in session.profile.friends}
    page = session.server.content.leaderboard.page(request, players, me, friends)
    session.send(PROFILES, 39000, page)
    key, number = request.get("+0x0", 0), request.get("+0x8", 0)
    session.log(f"[>>>] Leaderboard 0x{key:016X} page {number}: {len(page['+0x78']['+0x0'])} rows")


@routes.on(STORE_QUERY, 26500)
def store(session: Session, value: dict) -> None:
    for msg_id, message in session.server.templates.all(STORE):
        session.send(STORE, msg_id, localize(message, session.profile.region))
