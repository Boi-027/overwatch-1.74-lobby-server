"""Event-start packets follow the captured native 38901 path."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.catalog.events import CELEBRATION_BASE, challenge_event, load_resource_keys  # noqa: E402
from ow174.catalog.items import ItemDB  # noqa: E402
from ow174.catalog.templates import RetailTemplates  # noqa: E402
from ow174.content.celebrations import Celebrations  # noqa: E402
from ow174.content.clock import server_time, stu_datetime  # noqa: E402
from ow174.jam.codec import Schemas  # noqa: E402
from ow174.jam.groups import EVENTS  # noqa: E402


class EventAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.schemas = Schemas()
        self.celebrations = Celebrations.__new__(Celebrations)
        self.celebrations.resource_keys = {}
        self.celebrations._map_swaps = {}
        self.celebrations._items = SimpleNamespace(challenges=lambda: {})
        self.profile = SimpleNamespace(server_date="2022-08-10", events=["anniversary"], challenge="")

    def test_notification_wraps_same_active_record_with_native_38901_schema(self):
        active = self.celebrations.records(self.profile)["+0x78"]
        messages = self.celebrations.notifications(self.profile)
        self.assertEqual(messages, [(EVENTS, 38901, {"+0x78": active[0]})])
        crc, mid, value = messages[0]
        self.assertEqual(self.schemas.decode(crc, mid, self.schemas.encode(crc, mid, value)), value)
        self.assertEqual(value["+0x78"]["+0x40"], CELEBRATION_BASE | 0x118)
        self.assertEqual(value["+0x78"]["+0x48"]["+0x0"], stu_datetime(server_time(self.profile) - 2 * 86400))

    def test_inactive_events_do_not_generate_notifications(self):
        self.profile.events = []
        self.assertEqual(self.celebrations.notifications(self.profile), [])

    def test_duplicate_presets_emit_one_notification(self):
        self.profile.events = ["anniversary", "anniversary"]
        self.assertEqual(len(self.celebrations.notifications(self.profile)), 1)

    def test_retail_notifications_are_exact_wrappers_of_initial_active_records(self):
        retail = RetailTemplates()
        initial = {r["+0x40"]: r for r in retail.first(EVENTS, 38900)["+0x78"]}
        later = retail.all(EVENTS, 38901)
        self.assertEqual(len(later), 2)
        for _, packet in later:
            self.assertEqual(packet["+0x78"], initial[packet["+0x78"]["+0x40"]])

    def test_a_blank_challenge_stays_off(self):
        self.celebrations._items = ItemDB()
        self.assertEqual(self.celebrations.challenge_rewards(self.profile), [])
        self.assertEqual(len(self.celebrations.records(self.profile)["+0x78"]), 1)

    def test_verified_weekly_ids_rewards_keys_and_progress_agree(self):
        self.celebrations._items = ItemDB()
        self.celebrations.resource_keys = load_resource_keys()
        self.profile.challenge_wins = 12
        for title, celebration, reward_indexes in (
            ("Tracer's Comic Challenge", 0x119, [0x4AEA, 0x4AEB, 0x4AEC]),
            ("Symmetra's Restoration Challenge", 0x11A, [0x4B10, 0x4B11, 0x4B08]),
        ):
            with self.subTest(title=title):
                self.profile.challenge = title
                definition = challenge_event(self.profile.challenge)
                self.assertEqual((definition.celebration, definition.key), (celebration, 0x198))
                weekly = self.celebrations.records(self.profile)["+0x78"][-1]
                self.assertEqual(weekly["+0x40"], CELEBRATION_BASE | celebration)
                self.assertEqual([r["+0x0"][0] & 0xFFFFFFFF for r in weekly["+0x18"]], reward_indexes)
                self.assertEqual([r["+0x18"] for r in weekly["+0x18"]], [9, 18, 27])
                self.assertEqual(
                    self.celebrations.progress(self.profile)["+0x78"],
                    [{"+0x0": CELEBRATION_BASE | celebration, "+0x8": 12.0}],
                )
                self.assertIn(0x198, self.celebrations.keys(self.profile))
                self.assertNotIn(0x188, self.celebrations.keys(self.profile))

    def test_other_manual_challenges_keep_legacy_definition(self):
        self.profile.challenge = "Legacy custom title"
        definition = challenge_event(self.profile.challenge)
        self.assertEqual((definition.celebration, definition.key), (0x106, 0x188))


if __name__ == "__main__":
    unittest.main()
