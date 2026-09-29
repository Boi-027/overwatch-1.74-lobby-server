"""A game-server process that only records the UDP packets sent to it.

It does not speak the Overwatch game protocol, which is still unknown, and it is not a playable
world. Run as `py -m ow174.matches.instance`. The manager in runtime.py reads its state.json.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path


def write_state(path, value):
    """Write state.json through a temporary file, so a reader never sees half a file."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _stop_when_parent_closes_stdin(stop: threading.Event) -> None:
    """The manager holds our stdin open. When it closes it, or dies, we shut down."""

    def wait_for_eof():
        sys.stdin.buffer.read()
        stop.set()

    threading.Thread(target=wait_for_eof, daemon=True).start()


def _initial_state(directory, host, port, player, mode, activity) -> dict:
    return {
        "id": directory.name,
        "pid": os.getpid(),
        "player": player,
        "mode": f"0x{mode:016X}",
        "host": host,
        "port": port,
        "activity": activity,
        "state": "starting",
        "protocol_ready": False,
        "packets_received": 0,
        "bytes_received": 0,
        "started_at": time.time(),
        "last_packet_at": None,
    }


def _record_packets(sock, stop, state, state_path, log_path) -> None:
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        while not stop.is_set():
            try:
                data, peer = sock.recvfrom(65535)
            except TimeoutError:
                continue
            state["packets_received"] += 1
            state["bytes_received"] += len(data)
            state["last_packet_at"] = time.time()
            entry = {
                "time": state["last_packet_at"],
                "peer": list(peer),
                "bytes": len(data),
                "hex": data.hex(),
            }
            log.write(json.dumps(entry) + "\n")
            write_state(state_path, state)


def run(directory, host, port, player, mode, control_stdin=False, activity="queue"):
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    stop = threading.Event()
    if control_stdin:
        _stop_when_parent_closes_stdin(stop)
    state = _initial_state(directory, host, port, player, mode, activity)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, port))
        except OSError as error:
            state.update(state="failed", error=str(error))
            write_state(state_path, state)
            return 1
        state.update(port=sock.getsockname()[1], state="listening")
        write_state(state_path, state)
        sock.settimeout(0.2)  # wake up regularly to check the stop flag
        _record_packets(sock, stop, state, state_path, directory / "packets.jsonl")
    state.update(state="stopped", stopped_at=time.time())
    write_state(state_path, state)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--player", required=True)
    parser.add_argument("--mode", type=lambda s: int(s, 0), required=True)
    parser.add_argument("--control-stdin", action="store_true")
    parser.add_argument("--activity", choices=("queue", "practice"), default="queue")
    args = parser.parse_args()
    return run(
        args.directory, args.host, args.port, args.player, args.mode, args.control_stdin, args.activity
    )


if __name__ == "__main__":
    raise SystemExit(main())
