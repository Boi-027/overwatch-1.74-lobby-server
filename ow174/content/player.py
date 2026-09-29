"""The player's own card, party, endorsements, settings and account features."""

import time

from ow174.accounts.profile import Profile, battle_tag
from ow174.catalog.regions import game_region_number, region_of
from ow174.content.collection import Collection
from ow174.content.identity import Identity
from ow174.content.menu_hero import MenuHero
from ow174.content.ranked import Ranked
from ow174.jam.codec import Schemas
from ow174.jam.groups import ENDORSEMENTS, LOBBY
from ow174.jam.values import id16

# Account features, sent in message 55500 right after the endorsements. The client looks a feature
# up by id and treats it as enabled when it is present with a non-zero value. 0x948D allows sending
# chat and whispers. The client drops the connection right after login if the list is incomplete
# or the value is 1. Evidence: the ids and the value are copied from a retail 1.68 capture.
ACCOUNT_FEATURES = (
    0x0D800000000055ED,
    0x0D800000000055EE,
    0x0D800000000055EB,
    0x0D800000000055EC,
    0x0D800000000055F9,
    0x0D800000000056F2,
    0x0D800000000056F3,
    0x0D8000000000948D,
)
ACCOUNT_FEATURE_VALUE = 0xFF00000000000006

# The parts of 20802 the client saves on its own: 22200 saves +0x78, 22201 +0x108 (5 flags),
# 22202 +0x10D (17 bytes), 22203 +0x120 and 22204 +0x130.
SAVED_SETTINGS = ("+0x78", "+0x108", "+0x10D", "+0x120", "+0x130")
# Byte 5 of +0x10D says who may whisper: 0 nobody, 2 friends, anything else everyone.
# The client hides incoming whispers while it is 0, which is the schema default.
WHISPERS_FROM_EVERYONE = 1
# +0x130 holds values [{+0x0 value, +0x8 key}], a key being (type table << 16) | identifier
# (0x7FF7893512B0). While key B03A of table E0 is above 0, the login popups graph (01B/0C75)
# shows "N of your containers were opened" (05A/07D9, the move to Overwatch 2) on the main menu,
# and Continue sets it to -1 (22204).
BOXES_OPENED_KEY = (0xE0 << 16) | 0xB03A


def set_saved_value(profile: Profile, key: int, value: int) -> None:
    """Set one of the values in 20802 +0x130."""
    values = [entry for entry in profile.settings.get("+0x130", []) if entry.get("+0x8") != key]
    values.append({"+0x0": value, "+0x8": key})
    profile.settings["+0x130"] = values


# The three endorsement categories (STUIdentifier 01C, from 054/0x168 in the client data) and how
# many endorsements each one has. The ring around the level shows each category's share in its
# color, and stays grey when the list is empty.
ENDORSEMENT_CATEGORIES = {
    0x0D80000000003946: 40,  # sportsmanship, green
    0x0D80000000003945: 35,  # good teammate, purple
    0x0D80000000003944: 25,  # shot caller, orange
}


def endorsement(level: int) -> dict:
    counts = [{"+0x0": category, "+0x8": count} for category, count in ENDORSEMENT_CATEGORIES.items()]
    return {"+0x0": counts, "+0x18": level}


# 20812 in the capture.
RETAIL_UX_STATES = {0: 2, 4: 0}


class PlayerMessages:
    def __init__(self, schemas: Schemas, collection: Collection, menu_hero: MenuHero, ranked: Ranked) -> None:
        self._schemas = schemas
        self._collection = collection
        self._menu_hero = menu_hero
        self._ranked = ranked

    def record(self, profile: Profile, identity: Identity) -> dict:
        """The player card: ids, icon, portrait frame, level and name."""
        return {
            "+0x0": identity.account,
            "+0x10": identity.account,
            "+0x20": profile.icon_guid,
            "+0x28": self._collection.portrait_frame(profile),
            "+0x30": int(time.time()),
            "+0x38": profile.level,
            "+0x3C": 1,
            "+0x40": battle_tag(profile.player_name, identity.account_lo),
        }

    def hello(self, profile: Profile, identity: Identity) -> dict:
        """20500, the first message after the handshake."""
        return {
            "+0x78": identity.account,
            "+0x88": identity.account,
            "+0x98": {"+0x0": list(identity.session)},
            "+0xA8": battle_tag(profile.player_name, identity.account_lo),
            "+0xD0": battle_tag(profile.player_name, identity.account_lo),
            "+0xF8": region_of(profile.region).country,
            "+0x120": "",
            "+0x14C": game_region_number(profile.game_region),
            "+0x150": game_region_number(profile.game_region),
        }

    def party_member(self, profile: Profile, identity: Identity, hero: int, leader: bool) -> dict:
        skin = 0
        if hero:
            skin = self._collection.loadout(profile, hero).get("+0x38", 0)
        return {
            "+0x0": self.record(profile, identity),
            "+0x68": self._ranked.party_ratings(profile),
            "+0x80": endorsement(profile.endorsement_level),
            "+0xA0": [],
            "+0xB8": hero,
            "+0xC0": skin,
            "+0xC8": [],
            "+0xE0": 5,
            "+0xE1": 0,
            "+0xE2": leader,  # only the party leader has it; with it on everyone, all showed as leader
        }

    def party_state(self, profile: Profile, identity: Identity, hero: int) -> dict:
        """20700 for a player alone in their own party."""
        return self.party_state_for([(profile, identity)], identity.party_id, identity.party_entity, hero)

    def party_state_for(self, members: list, party_id: tuple, entity: tuple, hero: int | None = None) -> dict:
        """20700 for [(profile, identity), ...], leader first.

        Without a hero, each member shows their own menu hero.
        """
        member_records = []
        for index, (member_profile, member_identity) in enumerate(members):
            member_hero = hero
            if member_hero is None:
                member_hero = self._menu_hero.choose(member_profile)
            leader = index == 0
            member_records.append(self.party_member(member_profile, member_identity, member_hero, leader))
        return {
            "+0x78": {
                "+0x0": member_records,
                "+0x18": [],
                "+0x30": [],
                "+0x48": [],
                "+0x60": id16(*party_id),
                "+0x70": id16(*entity),
                "+0x80": {"+0x0": [0, 0]},
                "+0x90": 15959616,
                "+0x94": True,
                "+0x95": 1,
                "+0x96": True,
                "+0x98": True,
            }
        }

    def endorsements(self, profile: Profile, identity: Identity) -> list[tuple]:
        """The player's endorsement level (52002 and 52005) and an empty list (52006)."""
        level = profile.endorsement_level
        account_endorsement = {
            "+0x78": identity.account,
            "+0x88": endorsement(level),
            "+0xA8": 1,
            "+0xAC": 15.0,
            "+0xB0": 1,
            "+0xB4": 0,
        }
        own_endorsement = {
            "+0x78": endorsement(level),
            "+0x98": 1,
            "+0x9C": 15.0,
            "+0xA0": 1,
            "+0xA4": 0,
            "+0xA8": 1,
            "+0xB0": int(time.time()),
            "+0xB8": 0,
            "+0xBC": 0,
        }
        return [
            (ENDORSEMENTS, 52002, account_endorsement),
            (ENDORSEMENTS, 52005, own_endorsement),
            (ENDORSEMENTS, 52006, {"+0x78": []}),
        ]

    def ux_states(self, profile: Profile) -> dict:
        """20812: the interface states the client saved with 22207 (a dialog seen, "don't show
        again", ...), over the capture's. The client keeps them as index -> value
        (0x7FF789535EB0) and reads a missing one as 0."""
        states = dict(RETAIL_UX_STATES)
        for index, value in (profile.ux_states or {}).items():
            states[int(index)] = value
        return {"+0x78": [{"+0x0": value, "+0x4": index} for index, value in sorted(states.items())]}

    def settings(self, profile: Profile) -> dict:
        """20802: the player's saved settings, with defaults for the parts never saved."""
        saved = profile.settings or {}
        value = self._schemas.empty(LOBBY, 20802)
        for key in SAVED_SETTINGS:
            if key in saved:
                value[key] = saved[key]
        if "+0x10D" not in saved:
            value["+0x10D"]["+0x5"] = WHISPERS_FROM_EVERYONE
        if "+0x108" not in saved:
            value["+0x108"]["+0x4"] = True
        return value

    def features(self) -> dict:
        """55500: the account features list."""
        features = []
        for feature in ACCOUNT_FEATURES:
            features.append({"+0x0": feature, "+0x8": 0, "+0x10": ACCOUNT_FEATURE_VALUE})
        return {"+0x78": {"+0x0": features}}

    def summary(self, profile: Profile, identity: Identity) -> dict:
        """39002: the card other players see (name, icon, frame, level, endorsement)."""
        return {
            "+0x78": identity.account,
            "+0x88": {
                "+0x0": self.record(profile, identity),
                "+0x68": {},
                "+0x80": endorsement(profile.endorsement_level),
                "+0xA0": [],
                "+0xB8": 0.0,
                "+0xC0": [],
                "+0xD8": [],
            },
        }
