"""The relay DLL that strips TLS from the game's Battle.net connection (source: relay/owwfd_relay.cpp).

The official build is downloaded on the first start. A DLL built from source is used as it is.
"""

import hashlib
import logging
import urllib.request
from pathlib import Path

from ow174.launcher import LaunchError
from ow174.paths import RELAY_DLL

log = logging.getLogger("ow174.launcher")

RELEASE_URL = (
    "https://github.com/squeeeezy/overwatch-1.74-lobby-server/releases/download/"
    "relay-1.74.0.0.104319/owwfd_relay.dll"
)
RELEASE_SHA256 = "60f62b9928e7fe15d2b49cc9557029d44b25c3f00c01633cf0c4c32452f6592f"


def ensure_relay_dll(path: Path = RELAY_DLL) -> Path:
    if path.is_file():
        if hashlib.sha256(path.read_bytes()).hexdigest() != RELEASE_SHA256:
            log.info("Using a self-built relay DLL: %s", path)
        return path
    log.info("Downloading the relay DLL...")
    try:
        with urllib.request.urlopen(RELEASE_URL, timeout=60) as response:
            data = response.read()
    except OSError as error:
        raise LaunchError(
            f"Could not download the relay DLL ({error}). Download it from the project's Releases page "
            f"into {path.parent}, or build it with relay\\build.bat."
        ) from error
    if hashlib.sha256(data).hexdigest() != RELEASE_SHA256:
        raise LaunchError("The downloaded relay DLL is not the official build. Start again later.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
