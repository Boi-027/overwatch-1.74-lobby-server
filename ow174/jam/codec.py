"""JAM message codec driven by the client's own message schemas.

data/schemas_174.json is extracted from a running 1.74 client by tools/dump_schemas.py. It maps a
protocol CRC (hex) to its messages, and a message id to its list of fields. A field is
{type, off, size, count, array, fields?}. Decoded values are dicts keyed by the field's in-memory
offset ("+0x78"), with nested dicts for structs and lists for arrays.

How each field type is written:
  0 bool        one bit, lowest bit first
  1-9           little-endian integers of 1, 2, 4 or 8 bytes (see SCALAR_FORMATS)
  10, 11        f32, f64
  12, 13        UTF-8 string ending in a zero byte
  14 struct     its fields, inline
  15 blob       u32 length, then the bytes
  arrays        u32 count, then the elements (fixed-size arrays have no count)

Bools fill bytes across the whole message: a run of bools goes on over the edges of structs, array
elements and fixed arrays, and only a byte-sized write (a number, string, blob or array count)
starts a new byte. Tested in game: in 20802 the 5 flags of +0x108 and the first 4 of +0x10D form
one run of 9 bits. ProCore's research of the client's encoder describes the same rule.
"""

import json
import struct
from pathlib import Path

from ow174.paths import DATA_DIR

SCHEMA_PATH = DATA_DIR / "schemas_174.json"

# Frames of these protocols carry a u32 between the protocol byte and the message id. The client
# registers them with header flag 8 (read live: registration +8 = 0xA, others 0x2) and reads the u32
# before the id (0x7FF789471C50); its protocol table marks them with flag 2 (+0xC of the entry). We
# send 0. With the u32 after the id instead, only messages with id offset 0 (52300, 55500) worked.
PREFIXED_PROTOCOLS = {0xBCD57A46, 0xBDDBF58A}

BOOL = 0
STRING_TYPES = (12, 13)
STRUCT = 14
BLOB = 15
SCALAR_FORMATS = {1: "B", 2: "B", 3: "b", 4: "H", 5: "h", 6: "I", 7: "i", 8: "Q", 9: "q", 10: "f", 11: "d"}
# Arrays longer than this are only accepted when the rest of the frame could hold them.
MAX_ARRAY_COUNT = 0x40000


class DecodeError(Exception):
    pass


class _Bits:
    """The byte that the current run of bools is filling."""

    def __init__(self) -> None:
        self.byte = 0
        self.bit = 8  # 8 means no byte is open

    def close(self) -> None:
        self.bit = 8


def _has_count_prefix(field) -> bool:
    return field["array"] or field["count"] == -1


def _key(field) -> str:
    return f"+0x{field['off']:X}"


def _default(field):
    if field["type"] == STRUCT:
        return {}
    if field["type"] in STRING_TYPES:
        return ""
    if field["type"] == BLOB:
        return b""
    return 0


def _read_u32(data: bytes, pos: int, what: str) -> int:
    if pos + 4 > len(data):
        raise DecodeError(f"{what} past end")
    return struct.unpack_from("<I", data, pos)[0]


def _read_value(field, data: bytes, pos: int, bits: _Bits):
    """Read one element of a field. Returns (value, next position)."""
    kind = field["type"]
    if kind == BOOL:
        if bits.bit >= 8:
            if pos >= len(data):
                raise DecodeError("bool past end")
            bits.byte, bits.bit, pos = pos, 0, pos + 1
        value = bool((data[bits.byte] >> bits.bit) & 1)
        bits.bit += 1
        return value, pos
    if kind == STRUCT:
        return _read_fields(field.get("fields", []), data, pos, bits)
    bits.close()
    if kind in STRING_TYPES:
        end = data.find(b"\x00", pos)
        if end < 0:
            raise DecodeError("unterminated string")
        return data[pos:end].decode("utf-8", "replace"), end + 1
    if kind == BLOB:
        size = _read_u32(data, pos, "blob length")
        start = pos + 4
        if start + size > len(data):
            raise DecodeError("blob past end")
        return data[start : start + size], start + size
    fmt = SCALAR_FORMATS.get(kind)
    if fmt is None:
        raise DecodeError(f"unknown field type {kind}")
    size = struct.calcsize(fmt)
    if pos + size > len(data):
        raise DecodeError("scalar past end")
    return struct.unpack_from("<" + fmt, data, pos)[0], pos + size


def _read_values(field, count: int, data: bytes, pos: int, bits: _Bits):
    values = []
    for _ in range(count):
        value, pos = _read_value(field, data, pos, bits)
        values.append(value)
    return values, pos


def _read_fields(fields, data: bytes, pos: int, bits: _Bits):
    out = {}
    for field in fields:
        if _has_count_prefix(field):
            bits.close()
            count = _read_u32(data, pos, "array count")
            pos += 4
            too_long = count > len(data) - pos + 1 and count > MAX_ARRAY_COUNT
            if too_long and field["type"] != BOOL:
                raise DecodeError(f"array count {count} too large")
            out[_key(field)], pos = _read_values(field, count, data, pos, bits)
        elif field["count"] > 1:
            out[_key(field)], pos = _read_values(field, field["count"], data, pos, bits)
        else:
            out[_key(field)], pos = _read_value(field, data, pos, bits)
    return out, pos


def _write_value(field, value, out: bytearray, bits: _Bits) -> None:
    kind = field["type"]
    if kind == BOOL:
        if bits.bit >= 8:
            out.append(0)
            bits.byte, bits.bit = len(out) - 1, 0
        if value:
            out[bits.byte] |= 1 << bits.bit
        bits.bit += 1
        return
    if kind == STRUCT:
        _write_fields(field.get("fields", []), value or {}, out, bits)
        return
    bits.close()
    if kind in STRING_TYPES:
        out += str(value).replace("\x00", "").encode("utf-8") + b"\x00"
    elif kind == BLOB:
        blob = bytes(value)
        out += struct.pack("<I", len(blob)) + blob
    else:
        out += struct.pack("<" + SCALAR_FORMATS[kind], value)


def _write_fixed_array(field, value, out: bytearray, bits: _Bits) -> None:
    """Write exactly field["count"] elements, padding with defaults or cutting extra ones."""
    count = field["count"]
    elements = list(value or [])
    while len(elements) < count:
        elements.append(_default(field))
    for element in elements[:count]:
        _write_value(field, element, out, bits)


def _write_fields(fields, value, out: bytearray, bits: _Bits) -> None:
    for field in fields:
        item = value.get(_key(field)) if isinstance(value, dict) else None
        if _has_count_prefix(field):
            elements = item or []
            bits.close()
            out += struct.pack("<I", len(elements))
            for element in elements:
                _write_value(field, element, out, bits)
        elif field["count"] > 1:
            _write_fixed_array(field, item, out, bits)
        else:
            _write_value(field, _default(field) if item is None else item, out, bits)


class Schemas:
    """All 1.74 lobby protocol schemas, keyed by protocol CRC and message id."""

    def __init__(self, path: Path = SCHEMA_PATH):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.groups = {}
        for crc_hex, messages in raw.items():
            self.groups[int(crc_hex, 16)] = {int(msg_id): fields for msg_id, fields in messages.items()}
        self._first_ids = {crc: min(messages) for crc, messages in self.groups.items()}

    def fields(self, crc: int, msg_id: int):
        try:
            return self.groups[crc][msg_id]
        except KeyError:
            raise KeyError(f"no schema for protocol {crc:08X} message {msg_id}") from None

    def base(self, crc: int) -> int:
        """The lowest message id of a protocol. The wire carries ids as an offset from it."""
        return self._first_ids[crc]

    def header(self, crc: int, wire: int, msg_id: int) -> bytes:
        """The bytes of a frame before the message fields: the protocol's wire byte, the u32 of
        PREFIXED_PROTOCOLS, then the message id as an offset."""
        prefix = bytes(4) if crc in PREFIXED_PROTOCOLS else b""
        return bytes([wire]) + prefix + bytes([msg_id - self.base(crc)])

    def split(self, crc: int, frame: bytes) -> tuple[int, bytes]:
        """The message id and the fields of a frame that header() began."""
        start = 5 if crc in PREFIXED_PROTOCOLS else 1
        return self.base(crc) + frame[start], frame[start + 1 :]

    def decode(self, crc: int, msg_id: int, body: bytes, strict=True) -> dict:
        value, pos = self.read(crc, msg_id, body)
        if strict and pos != len(body):
            raise DecodeError(f"{len(body) - pos} bytes left over")
        return value

    def read(self, crc: int, msg_id: int, data: bytes, pos: int = 0) -> tuple[dict, int]:
        """Decode the message whose fields start at `pos`: the value and where the message ends. The
        game link packs messages back to back with no length."""
        return _read_fields(self.fields(crc, msg_id), data, pos, _Bits())

    def encode(self, crc: int, msg_id: int, value: dict) -> bytes:
        out = bytearray()
        _write_fields(self.fields(crc, msg_id), value or {}, out, _Bits())
        return bytes(out)

    def empty(self, crc: int, msg_id: int) -> dict:
        """A value with every field at its default, useful as a template."""
        return self.decode(crc, msg_id, self.encode(crc, msg_id, {}))
