"""What the dashboard can do with an account, independent of HTTP."""

import threading
import time
from collections import Counter
from copy import deepcopy
from datetime import date

from ow174.accounts.profile import Profile, save_profile
from ow174.accounts.registry import Account, account_id_for
from ow174.catalog.boxes import BOX_TYPES
from ow174.catalog.events import EVENT_INFO, EVENT_PRESETS
from ow174.catalog.regions import REGIONS
from ow174.content.menu_hero import PVE_NPCS
from ow174.dashboard.errors import ApiError, parse_bool, parse_guid, parse_int

PROFILE_FIELDS = (
    "player_name",
    "region",
    "level",
    "credits",
    "comp_points",
    "league_tokens",
    "endorsement_level",
    "lobby_hero",
    "events",
    "server_date",
    "challenge",
    "challenge_wins",
    "unlock_all",
    "stats",
)
EDITABLE_FIELDS = frozenset(PROFILE_FIELDS) - {"stats"} | {"account"}
# Whole-number fields that only need a range check; the level starts at 1, the rest at 0.
NUMBER_FIELDS = ("level", "credits", "comp_points", "league_tokens", "challenge_wins")
# Scenes the picker hides: the event loads no scene of its own in this client.
HIDDEN_SCENES = ("limited", "unavailable")
UNLOCK_KINDS = {"skins": ("Skin", "WeaponSkin"), "icons": ("Icon",), "sprays": ("Spray",)}
RARITY_RANK = {"Common": 0, "Rare": 1, "Epic": 2, "Legendary": 3}
SKIN_PAGE_SIZE = 48
DATE_HINT = "Use a date like 2022-10-03."


def guid_text(guid: int) -> str:
    return f"0x{guid:016X}"


def profile_snapshot(profile: Profile) -> dict:
    """The profile as the page sees it, plus loot box and collection counts."""
    result = {name: deepcopy(getattr(profile, name)) for name in PROFILE_FIELDS}
    result["loot_boxes_count"] = len(profile.loot_boxes)
    result["unlocked_count"] = len(profile.unlocked_items)
    result["box_counts"] = box_counts(profile.loot_boxes)
    return result


def box_counts(loot_boxes: list) -> list:
    """How many boxes of each type the player has, sorted by type."""
    counts = Counter(box["type"] for box in loot_boxes)
    rows = []
    for kind, count in sorted(counts.items()):
        box = BOX_TYPES.get(kind)
        rows.append(
            {
                "type": kind,
                "name": box.name if box is not None else str(kind),
                "label": box.label if box is not None else str(kind),
                "count": count,
            }
        )
    return rows


def parse_server_date(value) -> str:
    """The server date setting: "", "now", or an ISO date the client clock can hold."""
    if not isinstance(value, str):
        raise ApiError(DATE_HINT)
    value = value.strip()
    if value in ("", "now"):
        return value
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ApiError(DATE_HINT) from None
    if parsed.isoformat() != value:
        raise ApiError(DATE_HINT)
    # The event start is two days earlier (an unsigned STU year from 2000), and CONFIG 36602 holds a
    # u32 Unix timestamp at UTC noon.
    if not date(2000, 1, 3) <= parsed <= date(2106, 2, 6):
        raise ApiError("Server date must be between 2000-01-03 and 2106-02-06")
    return value


def parse_player_name(value) -> str:
    if not isinstance(value, str):
        raise ApiError("Enter a valid nickname")
    name = value.strip()
    has_control_character = any(ord(char) < 32 for char in name)
    if not 1 <= len(name) <= 32 or has_control_character:
        raise ApiError("Nickname must be 1 to 32 characters")
    return name


def parse_events(value) -> list:
    """The lobby event list: empty, or one event id. The page sends a list, a plain form a
    comma-separated string."""
    if isinstance(value, str):
        value = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not isinstance(value, list) or len(value) > 1:
        raise ApiError("Pick an event from the list.")
    for event_id in value:
        if not isinstance(event_id, str) or event_id not in EVENT_PRESETS:
            raise ApiError("Pick an event from the list.")
    return list(value)


def is_owl_item(unlock) -> bool:
    return "OWL" in (unlock.categories or [])


def is_yes(value) -> bool:
    return str(value or "").lower() in ("1", "true", "yes", "on")


def page_number(value) -> int:
    """A 1-based page number; anything unreadable means the first page."""
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def skin_sort_key(unlock) -> tuple:
    """By hero (heroless items last), then rarest first, then by name."""
    return (unlock.hero or "~", -RARITY_RANK.get(unlock.rarity, 0), unlock.name.lower())


class DashboardService:
    def __init__(self, lobby) -> None:
        self.lobby = lobby
        self.started = time.monotonic()
        self.lock = getattr(lobby, "state_lock", threading.RLock())

    def account(self, name=None) -> Account:
        """The saved account with this name (any letter case), or the selected one when no name
        is given."""
        if name is None or name == "":
            return self.lobby.dashboard_account()
        if not isinstance(name, str):
            raise ApiError("Invalid profile name")
        wanted = name.strip().lower()
        for saved_name in self.lobby.accounts.all_saved():
            if saved_name.lower() == wanted:
                return self.lobby.accounts.get(saved_name)
        raise ApiError("Profile not found.", 404)

    def _save(self, account: Account, profile: Profile) -> None:
        save_profile(profile, account.path)
        account.profile = profile
        self.lobby.push_profile(account)

    # Reads

    def state(self, name=None) -> dict:
        """Everything the page shows: the profile, all accounts, the server and the pick lists."""
        account = self.account(name)
        online = set(self.lobby.social.sessions)
        return {
            "profile": profile_snapshot(account.profile),
            "accounts": self._account_rows(account, online),
            "server": self._server_info(online),
            "catalogs": self._catalogs(),
        }

    def _account_rows(self, selected: Account, online: set) -> list:
        rows = []
        for name in self.lobby.accounts.all_saved():
            rows.append(
                {
                    "name": name,
                    "online": account_id_for(name) in online,
                    "selected": name.lower() == selected.name.lower(),
                }
            )
        return rows

    def _server_info(self, online: set) -> dict:
        matches = getattr(self.lobby, "matches", None)
        settings = self.lobby.settings
        return {
            "host": settings.host,
            "port": settings.port,
            "connected_clients": len(online),
            "uptime_seconds": int(time.monotonic() - self.started),
            "game_instances": matches.snapshot() if matches else [],
            "game_runtime_available": matches is not None,
            "matchmaking_supported": False,
        }

    def _catalogs(self) -> dict:
        return {
            "heroes": self._lobby_heroes(),
            "npcs": sorted(PVE_NPCS),
            "events": self._visible_events(),
            "box_types": [
                {"id": kind, "name": box.name, "label": box.label} for kind, box in BOX_TYPES.items()
            ],
            "challenges": self._challenges(),
        }

    def _lobby_heroes(self) -> list:
        """Heroes that can stand in the lobby (those with a default loadout), sorted by name."""
        loadouts = self.lobby.content.collection.default_loadouts
        heroes = sorted(self.lobby.items.hero_names.items(), key=lambda row: row[1])
        return [{"guid": guid_text(guid), "name": name} for guid, name in heroes if guid in loadouts]

    @staticmethod
    def _visible_events() -> list:
        info_by_id = {info.id: info for info in EVENT_INFO}
        events = []
        for event_id in EVENT_PRESETS:
            info = info_by_id[event_id]
            if info.scene_status not in HIDDEN_SCENES:
                events.append(vars(info))
        return events

    def _challenges(self) -> list:
        rows = []
        for title, rewards in sorted(self.lobby.items.challenges().items()):
            reward_rows = [{"name": reward.name, "type": reward.type} for reward in rewards]
            rows.append({"id": title, "title": title, "rewards": reward_rows})
        return rows

    def shop(self, query: dict) -> dict:
        return self._shop().catalog(
            self.account(query.get("account")).profile,
            q=query.get("q", ""),
            hero=query.get("hero", ""),
            currency=query.get("currency", ""),
            page=parse_int(query.get("page", 1), "Page", 1),
            page_size=parse_int(query.get("page_size", 24), "Page size", 1, 100),
        )

    def skins(self, query: dict) -> dict:
        """Skins, player icons or sprays with ownership, to grant them directly (including OWL and
        event items the shop no longer sells)."""
        account = self.account(query.get("account"))
        catalog = self._unlocks((query.get("kind") or "skins").strip())
        owned = account.profile.unlocked_guids()
        unlock_all = account.profile.unlock_all
        hero = (query.get("hero") or "").strip()
        text = (query.get("q") or "").strip().lower()
        owl_only = is_yes(query.get("owl"))
        page = page_number(query.get("page", 1))

        matches = []
        for unlock in catalog:
            if hero and (unlock.hero or "") != hero:
                continue
            if owl_only and not is_owl_item(unlock):
                continue
            if text and text not in unlock.name.lower():
                continue
            matches.append(unlock)
        matches.sort(key=skin_sort_key)

        start = (page - 1) * SKIN_PAGE_SIZE
        items = []
        for unlock in matches[start : start + SKIN_PAGE_SIZE]:
            items.append(
                {
                    "guid": guid_text(unlock.guid),
                    "name": unlock.name,
                    "hero": unlock.hero or "",
                    "rarity": unlock.rarity,
                    "type": unlock.type,
                    "owl": is_owl_item(unlock),
                    "owned": unlock_all or unlock.guid in owned,
                }
            )
        return {
            "items": items,
            "page": page,
            "page_size": SKIN_PAGE_SIZE,
            "total": len(matches),
            "unlock_all": unlock_all,
            "heroes": self._unlock_heroes(),
        }

    def _unlock_heroes(self) -> list:
        """Every hero that owns a skin, icon or spray, for the hero filter."""
        heroes = set()
        for kind in UNLOCK_KINDS:
            for unlock in self._unlocks(kind):
                if unlock.hero:
                    heroes.add(unlock.hero)
        return sorted(heroes)

    # Writes

    def update_profile(self, data: dict) -> dict:
        unknown = set(data) - EDITABLE_FIELDS
        if unknown:
            raise ApiError("Unknown field: " + ", ".join(sorted(unknown)))
        with self.lock:
            account = self.account(data.get("account"))
            profile = deepcopy(account.profile)
            self._apply(profile, data)
            self._save(account, profile)
            return {"status": "ok", "profile": profile_snapshot(profile)}

    def _apply(self, profile: Profile, data: dict) -> None:
        """Check and copy each field present in `data` onto the profile."""
        if "player_name" in data:
            profile.player_name = parse_player_name(data["player_name"])
        if "region" in data:
            if data["region"] not in REGIONS:
                raise ApiError("Unknown region")
            profile.region = data["region"]
        for field in NUMBER_FIELDS:
            if field in data:
                lowest = 1 if field == "level" else 0
                setattr(profile, field, parse_int(data[field], field, lowest))
        if "endorsement_level" in data:
            profile.endorsement_level = parse_int(data["endorsement_level"], "Endorsement level", 1, 5)
        if "unlock_all" in data:
            profile.unlock_all = parse_bool(data["unlock_all"])
        if "events" in data:
            profile.events = parse_events(data["events"])
        if "lobby_hero" in data:
            profile.lobby_hero = self._parse_lobby_hero(data["lobby_hero"])
        if "server_date" in data:
            profile.server_date = parse_server_date(data["server_date"])
        if "challenge" in data:
            profile.challenge = self._parse_challenge(data["challenge"])

    def _parse_lobby_hero(self, value) -> str:
        allowed = set(self.lobby.items.hero_names.values()) | set(PVE_NPCS) | {"random", "none"}
        if not isinstance(value, str) or value not in allowed:
            raise ApiError("Pick a hero from the list.")
        return value

    def _parse_challenge(self, value) -> str:
        """A challenge title, or "" for none."""
        if not isinstance(value, str):
            raise ApiError("Challenge not found")
        if value and value not in self.lobby.items.challenges():
            raise ApiError("Challenge not found")
        return value

    def add_boxes(self, data: dict) -> dict:
        kind = parse_int(data.get("type", 0), "Box type")
        count = parse_int(data.get("count", 10), "Box count", 1, 100)
        if kind not in BOX_TYPES:
            raise ApiError("No such box type in the catalog")
        name = BOX_TYPES[kind].name
        with self.lock:
            account = self.account(data.get("account"))
            profile = deepcopy(account.profile)
            # Never reuse an id, even if next_box_id fell behind the boxes already in the profile.
            highest_id = max((box["id"] for box in profile.loot_boxes), default=0)
            first_id = max(profile.next_box_id, highest_id + 1)
            for offset in range(count):
                profile.loot_boxes.append({"id": first_id + offset, "type": kind, "name": name})
            profile.next_box_id = first_id + count
            self._save(account, profile)
            return {"status": "ok", "added": count, "total_boxes": len(profile.loot_boxes)}

    def purchase(self, data: dict) -> dict:
        shop = self._shop()
        guid = parse_guid(data.get("guid"))
        with self.lock:
            account = self.account(data.get("account"))
            profile = deepcopy(account.profile)
            try:
                receipt = shop.purchase(profile, guid)
            except ValueError as error:
                raise ApiError(str(error), getattr(error, "status", 400)) from None
            self._save(account, profile)
            return {"status": "ok", "receipt": receipt, "profile": profile_snapshot(profile)}

    def grant_skin(self, data: dict) -> dict:
        """Grant one or more items by GUID, or take them back with "revoke": true."""
        guids = self._parse_item_guids(data)
        revoke = parse_bool(data["revoke"]) if "revoke" in data else False
        with self.lock:
            account = self.account(data.get("account"))
            profile = deepcopy(account.profile)
            owned = profile.unlocked_guids()
            if revoke:
                owned.difference_update(guids)
            else:
                owned.update(guids)
            profile.unlocked_items = [guid_text(guid) for guid in sorted(owned)]
            self._save(account, profile)
            changed = [guid_text(guid) for guid in guids]
            return {
                "status": "ok",
                "granted": [] if revoke else changed,
                "revoked": changed if revoke else [],
                "unlocked_count": len(owned),
                "profile": profile_snapshot(profile),
            }

    def _parse_item_guids(self, data: dict) -> list:
        """Known item GUIDs from "guids" (a list) or from a single "guid"."""
        values = data.get("guids")
        if values is None and "guid" in data:
            values = [data["guid"]]
        if not isinstance(values, list) or not values:
            raise ApiError("No skin selected")
        guids = []
        for value in values:
            guid = parse_guid(value)
            if self.lobby.items.get(guid) is None:
                raise ApiError("Invalid item")
            guids.append(guid)
        return guids

    def select_account(self, data: dict) -> dict:
        """Make this account the one a freshly started game logs in as."""
        self.lobby.select_account(self.account(data.get("name")).name)
        return {"status": "ok"}

    def reconnect(self) -> dict:
        self.lobby.reconnect_all()
        return {"status": "ok"}

    # Helpers

    def _shop(self):
        shop = getattr(self.lobby, "shop", None)
        if shop is None:
            raise ApiError("The shop catalog is not loaded yet.", 503)
        return shop

    def _unlocks(self, kind: str) -> list:
        """Named unlocks of one kind: "skins", "icons" or "sprays"."""
        types = UNLOCK_KINDS.get(kind)
        if types is None:
            raise ApiError("Unknown item type")
        return [
            unlock for unlock in self.lobby.items.unlocks.values() if unlock.type in types and unlock.name
        ]
