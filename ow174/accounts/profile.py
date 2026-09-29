"""A player's saved progress: currencies, unlocks, loot boxes, loadouts and settings."""

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ow174.catalog.boxes import starter_boxes

log = logging.getLogger(__name__)

DEFAULT_ICON = 0x02500000000002F7
# Profiles saved before this marker was written had their unlocks reset once.
UNLOCKS_RESET_MARKER = "unlocks_reset_v2"


@dataclass
class Profile:
    player_name: str = "Researcher"
    region: str = "US"  # shop currency and country, see catalog/regions.py
    level: int = 100
    credits: int = 50000
    comp_points: int = 6000
    league_tokens: int = 1000
    icon_guid: int = DEFAULT_ICON
    frame_guid: int = 0  # 0 means the frame follows the level
    next_box_id: int = 1
    loot_boxes: list = field(default_factory=list)  # [{"id", "type", "name"}]
    unlocked_items: list = field(default_factory=list)  # GUIDs as hex text, e.g. "0x0250..."
    unlock_all: bool = False
    lobby_hero: str = "random"  # the menu hero: "random", "none", a hero name or a PvE character
    events: list = field(default_factory=lambda: ["goodbye"])  # active events, see catalog/events.py
    server_date: str = ""  # the clock sent to the client, e.g. "2022-10-03"; empty means now
    challenge: str = ""  # title of the active hero challenge
    challenge_wins: int = 0
    endorsement_level: int = 1
    loadouts: dict = field(default_factory=dict)  # hero GUID hex -> {slot key: unlock hex or list}
    settings: dict = field(default_factory=dict)  # settings the client saves (22200-22204)
    stats: dict = field(
        default_factory=lambda: {
            "boxes_opened": 0,
            "legendaries_dropped": 0,
            "credits_earned_from_duplicates": 0,
        }
    )

    def unlocked_guids(self) -> set[int]:
        """The extra unlocks as integers; entries that are not numbers are ignored."""
        guids = set()
        for item in self.unlocked_items:
            try:
                guids.add(int(item, 0) if isinstance(item, str) else int(item))
            except ValueError:
                continue
        return guids

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        """Build a profile from saved JSON. Unknown keys are ignored, so old files still load."""
        known = {profile_field.name for profile_field in fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        for key in ("icon_guid", "frame_guid"):  # saved as hex text
            if key in values:
                values[key] = int(str(values[key]), 0)
        return cls(**values)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["icon_guid"] = f"0x{self.icon_guid:016X}"
        data["frame_guid"] = f"0x{self.frame_guid:016X}"
        data[UNLOCKS_RESET_MARKER] = True
        return data


def save_profile(profile: Profile, path: Path) -> None:
    """Write the profile atomically, so a crash never leaves half a file."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def new_profile() -> Profile:
    boxes, next_id = starter_boxes()
    return Profile(next_box_id=next_id, loot_boxes=boxes)


def _load_profile(path: Path) -> Profile:
    data = json.loads(path.read_text(encoding="utf-8"))
    profile = Profile.from_dict(data)
    if data.get(UNLOCKS_RESET_MARKER) is not True:
        profile.unlocked_items = []
        save_profile(profile, path)
    log.info(
        "Loaded profile '%s' from %s (level %d, boxes %d, credits %d, unlocks %d, events %s)",
        profile.player_name,
        path.name,
        profile.level,
        len(profile.loot_boxes),
        profile.credits,
        len(profile.unlocked_items),
        profile.events,
    )
    return profile


def load_or_create_profile(path: Path) -> Profile:
    """Load a profile, or create and save a fresh one when the file is missing or unreadable."""
    if path.is_file():
        try:
            return _load_profile(path)
        except (OSError, ValueError, TypeError) as error:
            log.warning("Could not read %s (%s), creating a new profile", path, error)
    profile = new_profile()
    save_profile(profile, path)
    log.info("Created a new profile with %d loot boxes in %s", len(profile.loot_boxes), path.name)
    return profile
