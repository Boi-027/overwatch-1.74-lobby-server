"""The messages the lobby builds must match the snapshot taken before the package was restructured.

The client closes the connection on a malformed or unexpected message, so a refactor must not change
a single byte. The snapshot stores a hash per message for a few fixed profiles.

Regenerate only after an intended change:  py -3 tests/test_content_golden.py --update
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "tests" / "golden" / "messages.json"

from ow174.accounts.profile import Profile  # noqa: E402
from ow174.catalog.items import ItemDB  # noqa: E402
from ow174.catalog.templates import RetailTemplates  # noqa: E402
from ow174.content import Content, Identity  # noqa: E402
from ow174.jam.codec import Schemas  # noqa: E402

NOW = 1_800_000_000.0
IDENT = Identity(account_lo=0x1C2A69FB, session=(7, 1), party_id=(11, 12), party_entity=(13, 14))
TARGET = {"+0x0": 0x1C2A69FB, "+0x8": 0x0100000000000000}

SCENARIOS = {
    "default": {"lobby_hero": "Genji"},
    "unlock_all_eu": {"lobby_hero": "Reaper", "unlock_all": True, "region": "EU", "level": 2500},
    "npc_gb": {"lobby_hero": "Talon Sniper", "region": "GB", "endorsement_level": 4},
    "events_challenge": {
        "lobby_hero": "Tracer",
        "events": ["anniversary"],
        "challenge": "Tracer's Comic Challenge",
        "challenge_wins": 20,
        "server_date": "2022-10-03",
    },
    "granted": {
        "lobby_hero": "none",
        "unlocked_items": ["0x0250000000001149", "0x0250000000000921"],
        "icon_guid": 0x0250000000001149,
        "loadouts": {"0x02E0000000000002": {"+0x38": "0x0250000000000921"}},
    },
}
# Parts of the snapshot that belonged to code removed as dead (nothing called it)
REMOVED_PARTS = {"name_reply"}


def canonical(value):
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    return value


def digest(value) -> str:
    text = json.dumps(canonical(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def summarize(messages):
    return [[f"{crc:08X}", msg_id, digest(value)] for crc, msg_id, value in messages]


def build_all(content: Content, profile: Profile) -> dict:
    c = content
    return {
        "login": summarize(c.login_messages(profile, IDENT)),
        "live": summarize(c.live_messages(profile, IDENT)),
        "menu_config": summarize(
            [(0, 0, c.retail.menu_config(profile, IDENT, c.menu_hero.menu_guid(profile)))]
        ),
        "career": summarize(c.career.profile(profile, IDENT, TARGET)),
        "career_other": summarize(c.career.profile(profile, IDENT, {"+0x0": 5, "+0x8": 1})),
        "notifications": summarize(c.celebrations.notifications(profile)),
        "party": summarize([(0, 0, c.player.party_state(profile, IDENT, c.menu_hero.choose(profile)))]),
        "settings": summarize([(0, 0, c.player.settings(profile))]),
        "summary": summarize([(0, 0, c.player.summary(profile, IDENT))]),
        "owned_account": [digest(c.collection.owned_account(profile))],
        "owned_heroes": [digest([c.collection.owned_for_hero(profile, h) for h in c.collection.heroes])],
    }


def generate() -> dict:
    content = Content(Schemas(), RetailTemplates(), ItemDB())
    result = {}
    with patch("time.time", return_value=NOW), patch("random.choice", lambda seq: seq[0]):
        for name, fields in SCENARIOS.items():
            result[name] = build_all(content, Profile(**fields))
    return result


class GoldenMessageTests(unittest.TestCase):
    def test_messages_are_unchanged(self):
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        actual = generate()
        self.assertEqual(sorted(actual), sorted(expected))
        for scenario, parts in expected.items():
            for part, digests in parts.items():
                if part in REMOVED_PARTS:
                    continue
                self.assertEqual(actual[scenario][part], digests, f"{scenario}/{part} changed")
            self.assertEqual(set(actual[scenario]), set(parts) - REMOVED_PARTS)


if __name__ == "__main__":
    if "--update" in sys.argv:
        GOLDEN.write_text(json.dumps(generate(), indent=1) + "\n", encoding="utf-8")
        print("written", GOLDEN)
    else:
        unittest.main()
