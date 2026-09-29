"""JAM message codec driven by the client's own message schemas.

data/schemas_174.json is extracted from a running 1.74 client by tools/dump_schemas.py. It maps a
protocol CRC (hex) to its messages, and a message id to its list of fields. A field is
{type, off, size, count, array, fields?}. Decoded values are dicts keyed by the field's in-memory
offset ("+0x78"), with nested dicts for structs and lists for arrays.

How each field type is written:
  0 bool        bit-packed, lowest bit first; a run of bools shares one byte
  1-9           little-endian integers of 1, 2, 4 or 8 bytes (see SCALAR_FORMATS)
  10, 11        f32, f64
  12, 13        UTF-8 string ending in a zero byte
  14 struct     its fields, inline
  15 blob       u32 length, then the bytes
  arrays        u32 count, then the elements (fixed-size arrays have no count)
"""

import json
import struct
from pathlib import Path

from ow174.paths import DATA_DIR

SCHEMA_PATH = DATA_DIR / "schemas_174.json"

# Frames of these protocols carry a u32 before the message fields, although the schema has no such
# field. The client only applies the 55500 account features when it is there.
PREFIXED_PROTOCOLS = {0xBCD57A46}

BOOL = 0
STRING_TYPES = (12, 13)
STRUCT = 14
BLOB = 15
SCALAR_FORMATS = {1: "B", 2: "B", 3: "b", 4: "H", 5: "h", 6: "I", 7: "i", 8: "Q", 9: "q", 10: "f", 11: "d"}
# Arrays longer than this are only accepted when the rest of the frame could hold them.
MAX_ARRAY_COUNT = 0x40000


class DecodeError(Exception):
    pass


def _is_packed_bool(field) -> bool:
    return field["type"] == BOOL and not field["array"] and field["count"] in (0, 1, -1)


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


def _read_value(field, data: bytes, pos: int):
    """Read one element of a non-packed field. Returns (value, next position)."""
    kind = field["type"]
    if kind in STRING_TYPES:
        end = data.find(b"\x00", pos)
        if end < 0:
            raise DecodeError("unterminated string")
        return data[pos:end].decode("utf-8", "replace"), end + 1
    if kind == STRUCT:
        return _read_fields(field.get("fields", []), data, pos)
    if kind == BLOB:
        size = _read_u32(data, pos, "blob length")
        start = pos + 4
        if start + size > len(data):
            raise DecodeError("blob past end")
        return data[start : start + size], start + size
    if kind == BOOL:
        if pos >= len(data):
            raise DecodeError("bool past end")
        return bool(data[pos]), pos + 1
    fmt = SCALAR_FORMATS.get(kind)
    if fmt is None:
        raise DecodeError(f"unknown field type {kind}")
    size = struct.calcsize(fmt)
    if pos + size > len(data):
        raise DecodeError("scalar past end")
    return struct.unpack_from("<" + fmt, data, pos)[0], pos + size


def _read_values(field, count: int, data: bytes, pos: int):
    values = []
    for _ in range(count):
        value, pos = _read_value(field, data, pos)
        values.append(value)
    return values, pos


def _read_fields(fields, data: bytes, pos: int):
    out = {}
    bool_byte = 0
    bool_bit = 8  # 8 means no bool byte is open
    for field in fields:
        if _is_packed_bool(field):
            if bool_bit >= 8:
                if pos >= len(data):
                    raise DecodeError("bool past end")
                bool_byte, bool_bit, pos = pos, 0, pos + 1
            out[_key(field)] = bool((data[bool_byte] >> bool_bit) & 1)
            bool_bit += 1
            continue
        bool_bit = 8
        if _has_count_prefix(field):
            count = _read_u32(data, pos, "array count")
            pos += 4
            too_long = count > len(data) - pos + 1 and count > MAX_ARRAY_COUNT
            if too_long and field["type"] != BOOL:
                raise DecodeError(f"array count {count} too large")
            out[_key(field)], pos = _read_values(field, count, data, pos)
        elif field["count"] > 1:
            out[_key(field)], pos = _read_values(field, field["count"], data, pos)
        else:
            out[_key(field)], pos = _read_value(field, data, pos)
    return out, pos


def _write_value(field, value, out: bytearray) -> None:
    kind = field["type"]
    if kind in STRING_TYPES:
        out += str(value).replace("\x00", "").encode("utf-8") + b"\x00"
    elif kind == STRUCT:
        _write_fields(field.get("fields", []), value or {}, out)
    elif kind == BLOB:
        blob = bytes(value)
        out += struct.pack("<I", len(blob)) + blob
    elif kind == BOOL:
        out.append(1 if value else 0)
    else:
        out += struct.pack("<" + SCALAR_FORMATS[kind], value)


def _write_fixed_array(field, value, out: bytearray) -> None:
    """Write exactly field["count"] elements, padding with defaults or cutting extra ones."""
    count = field["count"]
    elements = list(value or [])
    while len(elements) < count:
        elements.append(_default(field))
    for element in elements[:count]:
        _write_value(field, element, out)


def _write_fields(fields, value, out: bytearray) -> None:
    bool_byte = 0
    bool_bit = 8  # 8 means no bool byte is open
    for field in fields:
        item = value.get(_key(field)) if isinstance(value, dict) else None
        if _is_packed_bool(field):
            if bool_bit >= 8:
                out.append(0)
                bool_byte, bool_bit = len(out) - 1, 0
            if item:
                out[bool_byte] |= 1 << bool_bit
            bool_bit += 1
            continue
        bool_bit = 8
        if _has_count_prefix(field):
            elements = item or []
            out += struct.pack("<I", len(elements))
            for element in elements:
                _write_value(field, element, out)
        elif field["count"] > 1:
            _write_fixed_array(field, item, out)
        elif item is None:
            _write_value(field, _default(field), out)
        else:
            _write_value(field, item, out)


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

    def decode(self, crc: int, msg_id: int, body: bytes, strict=True) -> dict:
        start = 4 if crc in PREFIXED_PROTOCOLS else 0
        value, pos = _read_fields(self.fields(crc, msg_id), body, start)
        if strict and pos != len(body):
            raise DecodeError(f"{len(body) - pos} bytes left over")
        return value

    def encode(self, crc: int, msg_id: int, value: dict) -> bytes:
        out = bytearray(4 if crc in PREFIXED_PROTOCOLS else 0)
        _write_fields(self.fields(crc, msg_id), value or {}, out)
        return bytes(out)

    def empty(self, crc: int, msg_id: int) -> dict:
        """A value with every field at its default, useful as a template."""
        return self.decode(crc, msg_id, self.encode(crc, msg_id, {}))
