"""
Overwatch 1.74 Profile Storage & Persistence Module
Handles persistent storage of player progress, loot boxes, cosmetics, and currencies to profile.json.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict

# Names of all official Overwatch 1 box types
BOX_TYPE_NAMES = {
    0: "Standard",
    1: "Summer Games",
    2: "Halloween Terror",
    3: "Winter Wonderland",
    4: "Lunar New Year",
    5: "Archives",
    6: "Anniversary",
    7: "Golden",
    9: "Legendary Anniversary",
    10: "Wrecking Ball",
    12: "Legendary",
}

@dataclass
class Profile:
    player_name: str = "Researcher"
    region: str = "US"  # shop currency and country, see region.py
    level: int = 100
    credits: int = 50000
    comp_points: int = 6000
    league_tokens: int = 1000
    icon_guid: int = 0x02500000000002F7
    frame_guid: int = 0  # 0 = auto-calculated from level
    next_box_id: int = 1
    loot_boxes: list = field(default_factory=list)  # list of {"id": int, "type": int, "name": str}
    unlocked_items: list = field(default_factory=list)  # list of hex strings e.g. "0x0250..."
    unlock_all: bool = False
    lobby_hero: str = "random"  # hero standing in the main menu: 'random', 'none' or a hero name
    events: list = field(default_factory=lambda: ["goodbye"])  # active celebrations, see content.EVENT_PRESETS
    server_date: str = ""  # clock sent to the client, e.g. "2022-10-03"; empty = the first event's season
    challenge: str = ""  # active challenge title, see ItemDB.challenges()
    challenge_wins: int = 0
    endorsement_level: int = 1
    loadouts: dict = field(default_factory=dict)  # hero GUID hex -> {catalog slot key: unlock hex or list}
    settings: dict = field(default_factory=dict)  # player settings the client saves (22200-22204), sent back in 20802
    stats: dict = field(default_factory=lambda: {
        "boxes_opened": 0,
        "legendaries_dropped": 0,
        "credits_earned_from_duplicates": 0
    })

    def get_unlocked_set(self) -> set:
        """Returns set of integer GUIDs."""
        out = set()
        for x in self.unlocked_items:
            try:
                out.add(int(x, 0) if isinstance(x, str) else int(x))
            except ValueError:
                pass
        return out

def create_default_boxes(start_id=1) -> tuple:
    """Creates a generous starter set of all loot box types."""
    types_config = [
        (0, 50),   # 50 Standard
        (1, 10),   # 10 Summer Games
        (2, 10),   # 10 Halloween Terror
        (3, 10),   # 10 Winter Wonderland
        (4, 10),   # 10 Lunar New Year
        (5, 10),   # 10 Archives
        (6, 10),   # 10 Anniversary
        (7, 10),   # 10 Golden Loot Boxes (Guaranteed Legendary!)
        (9, 10),   # 10 Legendary Anniversary
        (12, 10),  # 10 Legendary Loot Boxes (Guaranteed Legendary!)
    ]
    boxes = []
    cur_id = start_id
    for b_type, count in types_config:
        for _ in range(count):
            boxes.append({
                "id": cur_id,
                "type": b_type,
                "name": BOX_TYPE_NAMES.get(b_type, f"Type {b_type}")
            })
            cur_id += 1
    return boxes, cur_id

def load_or_create_profile(path: Path) -> Profile:
    """Loads profile from JSON, or creates a fresh one if not found."""
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            prof = Profile(
                player_name=data.get("player_name", "Researcher"),
                region=data.get("region", "US"),
                level=data.get("level", 100),
                credits=data.get("credits", 50000),
                comp_points=data.get("comp_points", 6000),
                league_tokens=data.get("league_tokens", 1000),
                icon_guid=int(str(data.get("icon_guid", "0x02500000000002F7")), 0),
                frame_guid=int(str(data.get("frame_guid", "0")), 0),
                next_box_id=data.get("next_box_id", 1),
                loot_boxes=data.get("loot_boxes", []),
                unlocked_items=data.get("unlocked_items", []),
                unlock_all=data.get("unlock_all", False),
                lobby_hero=data.get("lobby_hero", "random"),
                events=data.get("events", ["goodbye"]),
                server_date=data.get("server_date", ""),
                challenge=data.get("challenge", ""),
                challenge_wins=data.get("challenge_wins", 0),
                endorsement_level=data.get("endorsement_level", 1),
                loadouts=data.get("loadouts", {}),
                settings=data.get("settings", {}),
                stats=data.get("stats", {})
            )
            if data.get("unlocks_reset_v2") is not True:
                prof.unlocked_items = []
                save_profile(prof, path)
            print(f"[storage] Loaded profile '{prof.player_name}' from {path.name} (Level {prof.level}, Boxes: {len(prof.loot_boxes)}, Credits: {prof.credits}, Unlocks: {len(prof.unlocked_items)}, Events: {prof.events})")
            return prof
        except Exception as e:
            print(f"[storage] Error loading {path}: {e}. Creating new profile.")

    # Create default profile
    default_boxes, next_id = create_default_boxes(1)
    prof = Profile(
        player_name="Researcher",
        level=100,
        credits=50000,
        comp_points=6000,
        league_tokens=1000,
        next_box_id=next_id,
        loot_boxes=default_boxes
    )
    save_profile(prof, path)
    print(f"[storage] Created new default profile '{prof.player_name}' with {len(default_boxes)} loot boxes saved to {path.name}")
    return prof

def save_profile(profile: Profile, path: Path):
    """Saves profile to JSON atomically."""
    data = {
        "player_name": profile.player_name,
        "region": profile.region,
        "level": profile.level,
        "credits": profile.credits,
        "comp_points": profile.comp_points,
        "league_tokens": profile.league_tokens,
        "icon_guid": f"0x{profile.icon_guid:016X}",
        "frame_guid": f"0x{profile.frame_guid:016X}",
        "next_box_id": profile.next_box_id,
        "loot_boxes": profile.loot_boxes,
        "unlocked_items": profile.unlocked_items,
        "unlock_all": profile.unlock_all,
        "lobby_hero": profile.lobby_hero,
        "events": profile.events,
        "server_date": profile.server_date,
        "challenge": profile.challenge,
        "challenge_wins": profile.challenge_wins,
        "endorsement_level": profile.endorsement_level,
        "loadouts": profile.loadouts,
        "settings": profile.settings,
        "stats": profile.stats,
        "unlocks_reset_v2": True
    }
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)
