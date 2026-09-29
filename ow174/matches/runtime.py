"""Starts and stops one local game-server process (instance.py) per requested session."""

import json
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ow174.paths import ROOT


@dataclass
class MatchInstance:
    key: str
    player: str
    mode: int
    directory: Path
    process: subprocess.Popen
    port: int


def _stop_process(process: subprocess.Popen) -> None:
    """Close the child's stdin, which asks it to exit, then terminate or kill it if it does not."""
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _instance_command(directory: Path, port: int, player: str, mode: int, activity: str) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-u",
        "-m",
        "ow174.matches.instance",
        "--directory",
        str(directory.resolve()),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--player",
        player,
        "--mode",
        hex(mode),
        "--activity",
        activity,
        "--control-stdin",
    ]


class MatchManager:
    def __init__(self, directory, base_port=3730, startup_timeout=5):
        if not 0 <= base_port <= 65535 or not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise ValueError("Invalid game-server port or startup timeout")
        self.directory = Path(directory)
        self.base_port = base_port
        self.startup_timeout = startup_timeout
        self.instances = {}
        self.lock = threading.RLock()

    def request(self, key, player, mode, activity="queue"):
        """Return the running instance for this key and mode, or start a new one."""
        if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode < 1 << 64:
            raise ValueError("Invalid game mode")
        key = str(key)
        with self.lock:
            current = self.instances.get(key)
            if current is not None and current.mode == mode and current.process.poll() is None:
                return current
            self.cancel(key)
            port = self._free_port()
            directory = self.directory / uuid.uuid4().hex
            directory.mkdir(parents=True)
            with (directory / "process.log").open("wb") as log:
                process = subprocess.Popen(
                    _instance_command(directory, port, player, mode, activity),
                    cwd=ROOT,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            instance = MatchInstance(key, player, mode, directory, process, port)
            try:
                self._wait_until_listening(instance)
            except BaseException:
                _stop_process(process)
                raise
            self.instances[key] = instance
            return instance

    def _free_port(self) -> int:
        """The first port from base_port that no running instance uses. Port 0 lets the OS choose."""
        used = set()
        for instance in self.instances.values():
            if instance.process.poll() is None:
                used.add(instance.port)
        port = self.base_port
        while port and port in used:
            port += 1
        if port > 65535:
            raise RuntimeError("No game-server ports available")
        return port

    def _wait_until_listening(self, instance: MatchInstance) -> None:
        """Wait for the instance to report "listening" in its state.json, and take its real port."""
        process = instance.process
        state_path = instance.directory / "state.json"
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if state_path.is_file():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state["state"] == "listening" and process.poll() is None:
                    instance.port = state["port"]
                    return
                if state["state"] == "failed":
                    raise RuntimeError("Game-server startup failed: " + state.get("error", "unknown error"))
            if process.poll() is not None:
                raise RuntimeError(
                    f"Game server exited during startup: {process.returncode}; {instance.directory}"
                )
            time.sleep(0.02)
        raise RuntimeError(f"Game-server startup timed out; {instance.directory}")

    def cancel(self, key, mode=None):
        """Stop the instance of this key. With a mode, only when the instance runs that mode."""
        with self.lock:
            instance = self.instances.get(str(key))
            if instance is None or (mode is not None and instance.mode != mode):
                return
            self.instances.pop(str(key))
            _stop_process(instance.process)

    def snapshot(self):
        """The state of every instance, for the dashboard."""
        with self.lock:
            result = []
            for instance in self.instances.values():
                state = self._read_state(instance)
                if instance.process.poll() is not None:
                    state["state"] = "stopped" if instance.process.returncode == 0 else "failed"
                result.append(state)
            return result

    @staticmethod
    def _read_state(instance: MatchInstance) -> dict:
        try:
            return json.loads((instance.directory / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {
                "pid": instance.process.pid,
                "player": instance.player,
                "port": instance.port,
                "mode": hex(instance.mode),
                "state": "unknown",
                "protocol_ready": False,
            }

    def close(self):
        with self.lock:
            for key in list(self.instances):
                self.cancel(key)
