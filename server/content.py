"""
Everything the lobby sends, built from the player's profile, the client's
schemas and the static data of the retail capture (catalogs, preload lists,
arcade, events, config, store).

Messages are (protocol CRC, message id, value) triples; the session maps the
CRC to the wire index the client announced and encodes the value with the
schema. Field keys are the in-memory offsets from data/schemas_174.txt.
"""

import calendar
import json
import os
import random
import struct
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from jam_codec import id16, clone
    from retail import personalize, RETAIL_ACCOUNT
    from protocol import get_portrait_frame_guid
except ImportError:
    from .jam_codec import id16, clone
    from .retail import personalize, RETAIL_ACCOUNT
    from .protocol import get_portrait_frame_guid

IN_CONNECT = 0xF5FF548A       # 20500 hello, 20502 player record, 20504 content keys, 20505 preload
OUT_CONNECT = 0x3C7E3468      # 21800 login, 21809 locale
PARTY = 0x2411DE56            # 20700 party state
LOBBY = 0x1A9879A4            # 20800-20825 matchmaking/lobby status (leaver penalty settings, ...)
CUSTOM_GAMES = 0x21286B49     # 23301 custom game maps + training maps
PROGRESSION_IN = 0x779A8581   # 24300 record, 24305 box result, 24306 unlock granted, 24307 credits
PROGRESSION_OUT = 0x7F4F46CB  # 24201 open box, 24202 box opened, 24203 purchase
HERO_CATALOG = 0x70E5A247     # 24900 catalog, 24901 unlock granted, 24902 item equipped
GALLERY_OUT = 0xB68870B8      # 24500 equip {hero, unlock, slot}, 24501 purchase {hero, unlock}
MODE_RULES = 0xB9DCA497       # 27202 game modes per hero and map
ENDORSEMENTS = 0x79BFDDBE     # 52002 player, 52005 own summary, 52006 pending
FRIENDS = 0xF470E2AB          # 27100 friends, 27104 news, 27113 presence
PERMISSIONS = 0xBCD57A46      # 55500 account features [{identifier, 0, value}]
SOCIAL_OUT = 0x75D32AE2       # 22206 career profile request {viewer, target, section}
PROFILES = 0xD5550262         # 39001 profile status, 39002 profile summary
NAME_QUERY = 0x46DC9706       # 58202 account ids -> 58301
NAME_REPLY = 0x981D6A52       # 58301 names
ARCADE = 0x00A8654E
EVENTS = 0x8B77C823
CONFIG = 0x1732EBF2
REPLAYS = 0x5894D085
STORE = 0x4BAD7A7E            # 26400 products, 26404
STORE_QUERY = 0x5217E4CD      # 26500
VOICE_QUERY = 0xFB2E5CAC      # 51400, answered by 51500 with Vivox tokens; no voice server here
TELEMETRY = (0xC64B397E, 0x692C511B)

CATALOG_LOADOUT_SLOTS = ("+0x38", "+0x40", "+0x48", "+0x50", "+0x58", "+0x70", "+0x88")
SLOT_BY_TYPE = {"Skin": "+0x38", "HighlightIntro": "+0x40", "VictoryPose": "+0x48", "WeaponSkin": "+0x50"}
LIST_SLOT_BY_TYPE = {"Spray": "+0x58", "VoiceLine": "+0x70", "Emote": "+0x88"}

# Static retail messages replayed at login, in capture order (value personalized).
RETAIL_AT_LOGIN = [
    (LOBBY, 20814), (LOBBY, 20806), (LOBBY, 20812), (LOBBY, 20813), (LOBBY, 20809), (LOBBY, 20821),
    (0xAD1C34BA, 58501), (0xAA91BE18, 40900), (0xAA91BE18, 40914), (0x713D7589, 43200),
    (ARCADE, 39800), (ARCADE, 39801), (ARCADE, 39802), (ARCADE, 39803), (ARCADE, 39810),
    (ARCADE, 39806), (ARCADE, 39807), (ARCADE, 39804),
    (0x267FDE9E, 28001),
    (0x0DFEEFEF, 38300), (CONFIG, 36600), (CONFIG, 36603), (CONFIG, 36602),
    (REPLAYS, 51804), (REPLAYS, 51807), (REPLAYS, 51810), (REPLAYS, 51817), (REPLAYS, 51812),
    (0x34BB385D, 43301), (STORE, 26404), (STORE, 26400), (FRIENDS, 27104),
    (LOBBY, 20805), (LOBBY, 20820), (CUSTOM_GAMES, 23301),
]

CLIENT_BUILD = 104319
SUPPORTED_BUILDS_KEY = 0x04227E56

# Celebrations (STUCelebration, type 0C3 = runtime 0x0430...). The client picks
# its main menu lobby from the active one (STU_DFEC912B in 054/12C maps each
# celebration to a lobby map catalog); loot boxes point at the base ones.
CELEBRATION_BASE = 0x0430000000000000
# Events are records in 38900. What makes one real for the client, learned from
# the capture's Lunar New Year 2022 record:
#   +0x40 celebration (0C3)  -> lobby catalog (STU_DFEC912B, 054/12C)
#   +0x70 celebration type   -> map variants and event loot box (STUIdentifier 01C)
#   +0x30/+0x38 content key  -> name + STUResourceKey (090); the key itself goes out in 20504
#   +0x0 map swaps, +0x58 login rewards, +0xB0 event box type
# Keys come from data/resource_keys_174.json (client data matched to OWLib's keyring).
@dataclass(frozen=True)
class EventDef:
    celebration: int
    key: int = 0
    kind: int = 0
    box: int = -1
    rewards: tuple = ()


EVENT_PRESETS = {
    "goodbye": EventDef(0x128, key=0x1A3),                                              # 1.74 farewell lobby
    "lunar": EventDef(0x105, key=0x188, kind=0x237C, box=4, rewards=(0x0D5A, 0x4F50)),  # Year of the Tiger 2022
    "halloween": EventDef(0xFB, key=0x181, kind=0x212E, box=2),                         # Halloween Terror 2021
    "winter": EventDef(0xFF, key=0x185, kind=0x2168, box=3),                            # Winter Wonderland 2021
    "anniversary": EventDef(0x118, key=0x198, box=6),                                   # Anniversary Remix vol 3
    "anniversary_remix_1": EventDef(0x10E, key=0x196, box=6),                            # same E83 catalog, earlier selector
    "anniversary_remix_2": EventDef(0x112, key=0x197, box=6),
    "summer": EventDef(0xEB, key=0x178, box=1),                                         # Summer Games 2021
    "archives": EventDef(0xA8, key=0x169, box=5),                                       # Archives 2021
    "reaper": EventDef(0x104, key=0x189),                                               # Reaper's Code of Violence
    "cassidy": EventDef(0xF7, key=0x17F),                                               # Cassidy's New Blood
    "malevento": EventDef(0xFA),
    "owl": EventDef(0x125),
    "contenders": EventDef(0xC5, key=0x14A, rewards=(0x4B0E, 0x4B0D, 0x4A6A, 0x4A4B)),
}
# Skin themes (0A6) the lobby maps put on their hero (map instance data of entity 0x1629):
# OWL 0xEEE Genji, Reaper challenge 0xD2C Reaper "Dusk", Anniversary Remix 0xE83.
SKIN_THEME_BASE = 0x0A50000000000000
# E83 has six hero spawn overrides; the 1.68 capture contains none of their themes.
# Source: data/extracted_events_174.json, scene_assets, entity 000000001629.003.
PRELOAD_EXTRA_SKINS = tuple(SKIN_THEME_BASE | i for i in (
    0x49E1, 0x48A9, 0x49CF, 0x49E4, 0x49D8, 0x49D9, 0x49D1, 0x49A8,
))
UNLOCK_BASE = 0x0250000000000000
RESOURCE_KEY_BASE = 0x0F10000000000000
CELEBRATION_TYPE_BASE = 0x0D80000000000000
MAP_BASE = 0x0800000000000000
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_resource_keys() -> dict:
    keys = json.loads((DATA_DIR / "resource_keys_174.json").read_text())
    return {int(g, 16): (int(k["name"], 16) if k["name"] else 0, bytes.fromhex(k["key"])) for g, k in keys.items()}


def load_map_swaps() -> dict:
    """celebration type index -> [(map, event variant map)] from DataTool's list-maps."""
    swaps = {}
    for guid, m in json.loads((DATA_DIR / "extracted_maps.json").read_text(encoding="utf-8")).items():
        for v in m.get("CelebrationVariants") or []:
            kind = int(v["Virtual01C"].split(".")[0], 16)
            variant = int(v["MapInfo"]["GUID"].split(".")[0], 16)
            swaps.setdefault(kind, []).append((MAP_BASE | int(guid.split(".")[0], 16), MAP_BASE | variant))
    return swaps


def active_events(events: list) -> list:
    out = []
    for name in events or []:
        name = str(name).strip().lower()
        event = EVENT_PRESETS.get(name) or (EventDef(int(name, 16) & 0xFFFF) if name.startswith("0x") else None)
        if event and event not in out:
            out.append(event)
    return out


# Challenges ride on the event list: a record with reward tiers and the stat
# that counts toward them. Celebration, content key and stat are the retail
# capture's (Ashe's Year of the Tiger 2022 week 1 challenge).
CHALLENGE_CELEBRATION = 0x106
CHALLENGE_KEY = 0x188
CHALLENGE_WINS_STAT = 0x086000000000073F
CHALLENGE_TIER_WINS = 9
# In 1.74 this stat counts played + won matches (a win contributes two),
# despite the legacy profile field being named challenge_wins.
# Native Play graph B7C binds these current Remix 3 banners to 119 / 11A.
# Reward order is the extracted participation icon, spray, then skin.
VERIFIED_CHALLENGES = {
    "Tracer's Comic Challenge": (EventDef(0x119, key=0x198), (0x4AEA, 0x4AEB, 0x4AEC)),
    "Symmetra's Restoration Challenge": (EventDef(0x11A, key=0x198), (0x4B10, 0x4B11, 0x4B08)),
}
TIME_PLAYED_STAT = 0x0860000000000021   # STUStat 062/21 "Время в игре", per hero, lifetime (seconds)


def default_challenge_for_events(events) -> str | None:
    """Suggestion for explicit event selection; never overrides manual Off."""
    if any(str(name).strip().lower() == "anniversary" for name in events or []):
        return "Tracer's Comic Challenge"
    return None


def effective_challenge_definition(profile) -> EventDef:
    """Use verified native IDs, retaining the legacy custom-title fallback.

    Unsupported manual titles still use the captured Lunar challenge layout;
    that compatibility fallback does not establish a matching native banner.
    """
    verified = VERIFIED_CHALLENGES.get(profile.challenge or "")
    return verified[0] if verified else EventDef(CHALLENGE_CELEBRATION, key=CHALLENGE_KEY)


def server_time(profile) -> float:
    """The clock the lobby tells the client (36602): profile.server_date or now."""
    date = (profile.server_date or "").strip()
    if date and date.lower() != "now":
        return calendar.timegm(time.strptime(date[:10], "%Y-%m-%d")) + 12 * 3600
    return time.time()


RETAIL_APP_ACCOUNT = 0x3A169DEF   # the capture's Battle.net App game account
RETAIL_PRO_ACCOUNT = 0x3A169E4C   # the capture's Overwatch game account
RETAIL_FULL_NAME = b"ExampleUser"  # anonymized template marker, replaced with the local profile name


# Account features (STUIdentifier 0D8...), sent in 55500 right after the
# endorsements. The client keeps the list (handler 0x806750) and looks features
# up by id (0x43c7e0: present with a non-zero value). 0x948D gates sending chat
# and whispers (chat submit 0x84a6d0). The ids and the value are the retail
# server's (1.68 capture, data/postboot/w69_o00_0.bin; 1.68 had an extra u32
# before the list that 1.74 no longer reads). A partial list or value 1 makes
# the client drop the connection right after login.
ACCOUNT_FEATURES = (0x0D800000000055ED, 0x0D800000000055EE, 0x0D800000000055EB, 0x0D800000000055EC,
                    0x0D800000000055F9, 0x0D800000000056F2, 0x0D800000000056F3, 0x0D8000000000948D)
ACCOUNT_FEATURE_VALUE = 0xFF00000000000006
WHISPERS_FROM_EVERYONE = 1   # 20802 +0x10D+5; the client hides incoming whispers while it is 0


def rewrite_ids(blob: bytes, swap: dict) -> bytes:
    """Replace account ids inside presence protobuf values: fixed64 entity ids, the "<id>#1"
    game account name, and varints inside message values (the Overwatch game account's
    player id {1: 1, 2: high, 3: low}, which the client uses for invites and whispers)."""
    for old, new in swap.items():
        blob = blob.replace(struct.pack("<Q", old), struct.pack("<Q", new))
        blob = _replace_pb_string(blob, f"{old}#1".encode(), f"{new}#1".encode())
    return _rewrite_message_varints(blob, swap)


def _read_varint(blob: bytes, i: int) -> tuple:
    value = shift = 0
    while True:
        b = blob[i]
        value |= (b & 0x7F) << shift
        i += 1
        if b < 0x80:
            return value, i
        shift += 7


def _varint(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out + bytes([n]))


def _rewrite_message_varints(blob: bytes, swap: dict) -> bytes:
    """A Variant message_value (field 7) whose varint fields hold swapped ids is re-encoded."""
    if not blob.startswith(b"\x3a"):
        return blob
    try:
        size, i = _read_varint(blob, 1)
        if i + size != len(blob):
            return blob
        out, changed, end = bytearray(), False, len(blob)
        while i < end:
            key, j = _read_varint(blob, i)
            wire = key & 7
            if wire == 0:
                value, j = _read_varint(blob, j)
                if value in swap:
                    value, changed = swap[value], True
                out += _varint(key) + _varint(value)
            elif wire in (1, 5):
                j += 8 if wire == 1 else 4
                out += blob[i:j]
            elif wire == 2:
                n, k = _read_varint(blob, j)
                j = k + n
                out += blob[i:j]
            else:
                return blob
            i = j
    except IndexError:
        return blob
    return b"\x3a" + _varint(len(out)) + bytes(out) if changed else blob


def _replace_pb_string(blob: bytes, old: bytes, new: bytes) -> bytes:
    i = blob.find(old)
    if i < 2 or blob[i - 1] != len(old):
        return blob
    return blob[:i - 1] + bytes([len(new)]) + new + blob[i + len(old):]


def stu_datetime(t: float) -> int:
    """teStructuredDataDateAndTime: (year-2000)<<36 | month<<32 | day<<27 | hour<<22 | minute<<16 | second<<10."""
    d = time.gmtime(t)
    return ((d.tm_year - 2000) << 36) | (d.tm_mon << 32) | (d.tm_mday << 27) | (d.tm_hour << 22) | (d.tm_min << 16) | (d.tm_sec << 10)


@dataclass
class Identity:
    """Per-connection ids. The account id is the capture's, so replayed data refers to us."""
    account_lo: int
    session: tuple
    party_id: tuple
    party_entity: tuple

    @classmethod
    def create(cls, account_lo: int, seq: int):
        r = lambda: int.from_bytes(os.urandom(8), "little")
        entity = (seq & 0xFFFFFFFF) | (0x1D << 40) | (1 << 56)
        return cls(account_lo, (seq & 0xFFFFFFFF, 1), (r(), r()), (entity, r()))

    @property
    def account(self):
        return id16(self.account_lo)


class LobbyContent:
    def __init__(self, schemas, retail, items):
        self.schemas, self.retail, self.items = schemas, retail, items

        catalog = retail.first(HERO_CATALOG, 24900)
        self.catalog_template = catalog
        self.heroes = [h["+0x30"] for h in catalog["+0x80"]]
        self.default_hero_owned = {h["+0x30"]: [o["+0x0"] for o in h["+0x0"]] for h in catalog["+0x80"]}
        self.default_loadouts = {h["+0x30"]: {k: clone(h[k]) for k in CATALOG_LOADOUT_SLOTS} for h in catalog["+0x80"]}
        self.hero_of = {}
        self.store_entries = {}
        for store in catalog["+0x98"]:
            for entry in store["+0x0"]:
                self.hero_of[entry["+0x0"]] = store["+0x18"]
                self.store_entries[entry["+0x0"]] = entry

        progression = retail.first(PROGRESSION_IN, 24300)
        self.progression_template = progression
        self.default_account_owned = [o["+0x0"] for o in progression["+0x78"]["+0x30"]]
        self.account_entries = {e["+0x0"]: e for e in progression["+0xF8"]["+0x0"]}
        for guid, entry in self.account_entries.items():
            self.store_entries.setdefault(guid, entry)
        self._random_heroes = {}
        self.resource_keys = load_resource_keys()
        self.map_swaps = load_map_swaps()
        # Real level->portrait-frame progression: the 179 PortraitFrame unlocks with their unlock
        # level (+0x10). The top frame is 0x...9C4 at level 1791; there is nothing above it, so any
        # higher level keeps that frame instead of computing a non-existent GUID (which the client
        # renders as no border at all).
        self.border_levels = sorted(
            (e["+0x10"], guid) for guid, e in self.account_entries.items()
            if 0 <= e["+0x10"] < 65535
            and getattr(self.items.get(guid), "type", None) == "PortraitFrame")

    # ---------------------------------------------------------------- ownership

    def extra_unlocks(self, profile) -> list:
        return sorted(profile.get_unlocked_set())

    def owned_for_hero(self, profile, hero: int) -> list:
        owned = list(self.default_hero_owned.get(hero, []))
        seen = set(owned)
        if profile.unlock_all:
            extra = [g for g, h in self.hero_of.items() if h == hero]
        else:
            extra = [g for g in self.extra_unlocks(profile) if self.hero_of.get(g) == hero]
        owned += [g for g in extra if g not in seen]
        return owned

    def owned_account(self, profile) -> list:
        owned = list(self.default_account_owned)
        seen = set(owned)
        extra = list(self.account_entries) if profile.unlock_all else [
            g for g in self.extra_unlocks(profile) if g not in self.hero_of]
        owned += [g for g in extra if g not in seen]
        return owned

    def owns(self, profile, guid: int) -> bool:
        hero = self.hero_of.get(guid)
        if hero is not None:
            return guid in self.owned_for_hero(profile, hero)
        return guid in self.owned_account(profile)

    def price_of(self, guid: int):
        entry = self.store_entries.get(guid)
        return entry["+0x14"] if entry else None

    def portrait_frame(self, profile) -> int:
        if profile.frame_guid:
            return profile.frame_guid
        best = None
        for level, guid in self.border_levels:
            if level <= profile.level:
                best = guid
        return best or get_portrait_frame_guid(profile.level)

    def lobby_hero(self, profile) -> int:
        choice = (profile.lobby_hero or "random").strip()
        if choice.lower() == "none":
            return 0
        if choice.lower() != "random":
            hero = self.items.hero_by_name(choice)
            if hero is None and choice.lower().startswith("0x"):
                hero = int(choice, 16)
            if hero in self.default_loadouts:
                return hero
        return self._random_heroes.setdefault(profile.player_name, random.choice(self.heroes))

    def reroll_lobby_hero(self, profile):
        self._random_heroes.pop(profile.player_name, None)

    def loadout(self, profile, hero: int) -> dict:
        slots = clone(self.default_loadouts.get(hero, {}))
        for key, value in (profile.loadouts.get(f"0x{hero:016X}") or {}).items():
            if key in slots:
                slots[key] = [int(v, 0) for v in value] if isinstance(value, list) else int(value, 0)
        return slots

    # ---------------------------------------------------------------- login

    def player_record(self, profile, ident: Identity) -> dict:
        return {
            "+0x0": ident.account, "+0x10": ident.account,
            "+0x20": profile.icon_guid, "+0x28": self.portrait_frame(profile),
            "+0x30": int(time.time()), "+0x38": profile.level, "+0x3C": 1,
            "+0x40": profile.player_name,
        }

    def hello(self, profile, ident: Identity) -> dict:
        return {
            "+0x78": ident.account, "+0x88": ident.account,
            "+0x98": {"+0x0": list(ident.session)},
            "+0xA8": profile.player_name, "+0xD0": profile.player_name,
            "+0xF8": "", "+0x120": "",
        }

    def party_member(self, profile, ident: Identity, hero: int) -> dict:
        skin = self.loadout(profile, hero).get("+0x38", 0) if hero else 0
        return {
            "+0x0": self.player_record(profile, ident),
            "+0x68": [],
            "+0x80": {"+0x0": [], "+0x18": profile.endorsement_level},
            "+0xA0": [],
            "+0xB8": hero, "+0xC0": skin,
            "+0xC8": [],
            "+0xE0": 5, "+0xE1": 0, "+0xE2": True,
        }

    def party_state(self, profile, ident: Identity, hero: int) -> dict:
        return self.party_state_for([(profile, ident)], ident.party_id, ident.party_entity, hero)

    def party_state_for(self, members: list, party_id: tuple, entity: tuple, hero: int = None) -> dict:
        """20700 for [(profile, ident), ...]; the first member is the leader."""
        return {"+0x78": {
            "+0x0": [self.party_member(p, i, self.lobby_hero(p) if hero is None else hero) for p, i in members],
            "+0x18": [], "+0x30": [], "+0x48": [],
            "+0x60": id16(*party_id), "+0x70": id16(*entity),
            "+0x80": {"+0x0": [0, 0]},
            "+0x90": 15959616,
            "+0x94": True, "+0x95": 1, "+0x96": True, "+0x98": True,
        }}

    def endorsements(self, profile, ident: Identity) -> list:
        level = profile.endorsement_level
        return [
            (ENDORSEMENTS, 52002, {"+0x78": ident.account, "+0x88": {"+0x0": [], "+0x18": level},
                                   "+0xA8": 1, "+0xAC": 15.0, "+0xB0": 1, "+0xB4": 0}),
            (ENDORSEMENTS, 52005, {"+0x78": {"+0x0": [], "+0x18": level}, "+0x98": 1, "+0x9C": 15.0,
                                   "+0xA0": 1, "+0xA4": 0, "+0xA8": 1, "+0xB0": int(time.time()),
                                   "+0xB8": 0, "+0xBC": 0}),
            (ENDORSEMENTS, 52006, {"+0x78": []}),
        ]

    def presence_records(self, profile, account_lo: int) -> list:
        """Battle.net presence of an account (27100 +0xA8 / 27113): the account record and its
        Battle.net App and Overwatch ("Pro") game accounts, cloned from the capture with the
        ids inside the protobuf values rewritten."""
        app_lo, pro_lo = account_lo ^ 0x0A0A0000, account_lo ^ 0x0B0B0000
        swap = {RETAIL_ACCOUNT: account_lo, RETAIL_APP_ACCOUNT: app_lo, RETAIL_PRO_ACCOUNT: pro_lo}
        seen, out = set(), []
        for _, value in self.retail.all(FRIENDS, 27113):
            record = value["+0x78"][0]
            key = (record["+0x0"]["+0x0"], len(record["+0x20"]) > 3)
            if key in seen or not key[1]:
                continue
            seen.add(key)
            record = personalize(record, profile.player_name, account_lo)
            record["+0x0"]["+0x0"] = swap.get(record["+0x0"]["+0x0"], record["+0x0"]["+0x0"])
            for field in record["+0x20"]:
                field["+0x8"] = rewrite_ids(field["+0x8"], swap)
                field["+0x30"] = _replace_pb_string(rewrite_ids(field["+0x30"], swap), RETAIL_FULL_NAME,
                                                    profile.player_name.encode("utf-8"))
            out.append(record)
        return out

    def player_settings(self, profile) -> dict:
        """20802: the player's saved settings. The handler (0x7793a0) copies +0x108 (5 flags)
        to the settings system at +0x2A0 and +0x10D (17 bytes) to +0x2A5; +0x10D+5 is
        "allow private messages" (0 nobody, 2 friends, else everyone: whisper check 0x5ef340).
        The client saves changes with 22200 (+0x78), 22201 (+0x108), 22202 (+0x10D),
        22203 (+0x120) and 22204 (+0x130 values)."""
        saved = profile.settings or {}
        value = self.schemas.empty(LOBBY, 20802)
        for key in ("+0x78", "+0x108", "+0x10D", "+0x120", "+0x130"):
            if key in saved:
                value[key] = saved[key]
        if "+0x10D" not in saved:
            value["+0x10D"]["+0x5"] = WHISPERS_FROM_EVERYONE
        if "+0x108" not in saved:
            value["+0x108"]["+0x4"] = True
        return value

    def account_features(self) -> dict:
        return {"+0x78": {"+0x0": [{"+0x0": f, "+0x8": 0, "+0x10": ACCOUNT_FEATURE_VALUE} for f in ACCOUNT_FEATURES]}}

    def presence(self, profile, ident: Identity) -> list:
        """Our own presence (27113)."""
        return [(FRIENDS, 27113, {"+0x78": self.presence_records(profile, ident.account_lo)})]

    def progression(self, profile) -> dict:
        value = clone(self.progression_template)
        record = value["+0x78"]
        record["+0x0"] = [{"+0x0": {"+0x0": [b["id"], 0]}, "+0x10": b["type"], "+0x14": 1} for b in profile.loot_boxes]
        record["+0x18"] = []
        record["+0x30"] = [{"+0x0": g, "+0x8": True} for g in self.owned_account(profile)]
        record.update({
            "+0x48": 0, "+0x50": 2000,
            "+0x58": self.portrait_frame(profile), "+0x60": profile.icon_guid,
            "+0x68": profile.level, "+0x6C": profile.credits,
            "+0x70": profile.comp_points, "+0x74": profile.league_tokens,
            "+0x78": True, "+0x79": True,
        })
        return value

    def hero_catalog(self, profile) -> dict:
        value = clone(self.catalog_template)
        for record in value["+0x80"]:
            hero = record["+0x30"]
            record["+0x0"] = [{"+0x0": g, "+0x8": True} for g in self.owned_for_hero(profile, hero)]
            record.update(self.loadout(profile, hero))
        return value

    def celebrations(self, profile) -> dict:
        now = server_time(profile)
        records = [self._event_record(e, now) for e in active_events(profile.events)]
        challenge = self.challenge_rewards(profile)
        if challenge:
            record = self._event_record(effective_challenge_definition(profile), now)
            record.update({"+0x18": [{"+0x0": [u.guid], "+0x18": wins} for wins, u in challenge],
                           "+0x78": CHALLENGE_WINS_STAT})
            records.append(record)
        return {"+0x78": records}

    def event_notifications(self, profile) -> list:
        """38901: queue newly active celebrations in the native frontend.

        The retail capture sends these after its initial 38900 list. Unlike
        38900, the client handler populates its new-celebration queues. Call
        once after login/event changes, not on unrelated profile refreshes.
        Native assets and client viewed-state still decide which UI appears.
        """
        return [(EVENTS, 38901, {"+0x78": record})
                for record in self.celebrations(profile)["+0x78"]]

    def event_keys(self, profile) -> list:
        wanted = [e.key for e in active_events(profile.events) if e.key]
        if self.challenge_rewards(profile):
            wanted.append(effective_challenge_definition(profile).key)
        return [k for k in dict.fromkeys(wanted) if k in self.resource_keys]

    def challenge_progress(self, profile) -> dict:
        return {"+0x78": [{"+0x0": CELEBRATION_BASE | effective_challenge_definition(profile).celebration,
                          "+0x8": float(profile.challenge_wins)}]
                if self.challenge_rewards(profile) else []}

    def challenge_rewards(self, profile) -> list:
        """[(participation points needed, Unlock)] for the selected challenge."""
        verified = VERIFIED_CHALLENGES.get(profile.challenge or "")
        if verified:
            rewards = [self.items.get(UNLOCK_BASE | guid) for guid in verified[1]]
            if any(reward is None for reward in rewards):
                return []
        else:
            rewards = self.items.challenges().get(profile.challenge or "", [])
        return [((i + 1) * CHALLENGE_TIER_WINS, u) for i, u in enumerate(rewards)]

    def claim_challenge_rewards(self, profile) -> list:
        """Unlocks earned by the current win count that the profile does not own yet."""
        owned = profile.get_unlocked_set()
        earned = [u.guid for wins, u in self.challenge_rewards(profile)
                  if profile.challenge_wins >= wins and u.guid not in owned and not self.owns(profile, u.guid)]
        profile.unlocked_items += [f"0x{g:016X}" for g in earned]
        return earned

    def _event_record(self, event: EventDef, now: float) -> dict:
        key_name = self.resource_keys.get(event.key, (0, b""))[0] if event.key else 0
        return {
            "+0x0": [{"+0x0": a, "+0x8": b} for a, b in self.map_swaps.get(event.kind, [])],
            "+0x18": [], "+0x30": key_name, "+0x38": RESOURCE_KEY_BASE | event.key if event.key else 0,
            "+0x40": CELEBRATION_BASE | event.celebration,
            "+0x48": {"+0x0": stu_datetime(now - 2 * 86400)},
            "+0x50": {"+0x0": stu_datetime(now + 365 * 86400)},
            "+0x58": [UNLOCK_BASE | r for r in event.rewards],
            "+0x70": CELEBRATION_TYPE_BASE | event.kind if event.kind else 0,
            "+0x78": 0, "+0x80": [], "+0x98": [], "+0xB0": event.box,
        }

    def preload(self) -> dict:
        """20505: content the client treats as available (heroes, skin themes, map headers).
        The capture's lists are from 1.68; skins added later (like the ones the lobby maps put
        on their hero) have to be appended or the client does not spawn them."""
        value = self.retail.first(IN_CONNECT, 20505)
        have = set(value["+0x90"])
        value["+0x90"] += [s for s in PRELOAD_EXTRA_SKINS if s not in have]
        return value

    def content_keys(self, profile) -> dict:
        """The capture's four keys plus every named key in the client's data, so all event content decrypts."""
        value = self.retail.first(IN_CONNECT, 20504)
        names, blob = list(value["+0x78"]), bytes(value["+0x90"])
        for key in self.event_keys(profile) + sorted(self.resource_keys):
            name, raw = self.resource_keys[key]
            if name and name not in names:
                names.append(name)
                blob += raw
        return {"+0x78": names, "+0x90": list(blob)}

    def retail_static(self, profile, ident: Identity) -> list:
        now = int(server_time(profile))
        return [(crc, msg_id, self._refresh(crc, msg_id, personalize(value, profile.player_name, ident.account_lo), now))
                for crc, msg_id in RETAIL_AT_LOGIN for _, value in self.retail.all(crc, msg_id)]

    def _refresh(self, crc, msg_id, value, now: int):
        if (crc, msg_id) == (ARCADE, 39810):
            for window in value["+0x78"]:
                window["+0x8"], window["+0x10"] = now - 86400, now + 30 * 86400
        elif (crc, msg_id) == (CONFIG, 36602):
            value["+0x78"] = now
        elif (crc, msg_id) == (CONFIG, 36600):
            for entry in value["+0x78"]:
                if entry["+0x0"] == SUPPORTED_BUILDS_KEY and str(CLIENT_BUILD) not in entry["+0x8"]:
                    entry["+0x8"] = entry["+0x8"].rstrip("]") + f",{CLIENT_BUILD}]"
        return value

    def live_messages(self, profile, ident: Identity) -> list:
        """State that can be refreshed mid-session."""
        hero = self.lobby_hero(profile)
        msgs = [
            (CONFIG, 36602, {"+0x78": int(server_time(profile))}),
            (IN_CONNECT, 20502, {"+0x78": self.player_record(profile, ident)}),
            (IN_CONNECT, 20504, self.content_keys(profile)),
            (EVENTS, 38900, self.celebrations(profile)), (EVENTS, 38902, self.challenge_progress(profile)),
            (PARTY, 20700, self.party_state(profile, ident, hero)),
        ]
        msgs += self.endorsements(profile, ident)
        msgs += [(PERMISSIONS, 55500, self.account_features())]
        msgs += [(PROGRESSION_IN, 24300, self.progression(profile)),
                 (HERO_CATALOG, 24900, self.hero_catalog(profile))]
        return msgs

    def login_messages(self, profile, ident: Identity) -> list:
        self.reroll_lobby_hero(profile)
        hero = self.lobby_hero(profile)
        msgs = [
            (IN_CONNECT, 20500, self.hello(profile, ident)),
            (IN_CONNECT, 20502, {"+0x78": self.player_record(profile, ident)}),
            (IN_CONNECT, 20504, self.content_keys(profile)),
            (IN_CONNECT, 20505, self.preload()),
        ]
        msgs += [(EVENTS, 38900, self.celebrations(profile)), (EVENTS, 38902, self.challenge_progress(profile))]
        msgs += [(PARTY, 20700, self.party_state(profile, ident, hero))]
        msgs += self.endorsements(profile, ident)
        msgs += self.presence(profile, ident)
        msgs += [(PROGRESSION_IN, 24300, self.progression(profile)),
                 (HERO_CATALOG, 24900, self.hero_catalog(profile)),
                 (MODE_RULES, 27202, self.mode_rules(profile))]
        msgs += self.retail_static(profile, ident)
        return [m for m in msgs if m[2] is not None]

    # ---------------------------------------------------------------- replies

    def name_reply(self, ids: list, profile, ident: Identity) -> dict:
        names = []
        for account in ids:
            name = profile.player_name if account.get("+0x0") == ident.account_lo else f"Player{account.get('+0x0', 0) & 0xFFFF}"
            names.append({"+0x0": account, "+0x10": 0, "+0x18": name})
        return {"+0x78": names}

    def career_stats(self, profile) -> list:
        """Per-hero stats ([{[{[{stat, value}], hero}], category}]): the lobby hero gets the most time played."""
        hero = self.lobby_hero(profile)
        others = [h for h in self.heroes if h != hero][:4]
        hours = [(hero, 500.0)] + [(h, 50.0 - 10 * i) for i, h in enumerate(others)]
        heroes = [{"+0x0": [{"+0x0": TIME_PLAYED_STAT, "+0x8": h * 3600}], "+0x18": g} for g, h in hours if g]
        return [{"+0x0": heroes, "+0x18": 0}]

    def mode_rules(self, profile) -> dict:
        """27202: the capture's stat catalog per hero/map, plus our career stats."""
        value = self.retail.first(MODE_RULES, 27202)
        value["+0x78"] = self.career_stats(profile)
        value["+0xA8"] = self.career_stats(profile)
        return value

    def career_profile(self, profile, ident: Identity, target: dict) -> list:
        """Answer to 22206: the full player profile (20807) and its summary (39002).

        The 20807 handler (0x779280) reads the inline profile at +0x90 when +0x88
        is false; when true it expects the profile compressed in the +0x218 blob."""
        if target.get("+0x0") != ident.account_lo:
            return [(PROFILES, 39001, {"+0x78": target, "+0x88": 1})]
        endorsement = {"+0x0": [], "+0x18": profile.endorsement_level}
        full = {
            "+0x78": ident.account, "+0x88": False,
            "+0x90": {
                "+0x0": self.progression(profile)["+0x78"],
                "+0x80": self.hero_catalog(profile)["+0x80"],
                "+0x98": {"+0x0": []}, "+0xB0": self.career_stats(profile), "+0xC8": self.career_stats(profile),
                "+0xE0": endorsement, "+0x100": ident.account, "+0x110": [],
                "+0x128": 0, "+0x12C": 0, "+0x130": False,
                "+0x138": profile.player_name, "+0x160": "",
            },
            "+0x218": b"",
        }
        return [(LOBBY, 20807, full), (PROFILES, 39002, self.player_summary(profile, ident))]

    def player_summary(self, profile, ident: Identity) -> dict:
        """39002: the card other players see (name, icon, frame, level, endorsement)."""
        return {"+0x78": ident.account, "+0x88": {
            "+0x0": self.player_record(profile, ident), "+0x68": {},
            "+0x80": {"+0x0": [], "+0x18": profile.endorsement_level},
            "+0xA0": [], "+0xB8": 0.0, "+0xC0": [], "+0xD8": [],
        }}

    def unlock_granted(self, guid: int) -> tuple:
        """24901 {hero, unlock, new}: adds the unlock to the owned list and shows it as new."""
        return (HERO_CATALOG, 24901, {"+0x78": self.hero_of.get(guid, 0), "+0x80": guid, "+0x88": True})

    def equip(self, profile, hero: int, guid: int, slot: int) -> bool:
        """Store an equipped item in the profile; hero 0 is the account (player icon)."""
        unlock = self.items.get(guid)
        if hero == 0:
            if unlock is None or unlock.type != "Icon":
                return False
            profile.icon_guid = guid
            return True
        key = SLOT_BY_TYPE.get(unlock.type) if unlock else None
        list_key = LIST_SLOT_BY_TYPE.get(unlock.type) if unlock else None
        loadout = profile.loadouts.setdefault(f"0x{hero:016X}", {})
        if key:
            loadout[key] = f"0x{guid:016X}"
        elif list_key:
            current = loadout.get(list_key) or [f"0x{g:016X}" for g in self.loadout(profile, hero)[list_key]]
            slot = max(0, slot)
            current += [current[-1] if current else f"0x{guid:016X}"] * (slot + 1 - len(current))
            current[slot] = f"0x{guid:016X}"
            loadout[list_key] = current
        else:
            return False
        return True

    def box_result(self, box_id: list, drops: list) -> dict:
        return {"+0x78": {
            "+0x0": [{"+0x0": d["hero"], "+0x8": d["unlock"], "+0x10": d["credits"], "+0x14": d["order"],
                      "+0x18": d["highlight"], "+0x1C": d["duplicate"], "+0x1D": d["new"]} for d in drops],
            "+0x18": {"+0x0": list(box_id)},
            "+0x28": 0,
        }}
