"""Everything the lobby sends to the client, built from a profile and the client's data.

A message is a (protocol group CRC, message id, value) triple. The session maps the CRC to the
wire index the client announced and encodes the value with the client's schema. The keys of a value
are the field offsets inside the client's message struct ("+0x78"), as listed in
data/schemas_174.json.
"""

from ow174.accounts.profile import Profile
from ow174.catalog.events import PRELOAD_EXTRA_SKINS
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content.career import CareerMessages
from ow174.content.celebrations import Celebrations
from ow174.content.clock import server_time
from ow174.content.collection import Collection
from ow174.content.identity import Identity
from ow174.content.menu_hero import MenuHero
from ow174.content.player import PlayerMessages
from ow174.content.presence import Presence
from ow174.content.retail import RetailReplay
from ow174.jam.codec import Schemas
from ow174.jam.groups import (
    CONFIG,
    EVENTS,
    HERO_CATALOG,
    IN_CONNECT,
    MODE_RULES,
    PARTY,
    PERMISSIONS,
    PROGRESSION_IN,
)

__all__ = ["Content", "Identity"]


class Content:
    """The message builders, wired together, and the two message sets the session sends."""

    def __init__(self, schemas: Schemas, templates: RetailTemplates, items: ItemDB) -> None:
        self.collection = Collection(templates, items)
        self.menu_hero = MenuHero(self.collection, items)
        self.presence = Presence(templates)
        self.celebrations = Celebrations(templates, items, self.collection.owns)
        self.player = PlayerMessages(schemas, self.collection, self.menu_hero)
        self.career = CareerMessages(templates, self.collection, self.menu_hero, self.player)
        self.retail = RetailReplay(templates)

    def login_messages(self, profile: Profile, identity: Identity) -> list[tuple]:
        """Everything sent after the client logs in, in the order the retail server sent it."""
        self.menu_hero.reroll(profile)
        hero = self.menu_hero.choose(profile)
        return [
            (IN_CONNECT, 20500, self.player.hello(profile, identity)),
            (IN_CONNECT, 20502, {"+0x78": self.player.record(profile, identity)}),
            (IN_CONNECT, 20504, self.celebrations.content_keys(profile)),
            (IN_CONNECT, 20505, self.retail.preload(PRELOAD_EXTRA_SKINS)),
            (EVENTS, 38900, self.celebrations.records(profile)),
            (EVENTS, 38902, self.celebrations.progress(profile)),
            (PARTY, 20700, self.player.party_state(profile, identity, hero)),
            *self.player.endorsements(profile, identity),
            *self.presence.own(profile, identity),
            (PROGRESSION_IN, 24300, self.collection.progression(profile)),
            (HERO_CATALOG, 24900, self.collection.hero_catalog(profile)),
            (MODE_RULES, 27202, self.career.mode_rules(profile)),
            *self.retail.at_login(profile, identity),
        ]

    def live_messages(self, profile: Profile, identity: Identity) -> list[tuple]:
        """The state that can be refreshed while the client is connected (dashboard edits)."""
        hero = self.menu_hero.choose(profile)
        return [
            (CONFIG, 36602, {"+0x78": int(server_time(profile))}),
            (CONFIG, 36600, self.retail.menu_config(profile, identity)),
            (IN_CONNECT, 20502, {"+0x78": self.player.record(profile, identity)}),
            (IN_CONNECT, 20504, self.celebrations.content_keys(profile)),
            (EVENTS, 38900, self.celebrations.records(profile)),
            (EVENTS, 38902, self.celebrations.progress(profile)),
            (PARTY, 20700, self.player.party_state(profile, identity, hero)),
            *self.player.endorsements(profile, identity),
            (PERMISSIONS, 55500, self.player.features()),
            (PROGRESSION_IN, 24300, self.collection.progression(profile)),
            (HERO_CATALOG, 24900, self.collection.hero_catalog(profile)),
        ]
