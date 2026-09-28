"""Event-start packets follow the captured native 38901 path."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from content import LobbyContent, EVENTS, CELEBRATION_BASE, server_time, stu_datetime
import content
from items import ItemDB
from jam_codec import Schemas
from retail import RetailCapture


class EventAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.schemas = Schemas()
        self.content = LobbyContent.__new__(LobbyContent)
        self.content.resource_keys = {}
        self.content.map_swaps = {}
        self.content.items = SimpleNamespace(challenges=lambda: {})
        self.profile = SimpleNamespace(server_date='2022-08-10', events=['anniversary'], challenge='')

    def test_notification_wraps_same_active_record_with_native_38901_schema(self):
        active = self.content.celebrations(self.profile)['+0x78']
        messages = self.content.event_notifications(self.profile)
        self.assertEqual(messages, [(EVENTS, 38901, {'+0x78': active[0]})])
        crc, mid, value = messages[0]
        self.assertEqual(self.schemas.decode(crc, mid, self.schemas.encode(crc, mid, value)), value)
        self.assertEqual(value['+0x78']['+0x40'], CELEBRATION_BASE | 0x118)
        self.assertEqual(value['+0x78']['+0x48']['+0x0'], stu_datetime(server_time(self.profile) - 2 * 86400))

    def test_inactive_events_do_not_generate_notifications(self):
        self.profile.events = []
        self.assertEqual(self.content.event_notifications(self.profile), [])

    def test_duplicate_presets_emit_one_notification(self):
        self.profile.events = ['anniversary', 'anniversary']
        self.assertEqual(len(self.content.event_notifications(self.profile)), 1)

    def test_retail_notifications_are_exact_wrappers_of_initial_active_records(self):
        retail = RetailCapture(self.schemas)
        initial = {r['+0x40']: r for r in retail.first(EVENTS, 38900)['+0x78']}
        later = retail.all(EVENTS, 38901)
        self.assertEqual(len(later), 2)
        for _, packet in later:
            self.assertEqual(packet['+0x78'], initial[packet['+0x78']['+0x40']])

    def test_default_is_suggestion_and_blank_stays_off(self):
        self.assertEqual(content.default_challenge_for_events(['anniversary']), "Tracer's Comic Challenge")
        self.assertIsNone(content.default_challenge_for_events(['anniversary_remix_1']))
        self.content.items = ItemDB()
        self.assertEqual(self.content.challenge_rewards(self.profile), [])
        self.assertEqual(len(self.content.celebrations(self.profile)['+0x78']), 1)

    def test_verified_weekly_ids_rewards_keys_and_progress_agree(self):
        self.content.items = ItemDB()
        self.content.resource_keys = content.load_resource_keys()
        self.profile.challenge_wins = 12
        for title, celebration, reward_indexes in (
                ("Tracer's Comic Challenge", 0x119, [0x4AEA, 0x4AEB, 0x4AEC]),
                ("Symmetra's Restoration Challenge", 0x11A, [0x4B10, 0x4B11, 0x4B08])):
            with self.subTest(title=title):
                self.profile.challenge = title
                definition = content.effective_challenge_definition(self.profile)
                self.assertEqual((definition.celebration, definition.key), (celebration, 0x198))
                weekly = self.content.celebrations(self.profile)['+0x78'][-1]
                self.assertEqual(weekly['+0x40'], CELEBRATION_BASE | celebration)
                self.assertEqual([r['+0x0'][0] & 0xFFFFFFFF for r in weekly['+0x18']], reward_indexes)
                self.assertEqual([r['+0x18'] for r in weekly['+0x18']], [9, 18, 27])
                self.assertEqual(self.content.challenge_progress(self.profile)['+0x78'],
                                 [{'+0x0': CELEBRATION_BASE | celebration, '+0x8': 12.0}])
                self.assertIn(0x198, self.content.event_keys(self.profile))
                self.assertNotIn(0x188, self.content.event_keys(self.profile))

    def test_other_manual_challenges_keep_legacy_definition(self):
        self.profile.challenge = 'Legacy custom title'
        definition = content.effective_challenge_definition(self.profile)
        self.assertEqual((definition.celebration, definition.key), (0x106, 0x188))


if __name__ == '__main__':
    unittest.main()
