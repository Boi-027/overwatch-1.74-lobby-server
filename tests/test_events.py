"""Clock and asset-availability regressions for celebration packets."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.catalog.events import (  # noqa: E402
    EVENT_INFO,
    EVENT_PRESETS,
    PRELOAD_EXTRA_SKINS,
    SKIN_THEME_BASE,
)
from ow174.catalog.templates import RetailTemplates  # noqa: E402
from ow174.content import Content  # noqa: E402
from ow174.content.celebrations import Celebrations  # noqa: E402
from ow174.content.clock import server_time, stu_datetime  # noqa: E402
from ow174.content.identity import Identity  # noqa: E402
from ow174.content.retail import RetailReplay  # noqa: E402
from ow174.jam.groups import CONFIG, EVENTS  # noqa: E402


class EventTests(unittest.TestCase):
    def setUp(self):
        self.celebrations = Celebrations.__new__(Celebrations)
        self.celebrations.resource_keys = {}
        self.celebrations._map_swaps = {}
        self.celebrations._items = SimpleNamespace(challenges=lambda: {})
        self.profile = SimpleNamespace(
            server_date="2022-04-10", events=["anniversary"], challenge="", challenge_wins=0
        )

    def test_custom_date_inside_event_and_challenge_windows(self):
        self.celebrations._items = SimpleNamespace(challenges=lambda: {"test": [SimpleNamespace(guid=123)]})
        self.profile.challenge = "test"
        with patch("ow174.content.clock.time.time", return_value=1900000000):
            records = self.celebrations.records(self.profile)["+0x78"]
        self.assertEqual(len(records), 2)
        expected = server_time(self.profile)
        for record in records:
            self.assertEqual(record["+0x48"]["+0x0"], stu_datetime(expected - 2 * 86400))
            self.assertEqual(record["+0x50"]["+0x0"], stu_datetime(expected + 365 * 86400))

    def test_live_clock_precedes_events(self):
        content = Content.__new__(Content)
        content.celebrations = self.celebrations
        self.celebrations.content_keys = Mock(return_value={})
        self.celebrations.progress = Mock(return_value={})
        content.menu_hero = Mock(choose=Mock(return_value={}))
        content.retail = Mock(menu_config=Mock(return_value={}))
        content.player = Mock(
            record=Mock(return_value={}),
            party_state=Mock(return_value={}),
            features=Mock(return_value={}),
            endorsements=Mock(return_value=[]),
        )
        content.collection = Mock(progression=Mock(return_value={}), hero_catalog=Mock(return_value={}))
        messages = content.live_messages(self.profile, Identity.create(1, 1))
        clock_index = next(i for i, (crc, mid, _) in enumerate(messages) if (crc, mid) == (CONFIG, 36602))
        event_index = next(i for i, (crc, mid, _) in enumerate(messages) if (crc, mid) == (EVENTS, 38900))
        self.assertLess(clock_index, event_index)
        self.assertEqual(messages[clock_index][2]["+0x78"], int(server_time(self.profile)))

    def test_anniversary_all_six_scene_skin_themes_preloaded(self):
        retail = RetailReplay(RetailTemplates())
        skins = retail.preload(PRELOAD_EXTRA_SKINS)["+0x90"]
        # Extracted from E83 map entity1629 instance overrides, not unlock IDs.
        expected = {SKIN_THEME_BASE | x for x in (0x49CF, 0x49E4, 0x49D8, 0x49D9, 0x49D1, 0x49A8)}
        self.assertTrue(expected.issubset(skins), f"Missing scene themes: {expected.difference(skins)}")
        self.assertEqual(skins, retail.preload(PRELOAD_EXTRA_SKINS)["+0x90"])

    def test_remix_selectors_match_extracted_client_catalog(self):
        extracted = json.loads((ROOT / "data/extracted_events_174.json").read_text(encoding="utf-8"))
        rows = {r["celebration"]: r for r in extracted["lobby_mappings"] if r["region"] == "default"}
        for name in ("anniversary", "anniversary_remix_1", "anniversary_remix_2"):
            row = rows[f"{EVENT_PRESETS[name].celebration:012X}.0C3"]
            self.assertEqual(row["catalog"], "00000000034A.039")
            self.assertEqual(row["maps"][0]["guid"], "000000000E83.09F")
        for name in ("summer", "contenders"):
            self.assertNotIn(f"{EVENT_PRESETS[name].celebration:012X}.0C3", rows)

    def test_unavailable_tracer_does_not_invent_a_celebration(self):
        metadata = {e.id: e for e in EVENT_INFO}
        self.assertEqual(metadata["tracer_comic"].scene_status, "unavailable")
        self.assertNotIn("tracer_comic", EVENT_PRESETS)
        self.assertTrue(set(EVENT_PRESETS).issubset(metadata))


if __name__ == "__main__":
    unittest.main()
