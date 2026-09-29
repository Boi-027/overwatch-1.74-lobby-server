import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.jam.codec import Schemas
from ow174.lobby import research


class ResearchInjectionTests(unittest.TestCase):
    def test_expired_experiment_is_not_sent_to_a_reused_connection(self):
        sent = []
        session = SimpleNamespace(
            conn_id=2,
            account=SimpleNamespace(name="Player2"),
            wire_of={0x888716D3: 7},
            send_raw=sent.append,
            log=lambda text: None,
        )
        server = SimpleNamespace(schemas=Schemas(), social=SimpleNamespace(sessions={2: session}))
        count = research.send_experiment(
            server,
            {
                "crc": "888716D3",
                "msg": 52903,
                "value": {"+0x78": True},
                "conn": 2,
                "player": "Player2",
                "expires_at": 1,
            },
        )
        self.assertEqual(count, 0)
        self.assertEqual(sent, [])

    def test_targeted_message_does_not_reach_other_connections(self):
        deliver = getattr(research, "send_experiment", None)
        self.assertTrue(callable(deliver), "Research packets must target the requested connection")
        sent = {1: [], 2: []}
        sessions = {}
        for conn_id in (1, 2):
            sessions[conn_id] = SimpleNamespace(
                conn_id=conn_id,
                account=SimpleNamespace(name=f"Player{conn_id}"),
                wire_of={0x888716D3: 7},
                send_raw=sent[conn_id].append,
                log=lambda text: None,
            )
        server = SimpleNamespace(schemas=Schemas(), social=SimpleNamespace(sessions=sessions))
        result = deliver(
            server,
            {"crc": "888716D3", "msg": 52903, "value": {"+0x78": True}, "conn": 2, "player": "Player2"},
        )
        self.assertEqual(result, 1)
        self.assertEqual(sent[1], [])
        self.assertEqual(sent[2], [bytes.fromhex("070301")])


if __name__ == "__main__":
    unittest.main()
