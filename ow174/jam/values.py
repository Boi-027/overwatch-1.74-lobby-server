"""Helpers for the plain dict/list values the codec produces."""

import copy


def id16(lo: int, hi: int = 0x0100000000000000) -> dict:
    """A 16-byte id field (such as an account id) as the codec stores it: low and high u64."""
    return {"+0x0": lo, "+0x8": hi}


def clone(value):
    return copy.deepcopy(value)


def to_jsonable(value):
    """A copy that json.dumps accepts. Large ints (GUIDs, ids) become hex strings for readable logs."""
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return {"blob": value.hex()}
    if isinstance(value, int) and not isinstance(value, bool) and value > 0xFFFFFF:
        return f"0x{value:016X}"
    return value
