"""
Overwatch 1.74 lobby framing helpers.
Message layouts live in the client's schemas (see jam_codec.py and content.py).
"""

import socket
try:
    from .crypto import Jam
except (ImportError, ValueError):
    from crypto import Jam

# OW1 Portrait Border Base GUID: 300 sequential borders
BORDER_BASE_GUID = 0x0250000000000918

def get_portrait_frame_guid(level: int) -> int:
    """
    Calculates the exact Overwatch 1 portrait border GUID from level.
    Tiers (every 600 lvls): 0: Bronze, 1: Silver, 2: Gold, 3: Platinum, 4: Diamond.
    Stars (every 100 lvls): 0..5 stars.
    Wings (every 10 lvls): 0..9 wing decorations.
    """
    lvl = max(1, level)
    tier = min(4, (lvl - 1) // 600)
    stars = min(5, ((lvl - 1) % 600) // 100)
    wings = min(9, ((lvl - 1) % 100) // 10)
    border_idx = tier * 60 + stars * 10 + wings
    return BORDER_BASE_GUID + border_idx

def send_frame(c: socket.socket, txjam: Jam, payload: bytes):
    """Frames a payload with a 3-byte big-endian length, encrypts it, and sends it."""
    frame = len(payload).to_bytes(3, "big") + payload
    c.sendall(txjam.crypt(frame))
