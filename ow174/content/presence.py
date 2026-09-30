"""Battle.net presence records (27100 +0xA8 and 27113), cloned from the capture for our own ids.

Presence values are protobuf blobs that hold the recorded account ids in three forms: as fixed64
fields, inside the "<id>#1" game account name, and as varints inside a message value. All of them
are rewritten.
"""

import struct
import time

from ow174.accounts.profile import Profile, battle_tag
from ow174.catalog.templates import RETAIL_ACCOUNT, RetailTemplates, personalize
from ow174.content.identity import Identity
from ow174.jam.groups import FRIENDS

RETAIL_APP_ACCOUNT = 0x3A169DEF  # the capture's Battle.net App game account
RETAIL_PRO_ACCOUNT = 0x3A169E4C  # the capture's Overwatch game account
RETAIL_FULL_NAME = b"ExampleUser"  # anonymized template marker, replaced with the profile name

# Our own game account ids are the account id with these bits flipped, so they are stable per
# player and never equal the account id.
APP_ACCOUNT_BITS = 0x0A0A0000
PRO_ACCOUNT_BITS = 0x0B0B0000

# A presence field key is {program "BN", group, field, unique id}. Field 1 of group 2 (a game
# account) says whether it is online; its value is a Variant bool (field 2).
PRESENCE_PROGRAM = 16974  # "BN", the program of the Battle.net presence fields
GAME_ACCOUNT_ONLINE = bytes.fromhex("08ce8401100218012000")
OFFLINE = b"\x10\x00"
ONLINE_BOOL = b"\x10\x01"

# The status dropdown (message 27011 +0x78): its value maps to how friends see the player.
STATUS_ONLINE, STATUS_AWAY, STATUS_BUSY, STATUS_OFFLINE = 1, 2, 3, 4
STATUS_NAMES = {
    STATUS_ONLINE: "online",
    STATUS_AWAY: "away",
    STATUS_BUSY: "busy",
    STATUS_OFFLINE: "appear offline",
}

# Confirmed live against the client: away is the game-account bool (group 2, field 10), which the
# capture carries as false; busy is an account-wide bool (group 1, field 11) not in the capture, so it
# is appended to the account record when set. Either makes friends show the player yellow or red.
AWAY_FIELD = (2, 10)
BUSY_FIELD = (1, 11)

# Last online and session times are Variant int values (field 3) in microseconds since 1970.
INT_VALUE_TAG = b"\x18"
MICROSECONDS = 1_000_000
YEAR_2001 = 978_307_200 * MICROSECONDS  # anything later is a time, not a counter

MESSAGE_VALUE_TAG = b"\x3a"  # protobuf tag of Variant.message_value: field 7, length-delimited
VARINT = 0
FIXED64 = 1
LENGTH_DELIMITED = 2
FIXED32 = 5


def _read_varint(blob: bytes, position: int) -> tuple[int, int]:
    """Read a protobuf varint. Returns the value and the position after it."""
    value = shift = 0
    while True:
        byte = blob[position]
        value |= (byte & 0x7F) << shift
        position += 1
        if byte < 0x80:
            return value, position
        shift += 7


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field_key(group: int, field: int) -> bytes:
    """A presence field key {program "BN", group, field, index 0}, for a field not in the capture."""
    return (
        b"\x08"
        + _encode_varint(PRESENCE_PROGRAM)
        + b"\x10"
        + _encode_varint(group)
        + b"\x18"
        + _encode_varint(field)
        + b"\x20\x00"
    )


def _key_group_field(blob: bytes) -> tuple[int | None, int | None]:
    """The (group, field) of a presence field key, from its protobuf fields 2 and 3."""
    group = field = None
    position = 0
    try:
        while position < len(blob):
            tag, position = _read_varint(blob, position)
            number, wire_type = tag >> 3, tag & 7
            if wire_type == VARINT:
                value, position = _read_varint(blob, position)
                if number == 2:
                    group = value
                elif number == 3:
                    field = value
            elif wire_type == LENGTH_DELIMITED:
                length, position = _read_varint(blob, position)
                position += length
            elif wire_type == FIXED64:
                position += 8
            elif wire_type == FIXED32:
                position += 4
            else:
                break
    except IndexError:
        pass
    return group, field


def _swap_varint_fields(blob: bytes, position: int, swap: dict[int, int]) -> bytes | None:
    """Re-encode the protobuf fields from a position on, swapping ids held in varint fields.

    Returns None when no id was swapped or a field has a wire type this does not handle.
    """
    out = bytearray()
    changed = False
    while position < len(blob):
        field_start = position
        tag, position = _read_varint(blob, position)
        wire_type = tag & 7
        if wire_type == VARINT:
            value, position = _read_varint(blob, position)
            if value in swap:
                value = swap[value]
                changed = True
            out += _encode_varint(tag) + _encode_varint(value)
        elif wire_type == FIXED64:
            position += 8
            out += blob[field_start:position]
        elif wire_type == FIXED32:
            position += 4
            out += blob[field_start:position]
        elif wire_type == LENGTH_DELIMITED:
            length, position = _read_varint(blob, position)
            position += length
            out += blob[field_start:position]
        else:
            return None
    if not changed:
        return None
    return bytes(out)


def _rewrite_message_varints(blob: bytes, swap: dict[int, int]) -> bytes:
    """Swap ids in the varint fields of a blob that is a Variant message_value."""
    if not blob.startswith(MESSAGE_VALUE_TAG):
        return blob
    try:
        size, start = _read_varint(blob, 1)
        if start + size != len(blob):
            return blob
        fields = _swap_varint_fields(blob, start, swap)
    except IndexError:
        return blob
    if fields is None:
        return blob
    return MESSAGE_VALUE_TAG + _encode_varint(len(fields)) + fields


def _replace_pb_string(blob: bytes, old: bytes, new: bytes) -> bytes:
    """Replace a short protobuf string and its one-byte length. No match leaves the blob as it is."""
    start = blob.find(old)
    if start < 2 or blob[start - 1] != len(old):
        return blob
    return blob[: start - 1] + bytes([len(new)]) + new + blob[start + len(old) :]


def _with_time(value: bytes, microseconds: int) -> bytes:
    """A Variant int value that holds a time, set to another time. Any other value stays."""
    if not value.startswith(INT_VALUE_TAG):
        return value
    try:
        number, end = _read_varint(value, 1)
    except IndexError:
        return value
    if end != len(value) or number < YEAR_2001:
        return value
    return INT_VALUE_TAG + _encode_varint(microseconds)


def rewrite_ids(blob: bytes, swap: dict[int, int]) -> bytes:
    """Replace account ids inside a presence protobuf value.

    Covers fixed64 entity ids, the "<id>#1" game account name, and the varints of the Overwatch game
    account's player id {1: 1, 2: high, 3: low}, which the client uses for invites and whispers.
    """
    for old, new in swap.items():
        blob = blob.replace(struct.pack("<Q", old), struct.pack("<Q", new))
        blob = _replace_pb_string(blob, f"{old}#1".encode(), f"{new}#1".encode())
    return _rewrite_message_varints(blob, swap)


class Presence:
    def __init__(self, templates: RetailTemplates) -> None:
        self._templates = templates

    def records(
        self, profile: Profile, account_lo: int, status: int = STATUS_ONLINE, created: int = 0
    ) -> list[dict]:
        """Presence of one account: the account record and its App and Overwatch game accounts.

        `status` is what the player picked in the status dropdown (STATUS_ONLINE/AWAY/BUSY/OFFLINE);
        appear-offline and a truly offline friend both use STATUS_OFFLINE. Their times (last online,
        session start) are the account's last login or logout, so an offline friend shows as
        "offline (2 h)" instead of the capture's date. An account that never logged in counts from
        when its profile was made.
        """
        seen = (profile.last_online or created or int(time.time())) * MICROSECONDS
        swap = {
            RETAIL_ACCOUNT: account_lo,
            RETAIL_APP_ACCOUNT: account_lo ^ APP_ACCOUNT_BITS,
            RETAIL_PRO_ACCOUNT: account_lo ^ PRO_ACCOUNT_BITS,
        }
        online = status != STATUS_OFFLINE
        seen_ids: set[int] = set()
        records = []
        for _, value in self._templates.all(FRIENDS, 27113):
            record = value["+0x78"][0]
            recorded_id = record["+0x0"]["+0x0"]
            # Keep the first full record of each account and skip the short updates.
            if len(record["+0x20"]) <= 3 or recorded_id in seen_ids:
                continue
            seen_ids.add(recorded_id)
            record = self._personalized(record, profile, account_lo, swap)
            is_account_record = False
            for field in record["+0x20"]:
                field["+0x30"] = _with_time(field["+0x30"], seen)
                if not online and field["+0x8"] == GAME_ACCOUNT_ONLINE:
                    field["+0x30"] = OFFLINE
                group, number = _key_group_field(field["+0x8"])
                if (group, number) == AWAY_FIELD:
                    field["+0x30"] = ONLINE_BOOL if status == STATUS_AWAY else OFFLINE
                if group == BUSY_FIELD[0]:
                    is_account_record = True
            # Busy is an account-wide bool the capture never carries, so append it to the account
            # record. Always send it (false when not busy) so going back to online clears a busy flag
            # a friend's client cached, instead of leaving it stuck on.
            if is_account_record:
                busy = ONLINE_BOOL if status == STATUS_BUSY else OFFLINE
                record["+0x20"].append({"+0x8": _field_key(*BUSY_FIELD), "+0x30": busy})
            records.append(record)
        return records

    def own(self, profile: Profile, identity: Identity) -> list[tuple]:
        """Our own presence as a 27113 message."""
        return [(FRIENDS, 27113, {"+0x78": self.records(profile, identity.account_lo)})]

    @staticmethod
    def _personalized(record: dict, profile: Profile, account_lo: int, swap: dict[int, int]) -> dict:
        # The recorded BattleTag becomes ours; the full-name field (below) takes the nickname.
        record = personalize(record, battle_tag(profile.player_name, account_lo), account_lo)
        record_id = record["+0x0"]["+0x0"]
        record["+0x0"]["+0x0"] = swap.get(record_id, record_id)
        player_name = profile.player_name.encode("utf-8")
        for field in record["+0x20"]:
            field["+0x8"] = rewrite_ids(field["+0x8"], swap)
            field_value = rewrite_ids(field["+0x30"], swap)
            field["+0x30"] = _replace_pb_string(field_value, RETAIL_FULL_NAME, player_name)
        return record
