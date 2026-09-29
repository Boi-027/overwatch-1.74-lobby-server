"""Events and hero challenges as the client sees them (messages 38900-38902 and the content keys)."""

from collections.abc import Callable

from ow174.accounts.profile import Profile
from ow174.catalog.events import (
    CELEBRATION_BASE,
    CELEBRATION_TYPE_BASE,
    CHALLENGE_TIER_WINS,
    CHALLENGE_WINS_STAT,
    RESOURCE_KEY_BASE,
    EventDef,
    active_events,
    challenge_event,
    challenge_reward_ids,
    load_map_swaps,
    load_resource_keys,
)
from ow174.catalog.items import UNLOCK_BASE, ItemDB, Unlock
from ow174.catalog.templates import RetailTemplates
from ow174.content.clock import server_time, stu_datetime
from ow174.jam.groups import EVENTS, IN_CONNECT

DAY = 86400
# Every event looks like it started two days ago and runs for another year.
EVENT_STARTED_DAYS_AGO = 2
EVENT_ENDS_IN_DAYS = 365


class Celebrations:
    def __init__(
        self, templates: RetailTemplates, items: ItemDB, owns: Callable[[Profile, int], bool]
    ) -> None:
        self._templates = templates
        self._items = items
        self._owns = owns
        self.resource_keys = load_resource_keys()
        self._map_swaps = load_map_swaps()

    def records(self, profile: Profile) -> dict:
        """38900: one record per active event, plus one for the selected hero challenge."""
        now = server_time(profile)
        records = []
        for event in active_events(profile.events):
            records.append(self._record(event, now))
        rewards = self.challenge_rewards(profile)
        if rewards:
            records.append(self._challenge_record(profile, rewards, now))
        return {"+0x78": records}

    def notifications(self, profile: Profile) -> list[tuple]:
        """38901: announce the active celebrations as new, one message each.

        38900 only lists the celebrations. 38901 is what fills the client's "new celebration"
        queue. Send it once after login or an event change, not on every profile refresh. The
        client's own data and viewed state decide what it actually shows.
        """
        messages = []
        for record in self.records(profile)["+0x78"]:
            messages.append((EVENTS, 38901, {"+0x78": record}))
        return messages

    def progress(self, profile: Profile) -> dict:
        """38902: the challenge's win counter."""
        if not self.challenge_rewards(profile):
            return {"+0x78": []}
        celebration = _challenge(profile).celebration
        counter = {"+0x0": CELEBRATION_BASE | celebration, "+0x8": float(profile.challenge_wins)}
        return {"+0x78": [counter]}

    def challenge_rewards(self, profile: Profile) -> list[tuple[int, Unlock]]:
        """[(wins needed, unlock)] for the selected challenge, or [] when a reward is unknown."""
        title = profile.challenge or ""
        verified_guids = challenge_reward_ids(title)
        if verified_guids is None:
            rewards = self._items.challenges().get(title, [])
        else:
            rewards = [self._items.get(guid) for guid in verified_guids]
            if None in rewards:
                return []
        tiers = []
        for tier, unlock in enumerate(rewards, start=1):
            tiers.append((tier * CHALLENGE_TIER_WINS, unlock))
        return tiers

    def claim_rewards(self, profile: Profile) -> list[int]:
        """Grant the unlocks earned by the current win count that the profile does not own yet."""
        already_unlocked = profile.unlocked_guids()
        earned = []
        for wins_needed, unlock in self.challenge_rewards(profile):
            if profile.challenge_wins < wins_needed:
                continue
            if unlock.guid in already_unlocked or self._owns(profile, unlock.guid):
                continue
            earned.append(unlock.guid)
        # Added only after the loop, so ownership is checked against the profile as it was.
        profile.unlocked_items += [f"0x{guid:016X}" for guid in earned]
        return earned

    def keys(self, profile: Profile) -> list[int]:
        """Content keys of the active events and the challenge, in the order they are needed."""
        wanted = []
        for event in active_events(profile.events):
            if event.key:
                wanted.append(event.key)
        if self.challenge_rewards(profile):
            wanted.append(_challenge(profile).key)
        keys = []
        for key in wanted:
            if key in self.resource_keys and key not in keys:
                keys.append(key)
        return keys

    def content_keys(self, profile: Profile) -> dict:
        """20504: the capture's keys plus every named key in the client's data.

        Sending all of them lets any event's encrypted content load.
        """
        value = self._templates.first(IN_CONNECT, 20504)
        names = list(value["+0x78"])
        key_bytes = bytes(value["+0x90"])
        for key in self.keys(profile) + sorted(self.resource_keys):
            name, raw_key = self.resource_keys[key]
            if name and name not in names:
                names.append(name)
                key_bytes += raw_key
        return {"+0x78": names, "+0x90": list(key_bytes)}

    def _challenge_record(self, profile: Profile, rewards: list[tuple[int, Unlock]], now: float) -> dict:
        record = self._record(_challenge(profile), now)
        tiers = []
        for wins_needed, unlock in rewards:
            tiers.append({"+0x0": [unlock.guid], "+0x18": wins_needed})
        record["+0x18"] = tiers
        record["+0x78"] = CHALLENGE_WINS_STAT
        return record

    def _record(self, event: EventDef, now: float) -> dict:
        key_name = 0
        key_guid = 0
        if event.key:
            key_name = self.resource_keys.get(event.key, (0, b""))[0]
            key_guid = RESOURCE_KEY_BASE | event.key
        event_type = 0
        if event.kind:
            event_type = CELEBRATION_TYPE_BASE | event.kind
        map_swaps = []
        for normal_map, event_map in self._map_swaps.get(event.kind, []):
            map_swaps.append({"+0x0": normal_map, "+0x8": event_map})
        rewards = []
        for reward in event.rewards:
            rewards.append(UNLOCK_BASE | reward)
        return {
            "+0x0": map_swaps,
            "+0x18": [],
            "+0x30": key_name,
            "+0x38": key_guid,
            "+0x40": CELEBRATION_BASE | event.celebration,
            "+0x48": {"+0x0": stu_datetime(now - EVENT_STARTED_DAYS_AGO * DAY)},
            "+0x50": {"+0x0": stu_datetime(now + EVENT_ENDS_IN_DAYS * DAY)},
            "+0x58": rewards,
            "+0x70": event_type,
            "+0x78": 0,
            "+0x80": [],
            "+0x98": [],
            "+0xB0": event.box,
        }


def _challenge(profile: Profile) -> EventDef:
    return challenge_event(profile.challenge or "")
