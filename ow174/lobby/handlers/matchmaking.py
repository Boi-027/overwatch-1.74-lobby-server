"""Queueing for a game, and the group finder.

No real match is created: the game connection is still blocked (see docs/STATE.md). A queue request
starts a local game-server process that only records what it receives.
"""

import json

from ow174.jam.groups import GAME_REQUEST, GROUP_FINDER, MATCHMAKE
from ow174.jam.values import to_jsonable
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

ENTER_QUEUE = 44100  # Play or search pressed
CANCEL_QUEUE = 44102  # the player cancelled a search
PRACTICE_RANGE = (2, 4)  # a create-game request with kind 2 and flags 4


def _mode_guid(value: dict) -> int:
    return value["+0x78"]["+0x0"]["+0x0"]


@routes.on(MATCHMAKE, ENTER_QUEUE)
def enter_queue(session: Session, value: dict) -> None:
    shown = json.dumps(to_jsonable(value), ensure_ascii=False)[:300]
    session.log(f"[MM] Enter queue (44100) {shown}")
    _allocate_game(session, _mode_guid(value), "queue")


@routes.on(MATCHMAKE, CANCEL_QUEUE)
def cancel_queue(session: Session, value: dict) -> None:
    if session.server.matches is not None:
        session.server.matches.cancel(session.conn_id, mode=_mode_guid(value))
    session.log("[MM] Cancel queue (44102): stopped this search's game instance")


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


# The group finder messages (52200-52205) only go from client to server. The client closes the
# connection on any group finder message it did not ask for, so these handlers only log.


@routes.on(GROUP_FINDER, 52201)
def group_state(session: Session, value: dict) -> None:
    group = value["+0x78"]
    session.log(f"[group] 52201 slots={list(group.get('+0x80', []))} name={group.get('+0xA0')!r}")


@routes.on(GROUP_FINDER, 52203)
def group_roles(session: Session, value: dict) -> None:
    session.log(f"[group] 52203 selected roles={list(value.get('+0x78', []))}")
