"""Clock and asset-availability regressions for celebration packets."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

from content import (
    LobbyContent, Identity, CONFIG, EVENTS, IN_CONNECT, SKIN_THEME_BASE,
    EVENT_PRESETS, server_time, stu_datetime,
)
from jam_codec import Schemas
from retail import RetailCapture
from events import EVENT_CATALOG


class EventTests(unittest.TestCase):
    def setUp(self):
        self.content = LobbyContent.__new__(LobbyContent)
        self.content.resource_keys = {}
        self.content.map_swaps = {}
        self.content.items = SimpleNamespace(challenges=lambda: {})
        self.profile = SimpleNamespace(server_date='2022-04-10', events=['anniversary'], challenge='', challenge_wins=0)

    def test_custom_date_inside_event_and_challenge_windows(self):
        self.content.items = SimpleNamespace(challenges=lambda: {'test': [SimpleNamespace(guid=123)]})
        self.profile.challenge = 'test'
        with patch('content.time.time', return_value=1900000000):
            records = self.content.celebrations(self.profile)['+0x78']
        self.assertEqual(len(records), 2)
        expected = server_time(self.profile)
        for record in records:
            self.assertEqual(record['+0x48']['+0x0'], stu_datetime(expected - 2 * 86400))
            self.assertEqual(record['+0x50']['+0x0'], stu_datetime(expected + 365 * 86400))

    def test_live_clock_precedes_events(self):
        for method in ('lobby_hero', 'player_record', 'content_keys', 'challenge_progress',
                       'party_state', 'account_features', 'progression', 'hero_catalog'):
            setattr(self.content, method, Mock(return_value={}))
        self.content.endorsements = Mock(return_value=[])
        messages = self.content.live_messages(self.profile, Identity.create(1, 1))
        clock_index = next(i for i, (crc, mid, _) in enumerate(messages) if (crc, mid) == (CONFIG, 36602))
        event_index = next(i for i, (crc, mid, _) in enumerate(messages) if (crc, mid) == (EVENTS, 38900))
        self.assertLess(clock_index, event_index)
        self.assertEqual(messages[clock_index][2]['+0x78'], int(server_time(self.profile)))

    def test_anniversary_all_six_scene_skin_themes_preloaded(self):
        self.content.retail = RetailCapture(Schemas())
        skins = self.content.preload()['+0x90']
        # Extracted from E83 map entity1629 instance overrides, not unlock IDs.
        expected = {SKIN_THEME_BASE | x for x in (0x49CF, 0x49E4, 0x49D8, 0x49D9, 0x49D1, 0x49A8)}
        self.assertTrue(expected.issubset(skins), f'Missing scene themes: {expected.difference(skins)}')
        self.assertEqual(skins, self.content.preload()['+0x90'])

    def test_remix_selectors_match_extracted_client_catalog(self):
        extracted = json.loads((ROOT / 'data/extracted_events_174.json').read_text(encoding='utf-8'))
        rows = {r['celebration']: r for r in extracted['lobby_mappings'] if r['region'] == 'default'}
        for name in ('anniversary', 'anniversary_remix_1', 'anniversary_remix_2'):
            row = rows[f'{EVENT_PRESETS[name].celebration:012X}.0C3']
            self.assertEqual(row['catalog'], '00000000034A.039')
            self.assertEqual(row['maps'][0]['guid'], '000000000E83.09F')
        for name in ('summer', 'contenders'):
            self.assertNotIn(f'{EVENT_PRESETS[name].celebration:012X}.0C3', rows)

    def test_unavailable_tracer_does_not_invent_a_celebration(self):
        metadata = {e['id']: e for e in EVENT_CATALOG}
        self.assertEqual(metadata['tracer_comic']['scene_status'], 'unavailable')
        self.assertNotIn('tracer_comic', EVENT_PRESETS)
        self.assertTrue(set(EVENT_PRESETS).issubset(metadata))


if __name__ == '__main__':
    unittest.main()
