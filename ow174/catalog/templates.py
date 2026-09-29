"""Server messages recorded from a retail 1.68 session, stored as JSON templates.

The lobby replays them (catalogs, config, store, arcade, presence) after swapping the recorded
account id and player name for the local player's. The templates were decoded with the 1.74 schemas;
groups whose layout changed between the builds are not included.
"""

import json
from pathlib import Path

from ow174.jam.values import clone
from ow174.paths import DATA_DIR

RETAIL_ACCOUNT = 0x425AE13F  # the recorded player's account id
REDACTED_NAME = "XXXXXXXXXXXXXXX"  # the recorded BattleTag, anonymized
TEMPLATE_FORMAT = 1


def _restore(value):
    """Turn the JSON form of a decoded value back into Python (`{"$bytes": hex}` becomes bytes)."""
    if isinstance(value, dict):
        if set(value) == {"$bytes"}:
            return bytes.fromhex(value["$bytes"])
        return {key: _restore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore(item) for item in value]
    return value


class RetailTemplates:
    """The recorded server messages, in the order the retail server sent them."""

    def __init__(self, path: Path = DATA_DIR / "retail_templates.json") -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != TEMPLATE_FORMAT:
            raise ValueError("Unsupported retail template format")
        self._messages = []
        for crc, msg_id, value in data["server"]:
            self._messages.append((int(crc, 16), int(msg_id), _restore(value)))

    def first(self, crc: int, msg_id: int) -> dict | None:
        """A copy of the first recorded message with this id."""
        for message_crc, message_id, value in self._messages:
            if message_crc == crc and message_id == msg_id:
                return clone(value)
        return None

    def all(self, crc: int, msg_id: int | None = None) -> list[tuple[int, dict]]:
        """Copies of every recorded message of a group (or of one id) as (message id, value)."""
        found = []
        for message_crc, message_id, value in self._messages:
            if message_crc == crc and (msg_id is None or message_id == msg_id):
                found.append((message_id, clone(value)))
        return found


def personalize(value, name: str, account_lo: int = RETAIL_ACCOUNT):
    """Swap the recorded name (in strings and protobuf blobs) and account id for ours."""
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
    start = blob.find(REDACTED_NAME.encode())
    # Only a one-byte length (below 0x80) is handled, so the new name is cut to 127 bytes.
    if start < 2 or blob[start - 1] >= 0x80:
        return blob
    old_length = blob[start - 1]
    new_name = name.encode("utf-8")[:0x7F]
    return blob[: start - 1] + bytes([len(new_name)]) + new_name + blob[start + old_length :]
