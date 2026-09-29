"""Frames on the encrypted stream: a 3-byte big-endian length, then the payload.

A payload starts with two bytes. The first is the wire index of the protocol group, which the client
announces at the start of the connection (index 0 is reserved for control frames). The second is the
message offset inside that group.
"""

import socket
import struct
from collections.abc import Iterator

from ow174.jam.cipher import Jam

CONTROL_WIRE = 0
ANNOUNCE = 0  # control frame: the client lists its protocol groups
PING = 3  # control frame: keep-alive, answered with PONG
CONNECTED = b"\x00\x02"  # control reply to the announcement
PONG = b"\x00\x03"


def send_frame(sock: socket.socket, cipher: Jam, payload: bytes) -> None:
    """Frame, encrypt and send one payload."""
    sock.sendall(cipher.crypt(len(payload).to_bytes(3, "big") + payload))


def parse_announcement(frame: bytes) -> list[int]:
    """The protocol group CRCs of an ANNOUNCE control frame, in wire order (index 1 first)."""
    (count,) = struct.unpack_from("<I", frame, 2)
    return list(struct.unpack_from(f"<{count}I", frame, 6))


class FrameReader:
    """Decrypts incoming bytes and splits them into frames."""

    def __init__(self, cipher: Jam) -> None:
        self._cipher = cipher
        self._buffer = b""

    def feed(self, data: bytes) -> Iterator[bytes]:
        """Yield every complete frame contained in the data received so far."""
        self._buffer += self._cipher.crypt(data)
        while len(self._buffer) >= 3:
            size = int.from_bytes(self._buffer[:3], "big")
            if len(self._buffer) < 3 + size:
                return
            frame, self._buffer = self._buffer[3 : 3 + size], self._buffer[3 + size :]
            yield frame
