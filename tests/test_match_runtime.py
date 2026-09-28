import importlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))


class MatchRuntimeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec('match_runtime')
        self.assertIsNotNone(spec, 'A queue must allocate a real managed game-server process')
        cls = importlib.import_module('match_runtime').MatchManager
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = cls(Path(self.tmp.name), base_port=0, startup_timeout=3)
        self.addCleanup(self.manager.close)

    def test_request_starts_real_udp_process_and_repeat_reuses_it(self):
        instance = self.manager.request('connection-1', 'Alpha', 0x0630000000000002)
        self.assertIsNone(instance.process.poll())
        self.assertGreater(instance.port, 0)
        again = self.manager.request('connection-1', 'Alpha', 0x0630000000000002)
        self.assertEqual(instance.process.pid, again.process.pid)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b'research-probe', ('127.0.0.1', instance.port))
        deadline = time.monotonic() + 3
        packets = instance.directory / 'packets.jsonl'
        while time.monotonic() < deadline and (not packets.exists() or not packets.stat().st_size):
            time.sleep(0.02)
        data = json.loads(packets.read_text(encoding='utf-8').splitlines()[0])
        self.assertEqual(data['hex'], '72657365617263682d70726f6265')
        self.assertFalse(self.manager.snapshot()[0]['protocol_ready'])

    def test_separate_sessions_get_separate_processes_and_cancel_is_scoped(self):
        a = self.manager.request('a', 'Alpha', 1)
        b = self.manager.request('b', 'Beta', 1)
        self.assertNotEqual(a.port, b.port)
        self.assertNotEqual(a.process.pid, b.process.pid)
        self.manager.cancel('a')
        self.assertIsNotNone(a.process.poll())
        self.assertIsNone(b.process.poll())
        self.assertEqual([s['player'] for s in self.manager.snapshot()], ['Beta'])

    def test_mode_change_replaces_only_that_players_instance(self):
        old = self.manager.request('a', 'Alpha', 1)
        new = self.manager.request('a', 'Alpha', 2)
        self.assertIsNotNone(old.process.poll())
        self.assertIsNone(new.process.poll())
        self.assertNotEqual(old.process.pid, new.process.pid)

    def test_port_conflict_is_reported_without_false_ready_instance(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', 0))
            self.manager.base_port = sock.getsockname()[1]
            with self.assertRaises(RuntimeError):
                self.manager.request('a', 'Alpha', 1)
        self.assertEqual(self.manager.snapshot(), [])


if __name__ == '__main__':
    unittest.main()
