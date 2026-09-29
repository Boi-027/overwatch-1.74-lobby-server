"""The real lobby server against the smoke client: handshake, login, box, equip, purchase, profile."""

import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = [sys.executable, "-B", "-u", "-m", "ow174", "--mode", "server"]
CLIENT = [sys.executable, "-B", str(ROOT / "tools" / "fake_client.py")]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port: int, process: subprocess.Popen, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"lobby exited early with code {process.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("lobby did not start listening")


class LobbySmokeTest(unittest.TestCase):
    def test_client_session_decodes_every_reply(self):
        name = "Smoke" + uuid.uuid4().hex[:8]
        profile = ROOT / "profiles" / f"{name}.json"
        with tempfile.TemporaryDirectory() as tmp:
            port = free_port()
            server = subprocess.Popen(
                [
                    *SERVER,
                    "--port",
                    str(port),
                    "--dashboard-port",
                    "0",
                    "--game-port",
                    "0",
                    "--save",
                    str(Path(tmp) / "template.json"),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                wait_for_port(port, server)
                run = subprocess.run(
                    [*CLIENT, "--port", str(port), "--name", name],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
            finally:
                server.terminate()
                try:
                    server_log = server.communicate(timeout=10)[0]
                except subprocess.TimeoutExpired:
                    server.kill()
                    server_log = server.communicate()[0]
                profile.unlink(missing_ok=True)
        self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-500:])
        login = re.search(r"login frames: (\d+) ok, (\d+) bad", run.stdout)
        replies = re.search(r"reply frames: (\d+) ok, (\d+) bad", run.stdout)
        self.assertGreaterEqual(int(login.group(1)), 50)
        self.assertEqual(login.group(2), "0")
        self.assertGreaterEqual(int(replies.group(1)), 10)
        self.assertEqual(replies.group(2), "0")
        self.assertNotIn("Traceback", server_log)


if __name__ == "__main__":
    unittest.main()
