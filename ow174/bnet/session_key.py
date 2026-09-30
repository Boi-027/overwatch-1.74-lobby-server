"""The Battle.net session key carries the account name from our Battle.net emulator to the lobby.

The retail client sends the logon result's session key on to the lobby in its login (21800
+0xB8 +0x20), next to the game account id, and uses it for nothing else we serve: 64 bytes have to be
there. Our emulator writes the account name into those bytes, so the lobby knows who logged in, also
when the game runs on another PC. The lobby handshake does not use this key (it uses zeros).
"""

KEY_SIZE = 64
MARK = b"ow174:"
MAX_NAME_BYTES = KEY_SIZE - len(MARK)


def session_key(name: str) -> bytes:
    """The session key for an account name (at most MAX_NAME_BYTES of UTF-8)."""
    data = MARK + name.encode("utf-8")
    if len(data) > KEY_SIZE:
        raise ValueError(f"The name {name!r} is too long for the session key")
    return data.ljust(KEY_SIZE, b"\x00")


def name_in_key(key: bytes) -> str | None:
    """The account name in a session key, or None for a key without one."""
    if not key.startswith(MARK):
        return None
    return key[len(MARK) :].rstrip(b"\x00").decode("utf-8", errors="replace").strip() or None
