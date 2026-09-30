"""Lobby events (celebrations) and hero challenges.

An event is a record in message 38900. The fields the client needs, as in the recorded Lunar New
Year 2022 record:

    +0x40        celebration id: picks the catalog of main menu maps
    +0x70        celebration type: picks the event map variants and the event loot box
    +0x30/+0x38  content key name and id; the key bytes themselves are sent in message 20504
    +0x0 map swaps, +0x58 login rewards, +0xB0 event loot box type

The "What's new" screen lists the contents of the event loot box in +0x58 as the event's "New
trophies". The boxes are the ones STULootBoxCelebrationOverride (054/0x18) gives each celebration type.

Content keys come from data/resource_keys_174.json.
"""

import json
from dataclasses import dataclass

from ow174.catalog.items import SKIN_THEME_BASE, UNLOCK_BASE
from ow174.paths import DATA_DIR

CELEBRATION_BASE = 0x0430000000000000
RESOURCE_KEY_BASE = 0x0F10000000000000
CELEBRATION_TYPE_BASE = 0x0D80000000000000
MAP_BASE = 0x0800000000000000


@dataclass(frozen=True)
class EventDef:
    celebration: int
    key: int = 0
    kind: int = 0
    box: int = -1
    rewards: tuple = ()
    splash: bool = False  # its start greeting shows a splash screen (content/celebrations.py)


EVENT_PRESETS = {
    "goodbye": EventDef(0x128, key=0x1A3),  # 1.74 farewell lobby
    "lunar": EventDef(0x105, key=0x188, kind=0x237C, box=4, rewards=(0x0D5A, 0x4F50)),  # 2022
    "halloween": EventDef(0xFB, key=0x181, kind=0x212E, box=2, rewards=(0x0C23,)),  # Halloween Terror 2021
    "winter": EventDef(0xFF, key=0x185, kind=0x2168, box=3, rewards=(0x0D0B,)),  # Winter Wonderland 2021
    "anniversary": EventDef(0x118, key=0x198, kind=0x2428, box=6, rewards=(0x0EC3,)),  # Remix vol 3
    # the same E83 catalog, earlier selectors
    "anniversary_remix_1": EventDef(0x10E, key=0x196, kind=0x2428, box=6, rewards=(0x0EC3,)),
    "anniversary_remix_2": EventDef(0x112, key=0x197, kind=0x2428, box=6, rewards=(0x0EC3,)),
    # Summer Games and Archives have no main menu scene in 1.74, but their type still picks the event
    # loot box.
    "summer": EventDef(0xEB, key=0x178, kind=0x212F, box=1, rewards=(0x0B2F,)),  # Summer Games 2021
    "archives": EventDef(0xA8, key=0x169, kind=0x2427, box=5, rewards=(0x0EC2,)),  # Archives 2021
    # Reaper's Code of Violence has no loot box. Its rewards (the Dusk skin, icon and spray) show the
    # "What's new" banner; the Reaper challenge lists them as trophies.
    "reaper": EventDef(0x104, key=0x189, rewards=(0x4DC7, 0x4EF3, 0x4FCF)),
    "cassidy": EventDef(0xF7, key=0x17F),  # Cassidy's New Blood
    "malevento": EventDef(0xFA),
    "owl": EventDef(0x125),
    # The Overwatch 2 Watchpoint Pack promo (2022): the OWL scene, and the main menu's "What's new?"
    # box shows the pack with a Buy link to the Battle.net shop (01B/1215).
    "watchpoint": EventDef(0x126),
    # The notice before Overwatch 2 (spend your credits by 2 October): the one event splash 1.74
    # still wires (01B/0C74 shows 05A/07D7 when this celebration is greeted).
    "ow2_credits": EventDef(0x123, splash=True),
}

# Skins that the Anniversary main menu scene puts on its heroes. The recorded 1.68 preload list has
# none of them, so we add them. Source: data/extracted_events_174.json.
PRELOAD_EXTRA_SKINS = tuple(
    SKIN_THEME_BASE | i for i in (0x49E1, 0x48A9, 0x49CF, 0x49E4, 0x49D8, 0x49D9, 0x49D1, 0x49A8)
)


def active_events(names: list[str] | None) -> list[EventDef]:
    """The presets for the profile's event names. A name like `0x105` is a raw celebration id."""
    events: list[EventDef] = []
    for name in names or []:
        name = str(name).strip().lower()
        event = EVENT_PRESETS.get(name)
        if event is None and name.startswith("0x"):
            event = EventDef(int(name, 16) & 0xFFFF)
        if event and event not in events:
            events.append(event)
    return events


def load_resource_keys() -> dict[int, tuple[int, bytes]]:
    """Content key GUID -> (name id, key bytes)."""
    entries = json.loads((DATA_DIR / "resource_keys_174.json").read_text(encoding="utf-8"))
    keys = {}
    for guid, entry in entries.items():
        name_id = int(entry["name"], 16) if entry["name"] else 0
        keys[int(guid, 16)] = (name_id, bytes.fromhex(entry["key"]))
    return keys


def load_map_swaps() -> dict[int, list[tuple[int, int]]]:
    """Celebration type index -> [(map, event variant of the map)], from DataTool's list-maps."""
    swaps: dict[int, list[tuple[int, int]]] = {}
    maps = json.loads((DATA_DIR / "extracted_maps.json").read_text(encoding="utf-8"))
    for guid, info in maps.items():
        base_map = MAP_BASE | _datatool_index(guid)
        for variant in info.get("CelebrationVariants") or []:
            kind = _datatool_index(variant["Virtual01C"])
            variant_map = MAP_BASE | _datatool_index(variant["MapInfo"]["GUID"])
            swaps.setdefault(kind, []).append((base_map, variant_map))
    return swaps


def _datatool_index(guid: str) -> int:
    """The index part of a DataTool GUID such as "1A3.0C3"."""
    return int(guid.split(".")[0], 16)


# Hero challenges. A challenge is also a record in the event list, with reward tiers and the stat
# that counts toward them. The stat is the recorded one (Ashe's Year of the Tiger 2022, week 1).
# In 1.74 it counts played plus won matches (a win counts twice), although the profile field is
# called challenge_wins.
CHALLENGE_WINS_STAT = 0x086000000000073F
CHALLENGE_TIER_WINS = 9

# The only challenges 1.74's Play menu has a banner for (01B/0B7C): celebrations 0x119, 0x11A and
# 0x11B, weeks 1-3 of Anniversary Remix 3. Rewards: participation icon, spray and skin, in that order.
CHALLENGES = {
    "Tracer's Comic Challenge": (EventDef(0x119, key=0x198), (0x4AEA, 0x4AEB, 0x4AEC)),
    "Symmetra's Restoration Challenge": (EventDef(0x11A, key=0x198), (0x4B10, 0x4B11, 0x4B08)),
    "Kanezaka Challenge": (EventDef(0x11B, key=0x198), (0x4BB2, 0x4BB1, 0x4AA4)),  # Hanzo's, week 3
}


def challenge_event(challenge: str) -> EventDef:
    """The event record for one of the CHALLENGES."""
    return CHALLENGES[challenge][0]


def challenge_reward_ids(challenge: str) -> list[int] | None:
    """Unlock GUIDs of a challenge's rewards, or None when the title is not one of the CHALLENGES."""
    if challenge not in CHALLENGES:
        return None
    return [UNLOCK_BASE | index for index in CHALLENGES[challenge][1]]


@dataclass(frozen=True)
class EventInfo:
    """What the dashboard shows for an event. `scene_status` says how well its scene loads."""

    id: str
    label: str
    description: str
    category: str
    scene_status: str = "unverified"
    scene_note: str = ""


EVENT_INFO = [
    EventInfo(
        "goodbye",
        "Farewell to Overwatch",
        "General scene with the original Overwatch heroes.",
        "special",
        "verified",
        "Worked in previous checks.",
    ),
    EventInfo(
        "lunar",
        "Lunar New Year",
        "Year of the Tiger: festive maps and loot boxes.",
        "seasonal",
        "verified",
        "Worked in previous checks.",
    ),
    EventInfo(
        "halloween",
        "Halloween Terror",
        "Festive Eichenwalde and themed loot boxes.",
        "seasonal",
        "verified",
        "Worked in game.",
    ),
    EventInfo(
        "winter",
        "Winter Wonderland",
        "Winter scenes and festive loot boxes.",
        "seasonal",
        "verified",
        "Worked in previous checks.",
    ),
    EventInfo(
        "anniversary",
        "Anniversary: Remix Vol. 3",
        "Scene with heroes in anniversary skins.",
        "seasonal",
        "verified",
        "Scene switching confirmed in game after the update.",
    ),
    EventInfo(
        "anniversary_remix_1",
        "Anniversary: Remix Vol. 1",
        "Early version of the event with the same anniversary scene.",
        "seasonal",
        "verified",
        "Scene switching confirmed in game after the update.",
    ),
    EventInfo(
        "anniversary_remix_2",
        "Anniversary: Remix Vol. 2",
        "Second version of the event with the same anniversary scene.",
        "seasonal",
        "verified",
        "Scene switching confirmed in game after the update.",
    ),
    EventInfo(
        "summer",
        "Summer Games",
        'The "Summer Games" event and themed loot boxes.',
        "seasonal",
        "limited",
        "In this client version the event does not select a separate summer scene.",
    ),
    EventInfo(
        "archives",
        "Archives",
        'The "Archives" event and themed loot boxes.',
        "seasonal",
        "limited",
        'In this client version the event does not select a separate "Archives" scene.',
    ),
    EventInfo(
        "reaper",
        "Reaper Challenge",
        'The "Code of Violence" challenge scene.',
        "special",
        "verified",
        "Worked in previous checks.",
    ),
    EventInfo(
        "cassidy",
        "Cassidy Challenge",
        'The "New Blood" challenge scene.',
        "special",
        "verified",
        "Worked in previous checks.",
    ),
    EventInfo(
        "malevento",
        "Malevento",
        "Main menu with a Malevento view.",
        "special",
        "verified",
        "Worked in previous checks.",
    ),
    EventInfo(
        "owl",
        "Overwatch League: Genji",
        "Overwatch League scene with Genji.",
        "esports",
        "verified",
        "Worked in previous checks.",
    ),
    EventInfo(
        "watchpoint",
        "Watchpoint Pack",
        "Overwatch 2 pre-order promo in the What's new box, with the OWL scene.",
        "special",
        "verified",
        "Worked in game. The box's Buy button opens the Battle.net shop page.",
    ),
    EventInfo(
        "ow2_credits",
        "Overwatch 2 credits notice",
        "The splash from before Overwatch 2: spend your credits by 2 October.",
        "special",
        "verified",
        "Worked in game. The splash shows once per player, on the main menu.",
    ),
]
