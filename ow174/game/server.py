"""The game server: one UDP port for every match, one thread.

The lobby sends each player of a match 20600 with this server's address, a connection id of their
own and two keys (lobby/matchmaker.py). The client then sends SYN datagrams with that id in the
header until it gets SYN|ACK, and the session is open with its first ACK. From then on the server
sends one world frame per tick; the first reliable message is 20300 (load the map).
"""

import ctypes
import logging
import os
import secrets
import socket
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

from ow174.game import messages, world
from ow174.game.bits import BitReader
from ow174.game.commands import InputError, read_commands
from ow174.game.link import (
    ACK,
    FIN,
    HEADER_SIZE,
    KIND_CONTROL,
    KIND_FRAME,
    MAX_DATAGRAM,
    SYN,
    LinkCipher,
    Packet,
    ReceiveWindow,
    delivery,
    peek_connection,
)
from ow174.game.match import Match, Player, no_skin, pong
from ow174.jam.codec import DecodeError
from ow174.jam.values import to_jsonable

log = logging.getLogger("ow174.game")

TICK_SECONDS = 0.016
FIRST_TICK = 1000
SILENCE_SECONDS = 10.0  # the client sends at least every 250 ms; after this long it is gone
MAX_PAYLOAD = MAX_DATAGRAM - HEADER_SIZE
PING = 21610
MAP_LOADED = (21601, 21602)
GAME_MESSAGE = 21616
LEAVE_GAME = 20304
SIO_UDP_CONNRESET = 0x9800000C


def _ignore_connection_resets(sock: socket.socket) -> None:
    """On Windows a closed game's ICMP "port unreachable" comes back as an error from the next
    recvfrom; SIO_UDP_CONNRESET turns that off. Python's sock.ioctl does not take it, so WSAIoctl."""
    if os.name != "nt":
        return
    ws2 = ctypes.WinDLL("ws2_32")
    ws2.WSAIoctl.argtypes = [
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    off = wintypes.BOOL(False)
    returned = wintypes.DWORD()
    ws2.WSAIoctl(
        sock.fileno(),
        SIO_UDP_CONNRESET,
        ctypes.byref(off),
        ctypes.sizeof(off),
        None,
        0,
        ctypes.byref(returned),
        None,
        None,
    )


@dataclass
class Handoff:
    """What the lobby puts in a player's 20600."""

    conn: int
    key_in: bytes  # the client encrypts with it (20600 +0xAE)
    key_out: bytes  # we encrypt with it (20600 +0xCE)
    match_id: tuple[int, int]


class Client:
    """One connected game: the transport state and the queues of what to send."""

    def __init__(self, server: "GameServer", address, conn: int, cipher: LinkCipher, player: Player) -> None:
        self.server = server
        self.address = address
        self.conn = conn
        self.cipher = cipher
        self.player = player
        self.window = ReceiveWindow()
        self.seq = 0  # of our next datagram
        self.open = False
        self.closed = False
        self.heard = time.time()
        self.next_reliable = 0
        self.reliable = []  # [(seq, encoded)] waiting to go out, oldest first
        self.unreliable = []  # [encoded]
        self.entities = []  # [EntityUpdate] waiting to go out
        self.in_flight = {}  # datagram seq -> (reliable messages, entity updates) it carried
        self.expected_in = 0  # the client's next reliable message
        self.parked_in = {}

    def log(self, message: str, level: int = logging.INFO) -> None:
        log.log(level, "[game %s:%d %s] %s", self.address[0], self.address[1], self.player.name, message)

    # --- queues --------------------------------------------------------------------------------

    def queue_reliable(self, msg_id: int, value: dict) -> None:
        self.reliable.append((self.next_reliable, messages.encode(msg_id, value)))
        self.next_reliable += 1
        if msg_id not in (20306,):
            self.log(f"[>>>] {msg_id} (reliable #{self.next_reliable - 1})")

    def queue_unreliable(self, msg_id: int, value: dict) -> None:
        self.unreliable.append(messages.encode(msg_id, value))

    def queue_entities(self, updates: list) -> None:
        self.entities.extend(updates)

    # --- sending -------------------------------------------------------------------------------

    def send_control(self, flags: int) -> None:
        self._send(Packet(self.conn, KIND_CONTROL, flags=flags))

    def _send(self, packet: Packet) -> None:
        packet.seq = self.seq
        packet.ack = self.window.expected
        packet.ack_bits = self.window.bits
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        try:
            self.server.sock.sendto(self.cipher.seal(packet), self.address)
        except OSError as error:
            self.log(f"[!] send failed: {error}", logging.WARNING)

    def send_frame(self, tick: int, now: float) -> None:
        reliable = self.reliable[: messages.MAX_PER_KIND]
        unreliable = self.unreliable[: messages.MAX_PER_KIND]
        entities = self.entities + self.player.match.remote_movements(self.player)
        payload = self._payload(tick, reliable, unreliable, entities)
        while len(payload) > MAX_PAYLOAD and len(entities) > 1:
            entities = entities[: len(entities) // 2]
            payload = self._payload(tick, reliable, unreliable, entities)
        del self.reliable[: len(reliable)]
        del self.unreliable[: len(unreliable)]
        sent_records = [item for item in entities if item.op or item.chunk is not None]
        self.entities = [item for item in self.entities if item not in sent_records]
        self.in_flight[self.seq] = (reliable, sent_records)
        self._send(Packet(self.conn, KIND_FRAME, flags=ACK, payload=payload))

    def _payload(self, tick: int, reliable, unreliable, entities) -> bytes:
        return world.frame(tick, tick, reliable, unreliable, entities)

    def acked(self, ack: int, ack_bits: int) -> None:
        """Take back what the client did not get: its reliable messages and entity records go out again."""
        lost_reliable, lost_records = [], []
        for seq in list(self.in_flight):
            state = delivery(seq, ack, ack_bits)
            if state is None:
                continue
            reliable, records = self.in_flight.pop(seq)
            if state:
                for item in records:
                    if item.stream is not None:
                        item.stream.arrived(item.chunk_last)
            else:
                lost_reliable += reliable
                lost_records += [item for item in records if item.resend]
        if lost_reliable:
            self.reliable = sorted(lost_reliable + self.reliable)
            self.log(f"[!] resending {len(lost_reliable)} reliable message(s)")
        if lost_records:
            self.entities = lost_records + self.entities
            self.log(f"[!] resending {len(lost_records)} entity record(s)")

    # --- receiving -----------------------------------------------------------------------------

    def received(self, packet: Packet, now: float) -> None:
        if not self.window.accept(packet.seq):
            return
        self.heard = now
        self.acked(packet.ack, packet.ack_bits)
        if packet.flags & FIN:
            self.log("[<<<] FIN: the game left")
            self.send_control(FIN | ACK)
            self.server.drop(self)
            return
        if packet.flags & SYN:
            self.send_control(SYN | ACK)
        if not self.open and packet.flags & ACK:
            self.open = True
            self.log("[+] connected")
            self.queue_reliable(20300, self.player.match.instance_message(self.player))
        if packet.kind == KIND_FRAME and packet.payload:
            self._payload_in(packet.payload, now)

    def _payload_in(self, payload: bytes, now: float) -> None:
        reader = BitReader(payload)
        try:
            reliable, unreliable = messages.read_channel(reader)
        except (DecodeError, KeyError, EOFError) as error:
            self.log(f"[<<<] undecodable messages: {error} {payload.hex()}", logging.WARNING)
            return
        for seq, msg_id, value in reliable:
            self._reliable_in(seq, msg_id, value, now)
        for msg_id, value in unreliable:
            self._message(msg_id, value, now)
        try:
            commands = read_commands(reader)
        except InputError as error:
            self.log(f"[<<<] undecodable input: {error}", logging.DEBUG)
            return
        if commands and self.player.spawned:
            self.player.take_commands(commands)

    def _reliable_in(self, seq: int, msg_id: int, value: dict, now: float) -> None:
        if seq < self.expected_in:
            return  # a repeat
        self.parked_in[seq] = (msg_id, value)
        while self.expected_in in self.parked_in:
            msg_id, value = self.parked_in.pop(self.expected_in)
            self.expected_in += 1
            self._message(msg_id, value, now)

    def _message(self, msg_id: int, value: dict, now: float) -> None:
        if msg_id == PING:
            self.queue_reliable(20306, pong(value, time.time_ns()))
            return
        if msg_id in MAP_LOADED:
            self.player.match.map_loaded(self.player, now)
        if msg_id == GAME_MESSAGE:
            self.player.match.game_message(self.player, value, now)
            return
        shown = str(to_jsonable(value))[:300]
        self.log(f"[<<<] {msg_id} {shown}")


class GameServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 3730, on_leave=None, skin_of=no_skin) -> None:
        self.host = host
        self.port = port
        self.on_leave = on_leave  # called with each player who leaves a match
        self.skin_of = skin_of  # (account, hero) -> the equipped (skin theme, golden weapon); no locks
        self.sock: socket.socket | None = None
        self.lock = threading.RLock()
        self.handoffs: dict[int, tuple[Handoff, Player]] = {}  # connection id -> waiting player
        self.clients: dict[tuple, Client] = {}  # address -> client
        self.matches: list[Match] = []
        self.tick = FIRST_TICK
        self._next_conn = secrets.randbelow(0x100000) + 1
        self._left_players: list[Player] = []  # told to on_leave once the lock is released
        self.running = False

    def start(self) -> None:
        """Bind the port and run the server thread. Raises OSError when the port is taken."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _ignore_connection_resets(self.sock)
        self.sock.bind((self.host, self.port))
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._run, name="game-server", daemon=True).start()
        log.info("[game] Game server on UDP %s:%d", self.host, self.port)

    def stop(self) -> None:
        self.running = False

    # --- matches -------------------------------------------------------------------------------

    def create_match(self, game_map, players) -> list[Handoff]:
        """A match for these players: [(account_lo, name, hero GUID, team, tournament)]. Returns the
        20600 data of each, in the same order."""
        with self.lock:
            match = Match(game_map, on_leave=self._left, skin_of=self.skin_of)
            handoffs = []
            for account_lo, name, hero, team, tournament in players:
                player = match.add_player(account_lo, name, hero, team, tournament)
                conn = self._next_conn
                self._next_conn = (self._next_conn % 0x0FFFFFFF) + 1
                handoff = Handoff(conn, secrets.token_bytes(32), secrets.token_bytes(32), match.id)
                self.handoffs[conn] = (handoff, player)
                handoffs.append(handoff)
            self.matches.append(match)
        names = ", ".join(player.describe() for player in match.players)
        log.info("[game] %s on %s (%s): %s", match.label(), game_map.name, game_map.mode_name, names)
        return handoffs

    def switch_hero(self, account_lo: int, hero) -> bool:
        with self.lock:
            for match in self.matches:
                for player in match.players:
                    if player.account_lo == account_lo and player.client is not None:
                        match.switch_hero(player, hero)
                        return True
        return False

    def send_home(self, account_lo: int | None = None) -> int:
        """Send a player's game (every game for None) back to the menu: 20304 with 0 = leave for good
        (0x7FF7896E6830). Returns how many games were told."""
        count = 0
        with self.lock:
            for client in list(self.clients.values()):
                if account_lo is None or client.player.account_lo == account_lo:
                    client.queue_reliable(LEAVE_GAME, {"+0x78": 0, "+0x79": False})
                    count += 1
        return count

    def in_match(self, account_lo: int) -> bool:
        with self.lock:
            return any(player.account_lo == account_lo for match in self.matches for player in match.players)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [match.snapshot() for match in self.matches]

    def _left(self, player: Player) -> None:
        self._left_players.append(player)

    def _report_leaves(self) -> None:
        """Tell the lobby about players who left, outside our lock: the lobby calls us under its own."""
        with self.lock:
            left, self._left_players = self._left_players, []
        for player in left:
            if self.on_leave is not None:
                try:
                    self.on_leave(player)
                except Exception:
                    log.exception("[game] the lobby's leave handler failed")

    # --- the loop ------------------------------------------------------------------------------

    def _run(self) -> None:
        next_tick = time.monotonic()
        while self.running:
            timeout = max(0.0, next_tick - time.monotonic())
            self.sock.settimeout(timeout or 0.0001)
            try:
                data, address = self.sock.recvfrom(4096)
            except TimeoutError:
                data = None
            except OSError as error:
                log.warning("[game] receive failed: %s", error)
                data = None
            try:
                with self.lock:
                    if data is not None:
                        self._datagram(data, address, time.time())
                    if time.monotonic() >= next_tick:
                        next_tick += TICK_SECONDS
                        if next_tick < time.monotonic():  # fell behind: do not try to catch up
                            next_tick = time.monotonic() + TICK_SECONDS
                        self._tick(time.time())
            except Exception:
                log.exception("[game] tick failed")
            self._report_leaves()
        self.sock.close()

    def _datagram(self, data: bytes, address, now: float) -> None:
        client = self.clients.get(address)
        if client is not None:
            packet = client.cipher.open(data)
            if packet is None:
                return
            client.received(packet, now)
            return
        conn = peek_connection(data)
        waiting = self.handoffs.get(conn)
        if waiting is None:
            return
        handoff, player = waiting
        cipher = LinkCipher(handoff.key_in, handoff.key_out)
        packet = cipher.open(data)
        if packet is None or not packet.flags & SYN:
            return
        del self.handoffs[conn]
        client = Client(self, address, conn, cipher, player)
        player.client = client
        self.clients[address] = client
        client.log(f"[+] SYN for {player.match.label()}")
        client.received(packet, now)

    def _tick(self, now: float) -> None:
        self.tick += 1
        for match in list(self.matches):
            match.update(now, self.tick)
        for client in list(self.clients.values()):
            if now - client.heard > SILENCE_SECONDS:
                client.log("[!] no datagrams for 10 s, dropping the game")
                self.drop(client)
            elif client.open:
                client.send_frame(self.tick, now)
        self.matches = [match for match in self.matches if not match.ended]

    def drop(self, client: Client) -> None:
        if client.closed:
            return
        client.closed = True
        self.clients.pop(client.address, None)
        client.player.match.leave(client.player)
