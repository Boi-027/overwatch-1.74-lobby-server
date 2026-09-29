"""Tools for protocol research: a log of everything the client sends, and message injection.

Injection sends an experimental message to a connected client without restarting the server. Every
line of inject.jsonl is sent to each matching logged-in client, then the file is deleted. A line is
one of

    {"crc": "BCD57A46", "msg": 55500, "value": {...}}   encoded with the schemas
    {"crc": "BCD57A46", "msg": 55500, "hex": "..."}     a raw body

and may carry "conn" or "player" to pick the client, and "expires_at" (Unix time) to drop stale lines.
"""

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ow174.jam.values import to_jsonable

if TYPE_CHECKING:
    from ow174.lobby.server import LobbyServer

log = logging.getLogger(__name__)


class ClientRecorder:
    """Appends every client message, decoded and raw, to a log file for protocol research."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def record(self, conn_id: int, player: str | None, crc: int, msg_id: int, body: bytes, value) -> None:
        entry = {
            "t": time.strftime("%H:%M:%S"),
            "conn": conn_id,
            "player": player,
            "crc": f"{crc:08X}",
            "msg": msg_id,
            "value": to_jsonable(value) if value is not None else None,
            "raw": body.hex(),
        }
        with self._lock, self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _is_expired(item: dict) -> bool:
    if "expires_at" not in item:
        return False
    deadline = item["expires_at"]
    # bool is a subclass of int, so it has to be refused by name.
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise ValueError("expires_at must be a finite Unix timestamp")
    return time.time() >= deadline


def _is_target(session, conn_id: int | None, player: str | None) -> bool:
    if conn_id is not None and session.conn_id != conn_id:
        return False
    return player is None or session.account.name.casefold() == player


def _message_body(server: "LobbyServer", item: dict, crc: int, msg_id: int) -> bytes:
    if "hex" in item:
        return bytes.fromhex(item["hex"])
    return server.schemas.encode(crc, msg_id, item["value"])


def send_experiment(server: "LobbyServer", item: dict) -> int:
    """Send one injected message to the matching clients. Returns how many received it."""
    if _is_expired(item):
        return 0
    crc = int(str(item["crc"]), 16)
    msg_id = int(item["msg"])
    body = _message_body(server, item, crc, msg_id)
    conn_id = int(item["conn"]) if "conn" in item else None
    player = str(item["player"]).casefold() if "player" in item else None
    sent = 0
    for session in list(server.social.sessions.values()):
        if not _is_target(session, conn_id, player):
            continue
        wire = session.wire_of.get(crc)
        if wire is None:
            continue
        session.send_raw(bytes([wire, msg_id - server.schemas.base(crc)]) + body)
        session.log(f"[inject] {crc:08X}/{msg_id} {len(body)}B")
        sent += 1
    return sent


def watch_inject_file(server: "LobbyServer", path: Path, interval: float = 1.0) -> None:
    """Poll the injection file forever. Meant to run in a daemon thread."""
    while True:
        time.sleep(interval)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            path.unlink()
        except OSError:
            continue
        for line in lines:
            if line.strip():
                _send_line(server, line)


def _send_line(server: "LobbyServer", line: str) -> None:
    try:
        if not send_experiment(server, json.loads(line)):
            log.info("[inject] No connected client matches this line or knows its protocol")
    except (ValueError, KeyError, TypeError, OSError) as error:
        log.warning("[inject] Bad line %s: %s", line[:120], error)
