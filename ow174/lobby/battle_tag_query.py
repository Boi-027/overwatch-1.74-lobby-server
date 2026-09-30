"""A join asks the host for its player's BattleTag before the game starts.

A game on another PC logs in through a Battle.net emulator of its own, which knows only the name typed
in START.bat. The host's profile can show another nickname (the dashboard changes it), and the
emulator should give the game the BattleTag the lobby shows. So cli.join asks first, on the lobby port:
one line "OW174 BATTLETAG <name>", answered with one line holding the BattleTag. A game starts its
lobby connection with "HELLO PRO CLIENT" instead.
"""

import socket
from collections.abc import Callable

QUERY = b"OW174 BATTLETAG "
TIMEOUT_SECONDS = 5.0
MAX_LINE_BYTES = 256


def ask_battle_tag(host: str, port: int, name: str) -> str:
    """The host's BattleTag for a name, "" when the host does not answer (an older server). Raises
    OSError when the host can't be reached."""
    with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS) as sock:
        sock.sendall(QUERY + name.encode("utf-8") + b"\n")
        try:
            return _read_line(sock)
        except OSError:  # an older server drops the connection when the hello is not a game's
            return ""


def answer_query(sock: socket.socket, battle_tag_of: Callable[[str], str]) -> bool:
    """Answer a BattleTag query on a new lobby connection. False, reading nothing, for a game."""
    if sock.recv(len(QUERY), socket.MSG_PEEK) != QUERY:
        return False
    name = _read_line(sock)[len(QUERY) :].strip()
    sock.sendall(battle_tag_of(name).encode("utf-8") + b"\n")
    return True


def _read_line(sock: socket.socket) -> str:
    data = bytearray()
    while len(data) < MAX_LINE_BYTES:
        byte = sock.recv(1)
        if not byte or byte == b"\n":
            break
        data += byte
    return data.decode("utf-8", errors="replace").strip()
