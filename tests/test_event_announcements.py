"""Event-start greetings follow the captured native 38901 path."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.accounts.profile import Profile  # noqa: E402
from ow174.catalog.events import CELEBRATION_BASE, challenge_event, load_resource_keys  # noqa: E402
from ow174.catalog.items import UNLOCK_BASE, ItemDB  # noqa: E402
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
        self.celebrations._items = SimpleNamespace(get=lambda guid: None)
        self.profile = SimpleNamespace(
            server_date="2022-08-10", events=["anniversary"], challenge="", greeted_events=[]
        )

    def test_greeting_wraps_same_active_record_with_native_38901_schema(self):
        active = self.celebrations.records(self.profile)["+0x78"]
        messages = self.celebrations.greetings(self.profile)
        self.assertEqual(messages, [(EVENTS, 38901, {"+0x78": active[0]})])
        crc, mid, value = messages[0]
        self.assertEqual(self.schemas.decode(crc, mid, self.schemas.encode(crc, mid, value)), value)
        self.assertEqual(value["+0x78"]["+0x40"], CELEBRATION_BASE | 0x118)
        self.assertEqual(value["+0x78"]["+0x48"]["+0x0"], stu_datetime(server_time(self.profile) - 2 * 86400))

    def test_inactive_events_do_not_generate_greetings(self):
        self.profile.events = []
        self.assertEqual(self.celebrations.greetings(self.profile), [])

    def test_duplicate_presets_emit_one_greeting(self):
        self.profile.events = ["anniversary", "anniversary"]
        self.assertEqual(len(self.celebrations.greetings(self.profile)), 1)

    def test_an_event_greets_once_with_its_login_rewards(self):
        # Lunar New Year 2022 as retail gave it: its box and the Year of the Tiger icon.
        self.celebrations._items = ItemDB()
        self.celebrations._owns = lambda profile, guid: False
        profile = Profile(server_date="2022-02-01", events=["lunar"])
        boxes = len(profile.loot_boxes)
        messages, given, gift_boxes = self.celebrations.greet(profile)
        self.assertEqual([msg_id for _, msg_id, _ in messages], [38901])
        self.assertEqual(given, [UNLOCK_BASE | 0x4F50])
        self.assertEqual(profile.loot_boxes[boxes:], gift_boxes)
        self.assertEqual([box["type"] for box in gift_boxes], [4])
        self.assertIn(f"0x{UNLOCK_BASE | 0x4F50:016X}", profile.unlocked_items)
        self.assertEqual(self.celebrations.greet(profile), ([], [], []))

    def test_the_overwatch_2_notice_greets_with_its_splash_and_no_box(self):
        # 01B/0C74 shows the splash 05A/07D7 when celebration 0x123 is greeted.
        self.celebrations._items = ItemDB()
        self.celebrations._owns = lambda profile, guid: False
        profile = Profile(server_date="2022-09-20", events=["ow2_credits"])
        boxes = len(profile.loot_boxes)
        messages, given, gift_boxes = self.celebrations.greet(profile)
        self.assertEqual([value["+0x78"]["+0x40"] for _, _, value in messages], [CELEBRATION_BASE | 0x123])
        self.assertEqual((given, gift_boxes, len(profile.loot_boxes)), ([], [], boxes))

    def test_an_event_without_a_box_has_no_greeting(self):
        # Reaper's +0x58 lists the challenge's rewards for "What's new"; they are not a gift.
        self.profile.events = ["reaper", "goodbye"]
        self.assertEqual(self.celebrations.greetings(self.profile), [])

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
            ("Kanezaka Challenge", 0x11B, [0x4BB2, 0x4BB1, 0x4AA4]),
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

    def test_a_challenge_without_a_play_menu_banner_stays_off(self):
        self.celebrations._items = ItemDB()
        self.profile.challenge = "Reaper's Code of Violence Challenge"
        self.assertEqual(self.celebrations.challenge_rewards(self.profile), [])
        self.assertEqual(len(self.celebrations.records(self.profile)["+0x78"]), 1)
        self.assertEqual(self.celebrations.progress(self.profile), {"+0x78": []})


if __name__ == "__main__":
    unittest.main()
