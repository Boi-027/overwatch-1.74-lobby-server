"""The game link's UDP transport, server side.

Read in the client (checked in IDA, addresses in our dump):
- A datagram is a 34-byte header, then the payload encrypted with AES-256-GCM (builder
  0x7FF7893096D0, the seal call at 0x7FF7893099E4):
      +0  tag[12]       the first 12 bytes of the GCM tag
      +12 u32           connection id << 4 | kind (the id is 20600 +0x28, stored at conn+4)
      +16 u32 seq       +20 u32 ack       +24 u64 ack_bits
      +32 u8 flags      SYN 1, ACK 2, FIN 4
      +33 u8 0xAD
  The additional data is the 22 header bytes after the tag. The nonce is the 8-byte salt of 20600
  (+0x18 client to server, +0x20 server to client; we send 0) and the u32 seq. The first key of
  20600 encrypts what the client sends, the second what the server sends (0x7FF7893089E0).
- kind 0 carries no payload, 1 is a world frame, 15 a bulk fragment.
- ack is the next seq expected from the other side, bit k of ack_bits = seq ack-1-k arrived. A
  seq below the expected one is dropped (0x7FF789309FC0). The transport resends nothing: lost
  reliable messages must be sent again by the layer above.
- The client sends SYN until it gets SYN|ACK, then its datagrams carry ACK (state machine
  0x7FF7893094F0). FIN closes the connection.
"""

import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEADER_SIZE = 34
TAG_SIZE = 12
MAGIC = 0xAD
MAX_DATAGRAM = 0x4F0  # the client's inline send buffer; bigger datagrams were never tested
SYN, ACK, FIN = 1, 2, 4
KIND_CONTROL, KIND_FRAME = 0, 1
ACK_WINDOW = 64


class Packet:
    __slots__ = ("ack", "ack_bits", "conn", "flags", "kind", "payload", "seq")

    def __init__(self, conn=0, kind=0, seq=0, ack=0, ack_bits=0, flags=0, payload=b"") -> None:
        self.conn = conn
        self.kind = kind
        self.seq = seq
        self.ack = ack
        self.ack_bits = ack_bits
        self.flags = flags
        self.payload = payload

    def header(self) -> bytes:
        word = ((self.conn << 4) | (self.kind & 0xF)) & 0xFFFFFFFF
        return struct.pack(
            "<IIIQBB", word, self.seq & 0xFFFFFFFF, self.ack & 0xFFFFFFFF, self.ack_bits, self.flags, MAGIC
        )

    def __repr__(self) -> str:
        names = [name for bit, name in ((SYN, "SYN"), (ACK, "ACK"), (FIN, "FIN")) if self.flags & bit]
        return (
            f"<conn {self.conn} kind {self.kind} seq {self.seq} ack {self.ack} "
            f"{'|'.join(names) or '-'} {len(self.payload)} bytes>"
        )


def peek_connection(datagram: bytes) -> int | None:
    """The connection id of a datagram, read before decrypting (it is not encrypted)."""
    if len(datagram) < HEADER_SIZE or datagram[HEADER_SIZE - 1] != MAGIC:
        return None
    return struct.unpack_from("<I", datagram, TAG_SIZE)[0] >> 4


class LinkCipher:
    """Seals what the server sends and opens what the client sends, with the keys of one 20600."""

    def __init__(self, key_in: bytes, key_out: bytes, salt_in: bytes = bytes(8), salt_out: bytes = bytes(8)):
        if len(key_in) != 32 or len(key_out) != 32:
            raise ValueError("the link uses 32-byte keys")
        self.key_in = key_in
        self.key_out = key_out
        self.salt_in = salt_in
        self.salt_out = salt_out

    def seal(self, packet: Packet) -> bytes:
        header = packet.header()
        nonce = self.salt_out + struct.pack("<I", packet.seq & 0xFFFFFFFF)
        encryptor = Cipher(algorithms.AES(self.key_out), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(header)
        body = encryptor.update(bytes(packet.payload)) + encryptor.finalize()
        return encryptor.tag[:TAG_SIZE] + header + body

    def open(self, datagram: bytes) -> Packet | None:
        """The packet, or None when the datagram does not decrypt with these keys."""
        if not HEADER_SIZE <= len(datagram) <= MAX_DATAGRAM or datagram[HEADER_SIZE - 1] != MAGIC:
            return None
        word, seq, ack, ack_bits, flags, _ = struct.unpack_from("<IIIQBB", datagram, TAG_SIZE)
        nonce = self.salt_in + struct.pack("<I", seq)
        mode = modes.GCM(nonce, datagram[:TAG_SIZE], min_tag_length=TAG_SIZE)
        decryptor = Cipher(algorithms.AES(self.key_in), mode).decryptor()
        decryptor.authenticate_additional_data(datagram[TAG_SIZE:HEADER_SIZE])
        try:
            payload = decryptor.update(datagram[HEADER_SIZE:]) + decryptor.finalize()
        except InvalidTag:
            return None
        return Packet(word >> 4, word & 0xF, seq, ack, ack_bits, flags, payload)


class ReceiveWindow:
    """What we got from the client, for the ack and ack_bits of our next header."""

    def __init__(self) -> None:
        self.expected = 0
        self.bits = 0

    def accept(self, seq: int) -> bool:
        """False for a packet older than the expected one: the client drops those too."""
        gap = (seq - self.expected) & 0xFFFFFFFF
        if gap >= 0x80000000:
            return False
        shift = gap + 1
        self.bits = ((self.bits << shift) | 1) & 0xFFFFFFFFFFFFFFFF if shift < ACK_WINDOW else 1
        self.expected = (seq + 1) & 0xFFFFFFFF
        return True


def delivery(seq: int, ack: int, ack_bits: int) -> bool | None:
    """Did the client get our packet `seq`, by its ack and ack_bits? None while it is not known."""
    back = (ack - 1 - seq) & 0xFFFFFFFF
    if back >= 0x80000000:
        return None  # newer than anything acked yet
    if back >= ACK_WINDOW:
        return False  # it fell out of the window unacked
    return bool((ack_bits >> back) & 1)
