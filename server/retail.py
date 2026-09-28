"""
Runtime server templates (derived from the public 1.68 capture, decoded with 1.74 schemas).
The shareable distribution loads retail_templates.json; raw captures are not required.
The legacy reader below remains available for independently supplied research captures.

srv_dec.bin / cli_dec.bin hold the decrypted streams. Their first byte indexes
the 1.68 session's own group list (cli_dec's announcement), so each frame is
mapped 1.68 index -> protocol CRC -> 1.74 schema. Groups whose schema changed
between the builds have a new CRC; RENAMED_68 maps the ones whose retail frames
still decode exactly under the 1.74 schema of the renamed group.

The capture's BattleTags were redacted to "XXXXXXXXXXXXXXX" padded with NULs to
28 bytes; that padding is removed before decoding so the strings parse.
"""

import struct
import json
from pathlib import Path

try:
    from jam_codec import Schemas, DecodeError, clone
except ImportError:
    from .jam_codec import Schemas, DecodeError, clone

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

RETAIL_ACCOUNT = 0x425AE13F
REDACTED_NAME = "XXXXXXXXXXXXXXX"
_REDACTED_FIELD = REDACTED_NAME.encode() + b"\x00" * 13

RENAMED_68 = {
    0x951887D5: 0xF5FF548A,  # ClientInLobbyConnect
    0x58D79088: 0x1A9879A4,
    0xA8094BA4: 0x5894D085,  # replay storage
    0x696D750A: 0x4BAD7A7E,  # store
    0x3EAB7F6A: 0xAA91BE18,
    0x78AD8177: 0x2411DE56,  # party state (rating records changed size)
    0x79324A55: 0x21286B49,  # custom game maps and modes (only 23301, the map lists, still decodes)
}

TELEMETRY = {0xC64B397E, 0x692C511B}


def _frames(data: bytes, start: int):
    pos = start
    while pos + 3 <= len(data):
        n = int.from_bytes(data[pos:pos + 3], "big")
        if pos + 3 + n > len(data):
            break
        yield data[pos + 3: pos + 3 + n]
        pos += 3 + n


class RetailCapture:
    def __init__(self, schemas: Schemas, data_dir: Path = DATA_DIR):
        self.schemas = schemas
        template_path = data_dir / "retail_templates.json"
        if template_path.is_file():
            def restore(value):
                if isinstance(value, dict):
                    if set(value) == {"$bytes"}:
                        return bytes.fromhex(value["$bytes"])
                    return {key: restore(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [restore(item) for item in value]
                return value
            data = json.loads(template_path.read_text(encoding="utf-8"))
            if data.get("format") != 1:
                raise ValueError("Unsupported retail template format")
            self.server = [(int(crc, 16), int(mid), restore(value)) for crc, mid, value in data["server"]]
            self.client, self.index_68 = [], {}
            return
        cli = (data_dir / "cli_dec.bin").read_bytes()
        first = next(_frames(cli, 0))
        count = struct.unpack_from("<I", first, 2)[0]
        self.index_68 = {i + 1: struct.unpack_from("<I", first, 6 + i * 4)[0] for i in range(count)}
        self.server = self._decode_stream((data_dir / "srv_dec.bin").read_bytes(), 292)
        self.client = self._decode_stream(cli, 0)

    def crc_174(self, index_68: int):
        crc = self.index_68.get(index_68)
        return RENAMED_68.get(crc, crc)

    def _decode_stream(self, data: bytes, start: int):
        out = []
        for frame in _frames(data, start):
            if len(frame) < 2 or frame[0] == 0:
                continue
            crc = self.crc_174(frame[0])
            if crc not in self.schemas.groups:
                continue
            msg_id = self.schemas.base(crc) + frame[1]
            if msg_id not in self.schemas.groups[crc]:
                continue
            value = self._decode(crc, msg_id, frame[2:])
            if value is not None:
                out.append((crc, msg_id, value))
        return out

    def _decode(self, crc, msg_id, body):
        for candidate in (body, body.replace(_REDACTED_FIELD, REDACTED_NAME.encode() + b"\x00")):
            try:
                return self.schemas.decode(crc, msg_id, candidate)
            except (DecodeError, struct.error, KeyError):
                continue
        return None

    def first(self, crc: int, msg_id: int):
        for c, m, v in self.server:
            if c == crc and m == msg_id:
                return clone(v)
        return None

    def all(self, crc: int, msg_id: int = None):
        return [(m, clone(v)) for c, m, v in self.server if c == crc and (msg_id is None or m == msg_id)]


def personalize(value, name: str, account_lo: int = RETAIL_ACCOUNT):
    """Swap the capture's redacted name (strings and protobuf blobs) and account id for ours."""
    if isinstance(value, dict):
        return {k: personalize(v, name, account_lo) for k, v in value.items()}
    if isinstance(value, list):
        return [personalize(v, name, account_lo) for v in value]
    if isinstance(value, str) and value.startswith(REDACTED_NAME):
        return name
    if isinstance(value, bytes) and REDACTED_NAME.encode() in value:
        return _personalize_blob(value, name)
    if isinstance(value, int) and not isinstance(value, bool) and value == RETAIL_ACCOUNT:
        return account_lo
    return value


def _personalize_blob(blob: bytes, name: str) -> bytes:
    """Presence blobs carry the name as a protobuf string (tag, varint length, bytes)."""
    marker = REDACTED_NAME.encode()
    i = blob.find(marker)
    if i < 2 or blob[i - 1] >= 0x80:
        return blob
    old_len = blob[i - 1]
    new = name.encode("utf-8")
    if len(new) >= 0x80:
        new = new[:0x7F]
    return blob[: i - 1] + bytes([len(new)]) + new + blob[i + old_len:]
