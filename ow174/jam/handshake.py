"""The lobby handshake: hello strings, nonce exchange, key proofs and the 292-byte state blob.

Both sides derive the stream keys from the two nonces with HMAC-SHA256 under an all-zero key. The
same HMAC values are the proofs each side sends, so a matching proof also confirms the keys.
"""

import hashlib
import hmac
import os
import socket
import struct
import time
from dataclasses import dataclass

from ow174.jam.cipher import Jam

HELLO_CLIENT = b"HELLO PRO CLIENT\x00"
HELLO_SERVER = b"HELLO PRO SERVER\x00"
ZERO_KEY = bytes(32)  # the client does not check a session key, so both sides use zeros
DIFFICULTY = 0
CLIENT_MESSAGE_SIZE = 40  # 8 bytes we ignore, then a 32-byte nonce or proof

# HMAC key that signs the state blob. It is fixed in the client.
BLOB_KEY = bytes.fromhex(
    "3586f3628a631b705712405b8acc71d40fd1670cc1b03ea384974a6fb1a76196"
    "b142f0b72310ea8116d00a4c352f09acdbfb50a63ec5153e62e4d67fe09beecc"
)


class HandshakeError(ConnectionError):
    """The peer did not speak the expected handshake."""


def recv_exact(sock: socket.socket, count: int, timeout: float = 30) -> bytes:
    """Read exactly `count` bytes, or raise ConnectionError when the peer closes early."""
    sock.settimeout(timeout)
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("client closed")
        data += chunk
    return bytes(data)


def build_state_blob(seq: int, host: str, port: int) -> bytes:
    """The 292-byte state message that completes the handshake.

    Bytes 0-255 are a fixed pattern holding the server's address and an HMAC over the rest of it. The
    address is a type (2, IPv4), the IPv4 address and the port. The retail client goes on only when
    the address or the port is the one it dialed (0x7FF789D48F10, else error 0x300DA); it keeps that
    port as a plain number (strtol in 0x7FF78932C11D), so it is little-endian here. A 36-byte trailer
    follows: the peer id (type 5), the channel id (type 13, with the lobby port and address) and a
    u32 equal to 1. Evidence for the trailer: the AyakaPS and Blizless servers.
    """
    blob = bytearray(range(256))
    blob[0] = 0x02
    address = socket.inet_aton(host)
    blob[1:5] = address
    blob[5:7] = struct.pack("<H", port)
    blob[255] = 0xFF
    signed = bytes(blob[:176]) + bytes(blob[208:256])
    blob[176:208] = hmac.new(BLOB_KEY, signed, hashlib.sha256).digest()

    peer_id = struct.pack("<IBHBH", seq, 0x00, 5, 0x01, 0xBEEF) + bytes(6)
    channel_id = struct.pack("<IBHBH", seq, 0x00, 13, 0x01, 0x0000) + struct.pack("<H", port) + address
    return bytes(blob) + peer_id + channel_id + struct.pack("<I", 1)


@dataclass(frozen=True)
class Channel:
    """The two encrypted streams of one connection and its sequence number."""

    tx: Jam  # server to client
    rx: Jam  # client to server
    seq: int


def _mac(message: bytes) -> bytes:
    return hmac.new(ZERO_KEY, message, hashlib.sha256).digest()


def _recv_client_value(sock: socket.socket) -> bytes:
    return recv_exact(sock, CLIENT_MESSAGE_SIZE)[8:]


def server_handshake(sock: socket.socket, conn_id: int) -> Channel:
    """Run the server side of the handshake and return the encrypted channel."""
    hello = recv_exact(sock, len(HELLO_CLIENT))
    if hello != HELLO_CLIENT:
        raise HandshakeError(f"bad hello: {hello!r}")
    sock.sendall(HELLO_SERVER)

    client_nonce = _recv_client_value(sock)
    server_nonce = os.urandom(32)
    # 32 random bytes, the difficulty byte, then our nonce.
    sock.sendall(os.urandom(32) + bytes([DIFFICULTY]) + server_nonce)

    server_to_client_key = _mac(client_nonce + server_nonce)
    if _recv_client_value(sock) != server_to_client_key:
        raise HandshakeError("client proof does not match")
    client_to_server_key = _mac(server_nonce + client_nonce)

    seq = (int(time.time() * 1000) ^ (conn_id << 16)) & 0xFFFFFFFF
    channel = Channel(tx=Jam(server_to_client_key), rx=Jam(client_to_server_key), seq=seq)
    # The address the connection came in on, not the one the server listens on (0.0.0.0 for players
    # on other PCs): the client compares it with the address it dialed.
    host, port = sock.getsockname()[:2]
    sock.sendall(client_to_server_key + channel.tx.crypt(build_state_blob(seq, host, port)))
    return channel
