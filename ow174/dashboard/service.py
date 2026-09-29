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
from ow174.catalog.regions import GAME_REGIONS, REGIONS
from ow174.content.collection import frame_parts
from ow174.content.menu_hero import PVE_NPCS
from ow174.content.passes import MAX_PASSES, POOLS
from ow174.content.player import BOXES_OPENED_KEY, set_saved_value
from ow174.content.ranked import CURRENT_SEASON, MAX_MATCHES, MAX_RATING, QUEUE_NAMES, matches_of, rating_of
from ow174.dashboard.errors import ApiError, parse_bool, parse_guid, parse_int
from ow174.jam.groups import PARTY
from ow174.lobby.handlers.party import MERGE_REQUEST, merge_request
from ow174.paths import WEB_DIR
from ow174.services.social import Party

PROFILE_FIELDS = (
    "player_name",
    "region",
    "game_region",
    "level",
    "credits",
    "comp_points",
    "league_tokens",
    "endorsement_level",
    "season",
    "sms_protect",
    "lobby_hero",
    "events",
    "server_date",
    "challenge",
    "challenge_wins",
    "unlock_all",
    "bot_chat",
    "stats",
)
# Competitive ratings, one field per queue ("rating_tank", ..., "rating_ctf").
RATING_FIELDS = {f"rating_{queue}": queue for queue in QUEUE_NAMES}
MATCH_FIELDS = {f"matches_{queue}": queue for queue in QUEUE_NAMES}
# Priority passes, one field per role queue pool ("passes_quick_play", "passes_competitive").
PASS_FIELDS = {f"passes_{pool}": pool for pool in POOLS}
# The group the bot lists in the group finder: Quick Play with roles, two of each (slot types
# 2 tank, 4 damage, 3 support).
BOT_GROUP_NAME = "Bot's group"
QUICK_PLAY_ROLE_QUEUE = 0x06300000000000ED
BOT_GROUP_SLOTS = (2, 2, 4, 4, 3, 3)
EDITABLE_FIELDS = (
    frozenset(PROFILE_FIELDS) - {"stats"}
    | {"account"}
    | frozenset(RATING_FIELDS)
    | frozenset(MATCH_FIELDS)
    | frozenset(PASS_FIELDS)
)
# Whole-number fields that only need a range check; the level starts at 1, the rest at 0.
NUMBER_FIELDS = ("level", "credits", "comp_points", "league_tokens", "challenge_wins")
# The collection's type filter: its value -> the item types it shows.
COLLECTION_KINDS = {
    "skins": ("Skin",),
    "weapons": ("WeaponSkin",),
    "icons": ("Icon",),
    "sprays": ("Spray",),
    "emotes": ("Emote",),
    "poses": ("VictoryPose",),
    "voicelines": ("VoiceLine",),
    "intros": ("HighlightIntro",),
}
ALL_ITEM_TYPES = tuple(item_type for types in COLLECTION_KINDS.values() for item_type in types)
PREVIEW_DIR = WEB_DIR / "assets" / "previews"  # <GUID>.webp, made by tools/export_item_previews.py
FRAME_TIERS = ("Bronze", "Silver", "Gold", "Platinum", "Diamond")  # collection.frame_parts tiers
RARITY_RANK = {"Common": 0, "Rare": 1, "Epic": 2, "Legendary": 3}
PAGE_SIZE = 24
DATE_HINT = "Use a date like 2022-10-03."


def frame_name(level: int) -> str:
    """How a frame looks, from its unlock level: "Gold ★★ · level 1441+"."""
    tier, stars, _ = frame_parts(level)
    return f"{FRAME_TIERS[tier]}{' ' + '★' * stars if stars else ''} · level {max(1, level)}+"


def guid_text(guid: int) -> str:
    return f"0x{guid:016X}"


def profile_snapshot(profile: Profile) -> dict:
    """The profile as the page sees it, plus loot box and collection counts."""
    result = {name: deepcopy(getattr(profile, name)) for name in PROFILE_FIELDS}
    result["loot_boxes_count"] = len(profile.loot_boxes)
    result["unlocked_count"] = len(profile.unlocked_items)
    result["box_counts"] = box_counts(profile.loot_boxes)
    result["frame"] = guid_text(profile.frame_guid) if profile.frame_guid else None  # chosen in Collection
    for name, queue in RATING_FIELDS.items():
        result[name] = rating_of(profile, queue)
    for name, queue in MATCH_FIELDS.items():
        result[name] = matches_of(profile, queue)
    for name, pool in PASS_FIELDS.items():
        result[name] = int((profile.priority_passes or {}).get(pool, 0))
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


def page_result(items: list, page: int, total: int) -> dict:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return {"items": items, "page": page, "total": total, "pages": pages}


def item_sort_key(unlock) -> tuple:
    """By hero (heroless items last), then rarest first, then by name."""
    return (unlock.hero or "~", -RARITY_RANK.get(unlock.rarity, 0), unlock.name.lower())


class DashboardService:
    def __init__(self, lobby) -> None:
        self.lobby = lobby
        self.started = time.monotonic()
        self.lock = getattr(lobby, "state_lock", threading.RLock())
        self._previews: set[str] | None = None  # GUIDs with a picture, read on first use

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

    def _save(self, account: Account, profile: Profile, granted: list[int] | None = None) -> None:
        save_profile(profile, account.path)
        account.profile = profile
        self.lobby.push_profile(account, granted)

    # Reads

    def state(self, name=None) -> dict:
        """Everything the page shows: the profile, all accounts, the server and the pick lists."""
        account = self.account(name)
        online = set(self.lobby.social.sessions)
        return {
            "profile": profile_snapshot(account.profile),
            "battle_tag": account.battle_tag,  # what friends type to add this player
            "accounts": self._account_rows(online),
            "server": self._server_info(online),
            "catalogs": self._catalogs(),
        }

    def _account_rows(self, online: set) -> list:
        """Every saved account; "selected" marks the one the game logs in as."""
        selected = self.lobby.dashboard_account()
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
            "events": self._events(),
            "box_types": [
                {"id": kind, "name": box.name, "label": box.label} for kind, box in BOX_TYPES.items()
            ],
            "challenges": self._challenges(),
            "frames": [
                {"level": level, "guid": guid_text(guid)}
                for level, guid in self.lobby.content.collection.border_levels
            ],
        }

    def _lobby_heroes(self) -> list:
        """Heroes that can stand in the lobby (those with a default loadout), sorted by name."""
        loadouts = self.lobby.content.collection.default_loadouts
        heroes = sorted(self.lobby.items.hero_names.items(), key=lambda row: row[1])
        return [{"guid": guid_text(guid), "name": name} for guid, name in heroes if guid in loadouts]

    @staticmethod
    def _events() -> list:
        """Every event the server can turn on. Those without a menu scene still bring their loot box,
        trophies and rewards."""
        info_by_id = {info.id: info for info in EVENT_INFO}
        return [vars(info_by_id[event_id]) for event_id in EVENT_PRESETS]

    def _challenges(self) -> list:
        rows = []
        for title, rewards in sorted(self.lobby.items.challenges().items()):
            reward_rows = [{"name": reward.name, "type": reward.type} for reward in rewards]
            rows.append({"id": title, "title": title, "rewards": reward_rows})
        return rows

    def collection(self, query: dict) -> dict:
        """Every cosmetic with what the player can do with it: buy it (when the shop sells it),
        unlock it for free, take it back, or, for portrait frames, pick it."""
        account = self.account(query.get("account"))
        page = page_number(query.get("page", 1))
        kind = (query.get("kind") or "all").strip()
        if kind == "frames":
            return self._frames(account, page)
        types = ALL_ITEM_TYPES if kind == "all" else COLLECTION_KINDS.get(kind)
        if types is None:
            raise ApiError("Unknown item type")
        hero = (query.get("hero") or "").strip()
        text = (query.get("q") or "").strip().lower()
        currency = (query.get("currency") or "").strip()
        owl_only = is_yes(query.get("owl"))
        shop = self._shop()

        matches = []
        for unlock in self.lobby.items.unlocks.values():
            if unlock.type not in types or not unlock.name:
                continue
            if hero and (unlock.hero or "") != hero:
                continue
            if owl_only and not is_owl_item(unlock):
                continue
            if text and text not in f"{unlock.name} {unlock.hero or ''}".lower():
                continue
            product = shop.product(unlock.guid)
            if currency and (product is None or product["currency"] != currency):
                continue
            matches.append((unlock, product))
        matches.sort(key=lambda pair: item_sort_key(pair[0]))

        profile = account.profile
        unlocked = profile.unlocked_guids()
        start = (page - 1) * PAGE_SIZE
        items = []
        for unlock, product in matches[start : start + PAGE_SIZE]:
            owned = profile.unlock_all or self.lobby.content.collection.owns(profile, unlock.guid)
            items.append(
                {
                    "guid": guid_text(unlock.guid),
                    "name": unlock.name,
                    "hero": unlock.hero or "",
                    "rarity": unlock.rarity,
                    "type": unlock.type,
                    "owl": is_owl_item(unlock),
                    "owned": owned,
                    # Only items unlocked here can be taken back; the starter ones stay.
                    "removable": unlock.guid in unlocked and not profile.unlock_all,
                    "price": product["price"] if product else None,
                    "currency": product["currency"] if product else None,
                    "purchasable": product is not None and not owned,
                    "preview": self._preview_url(unlock.guid),
                }
            )
        return page_result(items, page, len(matches))

    def _frames(self, account: Account, page: int) -> dict:
        """The portrait frames, lowest level first. The one on the player card is marked in_use."""
        collection = self.lobby.content.collection
        in_use = collection.portrait_frame(account.profile)
        chosen = account.profile.frame_guid
        start = (page - 1) * PAGE_SIZE
        items = []
        for level, guid in collection.border_levels[start : start + PAGE_SIZE]:
            items.append(
                {
                    "guid": guid_text(guid),
                    "name": frame_name(level),
                    "hero": "",
                    "rarity": "Common",
                    "type": "PortraitFrame",
                    "frame": True,
                    "in_use": guid == in_use,
                    "chosen": guid == chosen,
                    "preview": self._preview_url(guid),
                }
            )
        return page_result(items, page, len(collection.border_levels))

    def _preview_url(self, guid: int) -> str | None:
        """The item's picture in the dashboard, when there is one."""
        if self._previews is None:
            self._previews = {path.stem.upper() for path in PREVIEW_DIR.glob("*.webp")}
        name = f"{guid:016X}"
        return f"/assets/previews/{name}.webp" if name in self._previews else None

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
        if "game_region" in data:
            if data["game_region"] not in GAME_REGIONS:
                raise ApiError("Unknown game region")
            profile.game_region = data["game_region"]
        level_before = profile.level
        for field in NUMBER_FIELDS:
            if field in data:
                lowest = 1 if field == "level" else 0
                setattr(profile, field, parse_int(data[field], field, lowest))
        if profile.level != level_before:
            profile.frame_guid = 0  # a new level brings back the level's frame
        if "endorsement_level" in data:
            profile.endorsement_level = parse_int(data["endorsement_level"], "Endorsement level", 1, 5)
        if "season" in data:
            profile.season = parse_int(data["season"], "Season", 1, CURRENT_SEASON)
        for name, queue in RATING_FIELDS.items():
            if name in data:
                ratings = dict(profile.ratings or {})
                ratings[queue] = parse_int(data[name], f"{queue.capitalize()} rating", 1, MAX_RATING)
                profile.ratings = ratings
        for name, queue in MATCH_FIELDS.items():
            if name in data:
                matches = dict(profile.matches or {})
                matches[queue] = parse_int(data[name], f"{queue.capitalize()} matches", 0, MAX_MATCHES)
                profile.matches = matches
        for name, pool in PASS_FIELDS.items():
            if name in data:
                counts = dict(profile.priority_passes or {})
                counts[pool] = parse_int(data[name], "Priority passes", 0, MAX_PASSES)
                profile.priority_passes = counts
        if "sms_protect" in data:
            profile.sms_protect = parse_bool(data["sms_protect"])
        if "unlock_all" in data:
            profile.unlock_all = parse_bool(data["unlock_all"])
        if "bot_chat" in data:
            profile.bot_chat = parse_bool(data["bot_chat"])
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

    def open_all_boxes(self, data: dict) -> dict:
        """The move to Overwatch 2: every box opens, and the game says how many on the main menu."""
        with self.lock:
            account = self.account(data.get("account"))
            profile = deepcopy(account.profile)
            opened, granted = self.lobby.loot.open_all(profile)
            if not opened:
                raise ApiError("No boxes to open")
            set_saved_value(profile, BOXES_OPENED_KEY, opened)
            self._save(account, profile, granted=granted)
            self.lobby.push_settings(account)
            snapshot = profile_snapshot(profile)
            return {"status": "ok", "opened": opened, "new_items": len(granted), "profile": snapshot}

    def bot_group(self, data: dict) -> dict:
        """The bot in the group finder: list a group of its own, join the player's listed group,
        ask to merge the player's group into its own (invite), or leave. The player's client sees it
        in the next search or party update."""
        action = data.get("action")
        social = self.lobby.social
        with self.lock:
            if action == "list":
                self._list_bot_group()
                return {"status": "ok", "message": "The bot's group is in the group finder."}
            if action == "join":
                party = social.party_of(self.account(data.get("account")))
                if party.listing is None:
                    raise ApiError("List a group in the game first")
                if not social.free_slot_types(party):
                    raise ApiError("The group is full")
                social.join(self.lobby.accounts.bot, party)
                social.take_free_slot(party, self.lobby.accounts.bot)
                self.lobby.notify_party(party)
                return {"status": "ok", "message": "The bot joined your group."}
            if action == "remove":
                party = social.remove_bot_group()
                if party is not None:
                    self.lobby.notify_party(party)
                return {"status": "ok", "message": "The bot left the group finder."}
            if action == "invite":
                account = self.account(data.get("account"))
                session = self.lobby.session_of(account.account_lo)
                if session is None:
                    raise ApiError("The player is not in the game")
                bot = self.lobby.accounts.bot
                group = social.parties.get(bot.account_lo)
                if group is None or group.leader is not bot or group.listing is None:
                    group = self._list_bot_group()
                group.merge_invites.add(account.account_lo)
                session.send(PARTY, MERGE_REQUEST, merge_request(social, bot, group))
                return {"status": "ok", "message": "The bot asked to merge groups."}
        raise ApiError("Unknown bot action")

    def _list_bot_group(self) -> Party:
        """List the bot's own group; a group the bot was in without leading it gets the news."""
        social = self.lobby.social
        left = social.parties.get(self.lobby.accounts.bot.account_lo)
        listing = social.bot_listing(BOT_GROUP_NAME, QUICK_PLAY_ROLE_QUEUE, 1, BOT_GROUP_SLOTS)
        group = social.list_bot_group(listing)
        if left is not None and left is not group:
            self.lobby.notify_party(left)
        return group

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
            self._save(account, profile, granted=[guid, *(int(pair, 16) for pair in receipt["also"])])
            return {"status": "ok", "receipt": receipt, "profile": profile_snapshot(profile)}

    def set_frame(self, data: dict) -> dict:
        """Put a portrait frame on the player card, or go back to the level's frame with no GUID."""
        value = data.get("guid") or 0
        guid = parse_guid(value) if value else 0
        frames = {frame for _, frame in self.lobby.content.collection.border_levels}
        if guid and guid not in frames:
            raise ApiError("Invalid frame")
        with self.lock:
            account = self.account(data.get("account"))
            profile = deepcopy(account.profile)
            profile.frame_guid = guid
            self._save(account, profile)
            return {"status": "ok", "frame": guid_text(guid) if guid else None}

    def grant_skin(self, data: dict) -> dict:
        """Grant one or more items by GUID, or take them back with "revoke": true."""
        guids = self._parse_item_guids(data)
        revoke = parse_bool(data["revoke"]) if "revoke" in data else False
        with self.lock:
            account = self.account(data.get("account"))
            profile = deepcopy(account.profile)
            owned = profile.unlocked_guids()
            granted = [] if revoke else [guid for guid in guids if guid not in owned]
            if revoke:
                owned.difference_update(guids)
            else:
                owned.update(guids)
            profile.unlocked_items = [guid_text(guid) for guid in sorted(owned)]
            self._save(account, profile, granted)
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
