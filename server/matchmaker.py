"""Experimental schema builders for matchmaking and game-server handoff.

Queue44100 and cancel44102 are observed client requests. The live runtime
allocates owned UDP processes; it does not automatically send the candidate
56200/20600 messages. Their full field semantics and the actual game handshake
are not established. Use tools/probe_practice.py for the separate, targeted
53000/52903 state experiment; an acknowledgement is not a playable match.
"""

import socket
import struct

# ---- protocol CRCs (JAM groups) ----
MATCHMAKE = 0x1C6EC712   # client -> server queue ops
MM_RESULT = 0xA1498A6A   # server -> client match/queue channel (56200-56202)
HANDOFF = 0x074DAD18     # server -> client game-server handoff (20600-20602)

# ---- client -> server (MATCHMAKE) ----
ENTER_QUEUE = 44100      # Play/search pressed; body carries the mode GUID
CANCEL_QUEUE = 44102     # observed on manual search cancellation, 2026-09-28
QUEUE_PING = CANCEL_QUEUE  # legacy import compatibility; this is not a periodic ping

# ---- server -> client (MM_RESULT) ----
MATCH_ASSIGN = 56200     # match assignment / state channel (see module docstring)
MATCH_IDS = 56201        # auxiliary flat u64-id list
MATCH_STUB = 56202       # no-op in this client build

MATCH_HANDOFF = 20600    # game-server handoff (address + port + keys)

# ---- experiment config (flipped while testing) ----
AUTO_HANDOFF = False     # True: auto-send the handoff after enter-queue
HANDOFF_DELAY = 3.0      # seconds after enter-queue before the handoff
GAME_HOST = "127.0.0.1"  # where gameserver.py listens
GAME_PORT = 3730


def _fixed_bytes(data: bytes, n: int) -> list:
    """A fixed byte[n] JAM field as a list of ints, null-padded/truncated."""
    b = bytes(data)[:n]
    return list(b) + [0] * (n - len(b))


def player_session(ids=(), online: bool = True, ready: bool = True) -> dict:
    """One 64-byte entry of the 56200 player-session array.

    Layout (schema 56200 -> +0x78 -> +0x0 -> +0x0[]): seven id/token slots at
    +0x0..+0x30 (u64) followed by two bool flags at +0x38/+0x39. The exact
    meaning of the slots is not fully known, so pass whatever ids you have
    (e.g. player id, game-account id, session token).
    """
    vals = list(ids)[:7] + [0] * (7 - len(ids))
    keys = ("+0x0", "+0x8", "+0x10", "+0x18", "+0x20", "+0x28", "+0x30")
    entry = dict(zip(keys, vals))
    entry["+0x38"] = bool(online)
    entry["+0x39"] = bool(ready)
    return entry


def build_match_assign(players, endpoint=(0, 0), match_id=(0, 0, 0, 0), seq: int = 0) -> dict:
    """56200 schema-shaped candidate; a match-found transition is not established.

    players   list of player_session() dicts (>= 1 for a non-empty match)
    endpoint  {u64,u64} server/session address handle at +0x18/+0x18
    match_id  four u64 tokens at +0x40
    seq       trailing u64 at +0x60
    """
    return {
        "+0x78": {
            "+0x0": {"+0x0": list(players)},
            "+0x18": {
                "+0x0": [],  # team/slot triples (u64 x3), optional
                "+0x18": {"+0x0": endpoint[0], "+0x8": endpoint[1]},
            },
            "+0x40": {
                "+0x0": match_id[0], "+0x8": match_id[1],
                "+0x10": match_id[2], "+0x18": match_id[3],
            },
            "+0x60": seq,
        },
    }


def build_match_ids(ids) -> dict:
    """56201 value: a flat array of u64 ids (auxiliary list channel)."""
    return {"+0x78": list(ids)}


def build_handoff(host: str = None, port: int = None, session=(0, 0)) -> dict:
    """20600 game-server handoff pointing the client at our UDP endpoint.

    Only acted on once the client is in the match-found state (see docstring).
    The address encoding is an unverified candidate; no live game UDP exchange is established.
    """
    host = host or GAME_HOST
    port = GAME_PORT if port is None else port
    ipv4 = struct.unpack(">I", socket.inet_aton(host))[0]
    return {
        "+0x78": True,
        "+0x80": {
            "+0x0": {"+0x0": session[0], "+0x8": session[1]},
            "+0x10": 0,
            "+0x18": 0,
            "+0x20": 0,
            "+0x28": ipv4,
            "+0x2C": port,
            "+0x2E": _fixed_bytes(f"{host}:{port}".encode(), 64),
            "+0x6E": _fixed_bytes(host.encode(), 64),
            "+0xAE": [0] * 32,
            "+0xCE": [0] * 32,
            "+0xEE": False,
        },
    }
