"""JAM messages on the game link: the message channel at the start of every payload.

The channel (reader 0x7FF789B7A480, writer 0x7FF789B7ADE0):
    1 bit: any messages
    if set: 4 bits reliable count, 4 bits unreliable count; when either is not 0, pad to a byte, then
        reliable:   u32 seq, u8 protocol index, u8 message index, JAM fields   (in seq order)
        unreliable: u8 protocol index, u8 message index, JAM fields
    the stream goes on at the byte after the last message.
The client delivers reliable messages strictly in seq order: it parks one that comes early and drops
one it already had, so a lost reliable message has to be sent again or everything after it waits.

There is no protocol announcement on this link. The index points into a fixed table the client
builds in its message system (0x7FF789B797C0); the message id is the protocol's first id plus the
message index.
"""

import logging

from ow174.game.bits import BitReader, BitWriter
from ow174.jam.codec import DecodeError, Schemas
from ow174.paths import DATA_DIR

log = logging.getLogger("ow174.game")

GAME_SCHEMA_PATH = DATA_DIR / "game_schemas_174.json"
MAX_PER_KIND = 15  # the counts are 4 bits

# The table, in index order: (protocol CRC, first message id).
PROTOCOLS = (
    (0xC77F6403, 20300),  # 0 ClientInInstance
    (0xA7FBBD0C, 21600),  # 1 ClientOutInstance
    (0x8F89E0DF, 25000),  # 2 ClientInCombat
    (0x82A2F991, 27500),  # 3 ClientInVoice
    (0x9F0A9F88, 25800),  # 4 ClientInStats
    (0xA9385A52, 30700),  # 5 ClientOutCheats
    (0x398E145E, 42900),  # 6
    (0x741EC7B2, 42800),  # 7
    (0x716AD5D9, 22300),  # 8 ClientOutPvP
    (0x145CE2DA, 25700),  # 9 ClientOutStats
    (0xCF043764, 42700),  # 10 ClientOutSpectate
)

_schemas: Schemas | None = None


def schemas() -> Schemas:
    global _schemas
    if _schemas is None:
        _schemas = Schemas(GAME_SCHEMA_PATH)
    return _schemas


def locate(msg_id: int) -> tuple[int, int, int]:
    """(protocol index, protocol CRC, message index) of a message id: the protocol with the highest
    first id at or below it."""
    below = [index for index, (_, first) in enumerate(PROTOCOLS) if first <= msg_id < first + 256]
    if not below:
        raise KeyError(f"message {msg_id} is not on the game link")
    index = max(below, key=lambda number: PROTOCOLS[number][1])
    crc, first = PROTOCOLS[index]
    return index, crc, msg_id - first


def encode(msg_id: int, value: dict) -> bytes:
    index, crc, offset = locate(msg_id)
    return bytes([index, offset]) + schemas().encode(crc, msg_id, value)


def write_channel(bits: BitWriter, reliable=(), unreliable=()) -> None:
    """reliable: [(seq, encoded message)], unreliable: [encoded message]; see encode()."""
    if not reliable and not unreliable:
        bits.bit(0)
        return
    if len(reliable) > MAX_PER_KIND or len(unreliable) > MAX_PER_KIND:
        raise ValueError(f"at most {MAX_PER_KIND} messages of a kind per packet")
    bits.bit(1)
    bits.bits(len(reliable), 4)
    bits.bits(len(unreliable), 4)
    bits.align()
    for seq, message in reliable:
        bits.raw(seq.to_bytes(4, "little") + message)
    for message in unreliable:
        bits.raw(message)


def read_channel(reader: BitReader) -> tuple[list, list]:
    """(reliable [(seq, msg_id, value)], unreliable [(msg_id, value)]). The reader is left where the
    next channel starts. With both counts 0 there is no padding: the client's input record then
    starts at bit 9."""
    reliable, unreliable = [], []
    if not reader.bit():
        return reliable, unreliable
    n_reliable, n_unreliable = reader.bits(4), reader.bits(4)
    if not n_reliable and not n_unreliable:
        return reliable, unreliable
    reader.align()
    data, pos = reader.data, reader.pos >> 3
    for _ in range(n_reliable):
        seq = int.from_bytes(data[pos : pos + 4], "little")
        msg_id, value, pos = _read_message(data, pos + 4)
        reliable.append((seq, msg_id, value))
    for _ in range(n_unreliable):
        msg_id, value, pos = _read_message(data, pos)
        unreliable.append((msg_id, value))
    reader.pos = pos * 8
    return reliable, unreliable


def _read_message(data: bytes, pos: int) -> tuple[int, dict, int]:
    if pos + 2 > len(data):
        raise DecodeError("message header past the end")
    index, offset = data[pos], data[pos + 1]
    if index >= len(PROTOCOLS):
        raise DecodeError(f"unknown protocol index {index}")
    crc, first = PROTOCOLS[index]
    msg_id = first + offset
    value, end = schemas().read(crc, msg_id, data, pos + 2)
    return msg_id, value, end
