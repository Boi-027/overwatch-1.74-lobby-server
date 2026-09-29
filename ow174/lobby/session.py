"""One connected client: reading and sending messages, and the logged-in player."""

import contextlib
import json
import logging
import socket
import struct
import threading
import time
from typing import TYPE_CHECKING

from ow174.accounts.registry import Account
from ow174.content import Identity
from ow174.jam.codec import DecodeError
from ow174.jam.framing import (
    ANNOUNCE,
    CONNECTED,
    CONTROL_WIRE,
    PING,
    PONG,
    FrameReader,
    parse_announcement,
    send_frame,
)
from ow174.jam.groups import CHAT_IN, FRIENDS, PARTY, TELEMETRY
from ow174.jam.handshake import Channel
from ow174.jam.values import to_jsonable

if TYPE_CHECKING:
    from ow174.lobby.server import LobbyServer

logger = logging.getLogger("ow174.lobby")

LOGIN_MESSAGE = 21800  # the only message accepted before the player is logged in
POLL_SECONDS = 2.0  # how often the read loop wakes up to send a keep-alive
KEEPALIVE_SECONDS = 10.0
LOG_VALUE_LIMIT = 200  # characters of an unhandled message shown in the log


def without_party_state(messages: list[tuple]) -> list[tuple]:
    """Drop the party state message, because Session.party_messages builds a fresher one."""
    kept = []
    for message in messages:
        crc, msg_id = message[0], message[1]
        if (crc, msg_id) != (PARTY, 20700):
            kept.append(message)
    return kept


class Session:
    def __init__(self, server: "LobbyServer", sock: socket.socket, channel: Channel, conn_id: int) -> None:
        self.server = server
        self.sock = sock
        self.channel = channel
        self.conn_id = conn_id
        self.account: Account | None = None
        self.ident: Identity | None = None
        self.logged_in = False
        self.party_channel: dict | None = None  # the party chat channel the client has joined
        self.crc_at: dict[int, int] = {}  # wire index -> protocol group CRC, from the announcement
        self.wire_of: dict[int, int] = {}  # protocol group CRC -> wire index
        self._send_lock = threading.Lock()

    @property
    def profile(self):
        return self.account.profile

    def log(self, message: str, level: int = logging.INFO) -> None:
        who = f" {self.account.name}" if self.account else ""
        logger.log(level, "[lobby #%d%s] %s", self.conn_id, who, message)

    def save(self) -> None:
        self.account.save()

    def disconnect(self) -> None:
        """Close the connection. The read loop then ends and cleans up."""
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)

    # --- sending -------------------------------------------------------------------------------

    def announce(self, crcs: list[int]) -> None:
        """Remember the protocol groups the client announced. Their order gives the wire index."""
        self.crc_at = {}
        self.wire_of = {}
        missing = []
        for index, crc in enumerate(crcs, start=1):
            self.crc_at[index] = crc
            self.wire_of[crc] = index
            if crc not in self.server.schemas.groups:
                missing.append(f"{crc:08X}")
        message = f"[+] Client announced {len(crcs)} protocol groups"
        if missing:
            message += f" (no schema: {missing})"
        self.log(message)

    def send(self, crc: int, msg_id: int, value: dict) -> bool:
        """Encode and send a message. Returns False when the client did not announce its group."""
        wire = self.wire_of.get(crc)
        if wire is None:
            self.log(f"[!] Skipped {msg_id}: protocol {crc:08X} not announced", logging.WARNING)
            return False
        schemas = self.server.schemas
        self.send_raw(bytes([wire, msg_id - schemas.base(crc)]) + schemas.encode(crc, msg_id, value))
        return True

    def send_raw(self, payload: bytes) -> None:
        with self._send_lock:
            send_frame(self.sock, self.channel.tx, payload)

    def send_all(self, messages: list[tuple]) -> int:
        """Send (crc, id, value) messages. Returns how many were sent."""
        sent = 0
        for crc, msg_id, value in messages:
            if self.send(crc, msg_id, value):
                sent += 1
        return sent

    def send_social(self) -> None:
        """Send a fresh friends list with everyone's presence."""
        self.send(FRIENDS, 27100, self.server.social.friends_state(self.account))

    def party_messages(self) -> list[tuple]:
        """The party state, plus joining or leaving the party chat channel when that changed."""
        social = self.server.social
        party = social.party_of(self.account)
        messages = [(PARTY, 20700, social.party_state(party))]
        in_group = len(party.members) > 1
        if in_group and self.party_channel != party.chat_channel:
            messages.append((CHAT_IN, 20402, {"+0x78": party.chat_channel}))
            self.party_channel = party.chat_channel
        elif not in_group and self.party_channel is not None:
            messages.append((CHAT_IN, 20404, {"+0x78": self.party_channel}))
            self.party_channel = None
        return messages

    def push_state(self) -> None:
        """Refresh the client after a dashboard edit: card, party, currencies, collection, events."""
        if not self.logged_in:
            return
        content = self.server.content
        earned = content.celebrations.claim_rewards(self.profile)
        if earned:
            self.save()
        messages = without_party_state(content.live_messages(self.profile, self.ident))
        messages += self.party_messages()
        for guid in earned:
            messages.append(content.collection.unlock_granted(guid))
        sent = self.send_all(messages)
        summary = f"[>>>] Live update: {sent} messages"
        if earned:
            summary += f", {len(earned)} challenge rewards"
        self.log(summary)

    # --- receiving -----------------------------------------------------------------------------

    def run(self) -> None:
        """Serve the client until it disconnects."""
        try:
            self._receive_loop()
        except OSError:
            self.log("Client disconnected.")
        finally:
            self._cleanup()

    def _receive_loop(self) -> None:
        reader = FrameReader(self.channel.rx)
        last_keepalive = time.time()
        while True:
            try:
                self.sock.settimeout(POLL_SECONDS)
                data = self.sock.recv(65536)
            except TimeoutError:
                if time.time() - last_keepalive > KEEPALIVE_SECONDS:
                    self.send_raw(PONG)
                    last_keepalive = time.time()
                continue
            if not data:
                self.log("Client closed socket.")
                return
            for frame in reader.feed(data):
                if len(frame) < 2:
                    continue
                wire, offset = frame[0], frame[1]
                if wire != CONTROL_WIRE:
                    self.dispatch(wire, offset, frame[2:])
                elif offset == ANNOUNCE:
                    self.announce(parse_announcement(frame))
                    self.send_raw(CONNECTED)
                    self.log("[>>>] Sent CONNECTED")
                elif offset == PING:
                    last_keepalive = time.time()
                    self.send_raw(PONG)

    def dispatch(self, wire: int, offset: int, body: bytes) -> None:
        """Decode a message and run its handler."""
        schemas = self.server.schemas
        crc = self.crc_at.get(wire)
        if crc is None or crc not in schemas.groups:
            self.log(f"[<<<] wire={wire} offset={offset} len={len(body)} (unknown protocol)")
            return
        msg_id = schemas.base(crc) + offset
        try:
            value = schemas.decode(crc, msg_id, body)
        except (DecodeError, KeyError, struct.error) as error:
            self.log(f"[<<<] {crc:08X}/{msg_id} len={len(body)} undecodable: {error}")
            value = None
        if crc in TELEMETRY:
            return
        player = self.account.name if self.account else None
        self.server.recorder.record(self.conn_id, player, crc, msg_id, body, value)

        handler = self.server.router.get(crc, msg_id)
        allowed = self.logged_in or msg_id == LOGIN_MESSAGE
        if handler is None or value is None or not allowed:
            shown = json.dumps(to_jsonable(value), ensure_ascii=False)[:LOG_VALUE_LIMIT]
            self.log(f"[<<<] {crc:08X}/{msg_id} (wire {wire}) {shown}")
            return
        try:
            # Dashboard edits and message handlers change the same profile, so they take turns.
            with self.server.state_lock:
                handler(self, value)
        except Exception:
            logger.exception("[lobby #%d] Handler for %08X/%d failed", self.conn_id, crc, msg_id)

    def _cleanup(self) -> None:
        """Stop the client's game instance and tell the others it left."""
        server = self.server
        if server.matches is not None:
            server.matches.cancel(self.conn_id)
        if not self.account or server.social.sessions.get(self.account.account_lo) is not self:
            return
        del server.social.sessions[self.account.account_lo]
        party = server.social.leave(self.account)
        if party:
            server.notify_party(party)
        server.broadcast_presence()
