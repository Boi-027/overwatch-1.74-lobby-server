from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import lobbyserv
from jam_codec import Schemas


class ResearchInjectionTests(unittest.TestCase):
    def test_expired_experiment_is_not_sent_to_a_reused_connection(self):
        sent = []
        session = SimpleNamespace(cid=2, account=SimpleNamespace(name='Player2'),
                wire_of={0x888716D3: 7}, send_raw=sent.append, log=lambda text: None)
        server = SimpleNamespace(schemas=Schemas(), social=SimpleNamespace(sessions={2: session}))
        count = lobbyserv.send_research_message(server, {'crc': '888716D3', 'msg': 52903,
                    'value': {'+0x78': True}, 'conn': 2, 'player': 'Player2', 'expires_at': 1})
        self.assertEqual(count, 0)
        self.assertEqual(sent, [])

    def test_targeted_message_does_not_reach_other_connections(self):
        deliver = getattr(lobbyserv, 'send_research_message', None)
        self.assertTrue(callable(deliver), 'Research packets must target the requested connection')
        sent = {1: [], 2: []}
        sessions = {}
        for cid in (1, 2):
            sessions[cid] = SimpleNamespace(cid=cid, account=SimpleNamespace(name=f'Player{cid}'),
                wire_of={0x888716D3: 7}, send_raw=sent[cid].append, log=lambda text: None)
        server = SimpleNamespace(schemas=Schemas(), social=SimpleNamespace(sessions=sessions))
        result = deliver(server, {'crc': '888716D3', 'msg': 52903,
                                  'value': {'+0x78': True}, 'conn': 2, 'player': 'Player2'})
        self.assertEqual(result, 1)
        self.assertEqual(sent[1], [])
        self.assertEqual(sent[2], [bytes.fromhex('070301')])


if __name__ == '__main__':
    unittest.main()
