"""
JAM message codec driven by the client's own message schemas.

data/schemas_174.json is extracted from a running 1.74 client by
tools/dump_schemas.py: {protocol CRC hex: {message id: [field, ...]}}, where a
field is {type, off, size, count, array, fields?}. Values are dicts keyed by
the field's in-memory offset ("+0x78"), nested for structs, lists for arrays.

Wire rules (see the rentry notes linked in README):
  bool            bit-packed LSB first; a run of bools shares one byte
  1-3 / 4-5       1 / 2 bytes          6, 7, 10   4 bytes (10 = f32)
  8, 9, 11        8 bytes (11 = f64)   12, 13     UTF-8 + NUL
  14 struct       fields inline        15 blob    u32 length + bytes
  array           u32 count + elements (fixed-count arrays have no count)
"""

import copy
import json
import struct
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data" / "schemas_174.json"

# Protocols whose frames carry a u32 before the message fields. Seen on 55500
# (account features): the retail frame has it, and the client only applies the
# list when it is there, although the schema has no such field.
PREFIXED_PROTOCOLS = {0xBCD57A46}

_FMT = {1: "B", 2: "B", 3: "b", 4: "H", 5: "h", 6: "I", 7: "i", 8: "Q", 9: "q", 10: "f", 11: "d"}
_DEFAULTS = {12: "", 13: "", 15: b""}


class DecodeError(Exception):
    pass


def _is_bool(f):
    return f["type"] == 0 and not f["array"] and f["count"] in (0, 1, -1)


def _is_dynamic(f):
    return f["array"] or f["count"] == -1


def _key(f):
    return f"+0x{f['off']:X}"


def _read_scalar(f, data, pos):
    t = f["type"]
    if t in (12, 13):
        end = data.find(b"\x00", pos)
        if end < 0:
            raise DecodeError("unterminated string")
        return data[pos:end].decode("utf-8", "replace"), end + 1
    if t == 14:
        return _read_fields(f.get("fields", []), data, pos)
    if t == 15:
        if pos + 4 > len(data):
            raise DecodeError("blob length past end")
        n = struct.unpack_from("<I", data, pos)[0]
        if pos + 4 + n > len(data):
            raise DecodeError("blob past end")
        return data[pos + 4: pos + 4 + n], pos + 4 + n
    if t == 0:
        if pos >= len(data):
            raise DecodeError("bool past end")
        return bool(data[pos]), pos + 1
    fmt = _FMT.get(t)
    if fmt is None:
        raise DecodeError(f"unknown field type {t}")
    n = struct.calcsize(fmt)
    if pos + n > len(data):
        raise DecodeError("scalar past end")
    return struct.unpack_from("<" + fmt, data, pos)[0], pos + n


def _read_fields(fields, data, pos):
    out, bit, bool_at = {}, 8, 0
    for f in fields:
        if _is_bool(f):
            if bit >= 8:
                if pos >= len(data):
                    raise DecodeError("bool past end")
                bool_at, bit, pos = pos, 0, pos + 1
            out[_key(f)] = bool((data[bool_at] >> bit) & 1)
            bit += 1
            continue
        bit = 8
        if _is_dynamic(f):
            if pos + 4 > len(data):
                raise DecodeError("array count past end")
            n = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if n > len(data) - pos + 1 and f["type"] != 0 and n > 0x40000:
                raise DecodeError(f"array count {n} too large")
            vals = []
            for _ in range(n):
                v, pos = _read_scalar(f, data, pos)
                vals.append(v)
            out[_key(f)] = vals
        elif f["count"] > 1:
            vals = []
            for _ in range(f["count"]):
                v, pos = _read_scalar(f, data, pos)
                vals.append(v)
            out[_key(f)] = vals
        else:
            out[_key(f)], pos = _read_scalar(f, data, pos)
    return out, pos


def _write_scalar(f, v, out):
    t = f["type"]
    if t in (12, 13):
        out += str(v).replace("\x00", "").encode("utf-8") + b"\x00"
    elif t == 14:
        _write_fields(f.get("fields", []), v or {}, out)
    elif t == 15:
        b = bytes(v)
        out += struct.pack("<I", len(b)) + b
    elif t == 0:
        out.append(1 if v else 0)
    else:
        out += struct.pack("<" + _FMT[t], v)


def _default(f):
    if f["type"] == 14:
        return {}
    return _DEFAULTS.get(f["type"], 0)


def _write_fields(fields, val, out):
    bit, bool_at = 8, 0
    for f in fields:
        v = val.get(_key(f)) if isinstance(val, dict) else None
        if _is_bool(f):
            if bit >= 8:
                out.append(0)
                bool_at, bit = len(out) - 1, 0
            if v:
                out[bool_at] |= 1 << bit
            bit += 1
            continue
        bit = 8
        if _is_dynamic(f):
            v = v or []
            out += struct.pack("<I", len(v))
            for e in v:
                _write_scalar(f, e, out)
        elif f["count"] > 1:
            v = list(v or [])
            v += [_default(f)] * (f["count"] - len(v))
            for e in v[: f["count"]]:
                _write_scalar(f, e, out)
        else:
            _write_scalar(f, _default(f) if v is None else v, out)


class Schemas:
    """All 1.74 lobby protocol schemas, keyed by protocol CRC and message id."""

    def __init__(self, path: Path = SCHEMA_PATH):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.groups = {int(c, 16): {int(m): f for m, f in g.items()} for c, g in raw.items()}
        self.bases = {crc: min(g) for crc, g in self.groups.items()}

    def fields(self, crc: int, msg_id: int):
        try:
            return self.groups[crc][msg_id]
        except KeyError:
            raise KeyError(f"no schema for protocol {crc:08X} message {msg_id}") from None

    def base(self, crc: int) -> int:
        return self.bases[crc]

    def decode(self, crc: int, msg_id: int, body: bytes, strict=True) -> dict:
        start = 4 if crc in PREFIXED_PROTOCOLS else 0
        val, pos = _read_fields(self.fields(crc, msg_id), body, start)
        if strict and pos != len(body):
            raise DecodeError(f"{len(body) - pos} bytes left over")
        return val

    def encode(self, crc: int, msg_id: int, value: dict) -> bytes:
        out = bytearray(4 if crc in PREFIXED_PROTOCOLS else 0)
        _write_fields(self.fields(crc, msg_id), value or {}, out)
        return bytes(out)

    def empty(self, crc: int, msg_id: int) -> dict:
        """A value with every field at its default, useful as a template."""
        return self.decode(crc, msg_id, self.encode(crc, msg_id, {}))


def id16(lo: int, hi: int = 0x0100000000000000) -> dict:
    return {"+0x0": lo, "+0x8": hi}


def clone(value):
    return copy.deepcopy(value)


def to_jsonable(value):
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return {"blob": value.hex()}
    if isinstance(value, int) and not isinstance(value, bool) and value > 0xFFFFFF:
        return f"0x{value:016X}"
    return value
