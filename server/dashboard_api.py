"""Account-scoped dashboard operations, independent of HTML and HTTP."""
from collections import Counter
from copy import deepcopy
from datetime import date
import threading
import time

try:
    from storage import BOX_TYPE_NAMES, save_profile
    from region import REGIONS
    from content import EVENT_PRESETS
    from social import account_id_for
except ImportError:
    from .storage import BOX_TYPE_NAMES, save_profile
    from .region import REGIONS
    from .content import EVENT_PRESETS
    from .social import account_id_for

BOX_LABELS = {0: 'Standard', 1: 'Summer Games', 2: 'Halloween', 3: 'Winter Wonderland',
              4: 'Lunar New Year', 5: 'Archives', 6: 'Anniversary', 7: 'Golden',
              9: 'Legendary Anniversary', 10: 'Ram', 12: 'Legendary'}
EVENT_LABELS = {'goodbye': 'Farewell to Overwatch', 'halloween': 'Halloween',
                'winter': 'Winter Wonderland', 'lunar': 'Lunar New Year',
                'anniversary': 'Anniversary', 'summer': 'Summer Games',
                'archives': 'Archives', 'owl': 'Overwatch League',
                'cassidy': "Cassidy's New Blood", 'malevento': 'Malevento',
                'reaper': 'Code of Violence', 'contenders': 'Contenders',
                'tracer': 'Tracer Comic', 'tracer_comic': 'Tracer Comic'}


class ApiError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def integer(value, label, low=0, high=2**31 - 1):
    if isinstance(value, bool) or isinstance(value, float):
        raise ApiError(f'{label}: enter a whole number')
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(f'{label}: enter a whole number') from None
    if not low <= number <= high:
        raise ApiError(f'{label}: allowed range is {low} to {high}')
    return number


def boolean(value):
    if isinstance(value, bool):
        return value
    if value in ('true', 'false'):
        return value == 'true'
    raise ApiError('Expected a value of true or false')


def profile_snapshot(profile):
    fields = ('player_name', 'region', 'level', 'credits', 'comp_points', 'league_tokens',
              'endorsement_level', 'lobby_hero', 'events', 'server_date', 'challenge',
              'challenge_wins', 'unlock_all', 'stats')
    result = {name: deepcopy(getattr(profile, name)) for name in fields}
    counts = Counter(box['type'] for box in profile.loot_boxes)
    result.update(loot_boxes_count=len(profile.loot_boxes), unlocked_count=len(profile.unlocked_items),
                  box_counts=[{'type': kind, 'name': BOX_TYPE_NAMES.get(kind, str(kind)),
                               'label': BOX_LABELS.get(kind, str(kind)), 'count': count}
                              for kind, count in sorted(counts.items())])
    return result


class DashboardService:
    def __init__(self, lobby):
        self.lobby = lobby
        self.started = time.monotonic()
        self.lock = getattr(lobby, 'state_lock', threading.RLock())

    def account(self, name=None):
        if name is None or name == '':
            return self.lobby.dashboard_account()
        if not isinstance(name, str):
            raise ApiError('Invalid profile name')
        names = self.lobby.accounts.all_saved()
        canonical = next((n for n in names if n.lower() == name.strip().lower()), None)
        if canonical is None:
            raise ApiError('Profile not found. Choose a saved player.', 404)
        return self.lobby.accounts.get(canonical)

    def state(self, name=None):
        account = self.account(name)
        online = set(self.lobby.social.sessions)
        try:
            from events import EVENT_CATALOG
        except ImportError:
            try:
                from .events import EVENT_CATALOG
            except ImportError:
                EVENT_CATALOG = []
        by_id = {row['id']: row for row in EVENT_CATALOG}
        events = [by_id.get(key, {'id': key, 'label': EVENT_LABELS.get(key, key),
                  'description': 'Cosmetics and seasonal content', 'category': 'special',
                  'scene_status': 'unverified', 'scene_note': 'Scene switching is being verified'})
                  for key in EVENT_PRESETS]
        # The user-facing picker is for actual lobby scenes. Keep research-only
        # or seasonal-content-only events in metadata, not as misleading tiles.
        events = [row for row in events if row.get('scene_status') not in ('limited', 'unavailable')]
        manager = getattr(self.lobby, 'matches', None)
        return {
            'profile': profile_snapshot(account.profile),
            'accounts': [{'name': n, 'online': account_id_for(n) in online,
                          'selected': n.lower() == account.name.lower()}
                         for n in self.lobby.accounts.all_saved()],
            'server': {'host': self.lobby.cfg.host, 'port': self.lobby.cfg.port,
                       'connected_clients': len(online),
                       'uptime_seconds': int(time.monotonic() - self.started),
                       'game_instances': manager.snapshot() if manager else [],
                       'game_runtime_available': manager is not None,
                       'matchmaking_supported': False},
            'catalogs': {
                'heroes': [{'guid': f'0x{guid:016X}', 'name': label}
                           for guid, label in sorted(self.lobby.items.hero_names.items(), key=lambda row: row[1])
                           if guid in self.lobby.content.default_loadouts],
                'events': events,
                'box_types': [{'id': kind, 'name': label, 'label': BOX_LABELS.get(kind, label)}
                              for kind, label in BOX_TYPE_NAMES.items()],
                'challenges': [{'id': title, 'title': title,
                                'rewards': [{'name': u.name, 'type': u.type} for u in rewards]}
                               for title, rewards in sorted(self.lobby.items.challenges().items())],
            },
        }

    def update_profile(self, data):
        allowed = {'account', 'player_name', 'region', 'level', 'credits', 'comp_points',
                   'league_tokens', 'endorsement_level', 'lobby_hero', 'events',
                   'server_date', 'challenge', 'challenge_wins', 'unlock_all'}
        unknown = set(data) - allowed
        if unknown:
            raise ApiError('Unknown field: ' + ', '.join(sorted(unknown)))
        with self.lock:
            account = self.account(data.get('account'))
            profile = deepcopy(account.profile)
            if 'player_name' in data:
                name = data['player_name']
                if not isinstance(name, str):
                    raise ApiError('Enter a valid nickname')
                name = name.strip()
                if not (1 <= len(name) <= 32) or any(ord(c) < 32 for c in name):
                    raise ApiError('Nickname must be 1 to 32 characters')
                profile.player_name = name
            if 'region' in data:
                if data['region'] not in REGIONS:
                    raise ApiError('Unknown region')
                profile.region = data['region']
            for field in ('level', 'credits', 'comp_points', 'league_tokens', 'challenge_wins'):
                if field in data:
                    setattr(profile, field, integer(data[field], field, 1 if field == 'level' else 0))
            if 'endorsement_level' in data:
                profile.endorsement_level = integer(data['endorsement_level'], 'Endorsement level', 1, 5)
            if 'unlock_all' in data:
                profile.unlock_all = boolean(data['unlock_all'])
            if 'events' in data:
                events = data['events']
                if isinstance(events, str):
                    events = [e.strip().lower() for e in events.split(',') if e.strip()]
                if not isinstance(events, list) or len(events) > 1 or any(not isinstance(e, str) or e not in EVENT_PRESETS for e in events):
                    raise ApiError('Select one event from the list or turn events off')
                profile.events = list(events)
            if 'lobby_hero' in data:
                hero = data['lobby_hero']
                allowed_heroes = set(self.lobby.items.hero_names.values()) | {'random', 'none'}
                if not isinstance(hero, str) or hero not in allowed_heroes:
                    raise ApiError('Select a hero from the list')
                profile.lobby_hero = hero
            if 'server_date' in data:
                value = data['server_date']
                if not isinstance(value, str):
                    raise ApiError('Date must be in YYYY-MM-DD format')
                value = value.strip()
                if value not in ('', 'now'):
                    try:
                        parsed_date = date.fromisoformat(value)
                        if parsed_date.isoformat() != value:
                            raise ValueError()
                    except ValueError:
                        raise ApiError('Date must be in YYYY-MM-DD format') from None
                    # Event start is two days earlier (unsigned STU year >=2000),
                    # and CONFIG36602 holds a u32 Unix timestamp at UTC noon.
                    if not date(2000, 1, 3) <= parsed_date <= date(2106, 2, 6):
                        raise ApiError('Server date must be between 2000-01-03 and 2106-02-06')
                profile.server_date = value
            if 'challenge' in data:
                challenge = data['challenge']
                if not isinstance(challenge, str) or (challenge and challenge not in self.lobby.items.challenges()):
                    raise ApiError('Challenge not found')
                profile.challenge = challenge
            save_profile(profile, account.path)
            account.profile = profile
            self.lobby.push_profile(account)
            return {'status': 'ok', 'profile': profile_snapshot(profile)}

    def add_boxes(self, data):
        kind = integer(data.get('type', 0), 'Box type')
        count = integer(data.get('count', 10), 'Box count', 1, 100)
        if kind not in BOX_TYPE_NAMES:
            raise ApiError('No such box type in the catalog')
        with self.lock:
            account = self.account(data.get('account'))
            profile = deepcopy(account.profile)
            next_id = max(profile.next_box_id, max((b['id'] for b in profile.loot_boxes), default=0) + 1)
            profile.loot_boxes.extend({'id': next_id + i, 'type': kind, 'name': BOX_TYPE_NAMES[kind]} for i in range(count))
            profile.next_box_id = next_id + count
            save_profile(profile, account.path)
            account.profile = profile
            self.lobby.push_profile(account)
            return {'status': 'ok', 'added': count, 'total_boxes': len(profile.loot_boxes)}

    def shop(self, query):
        service = getattr(self.lobby, 'shop', None)
        if service is None:
            raise ApiError('The shop catalog is not connected yet', 503)
        return service.catalog(self.account(query.get('account')).profile,
                               q=query.get('q', ''), hero=query.get('hero', ''),
                               currency=query.get('currency', ''),
                               page=integer(query.get('page', 1), 'Page', 1),
                               page_size=integer(query.get('page_size', 24), 'Page size', 1, 100))

    def purchase(self, data):
        service = getattr(self.lobby, 'shop', None)
        if service is None:
            raise ApiError('The shop catalog is not connected yet', 503)
        try:
            guid = int(data.get('guid', ''), 0) if isinstance(data.get('guid'), str) else int(data['guid'])
        except (TypeError, ValueError, KeyError):
            raise ApiError('Invalid item') from None
        with self.lock:
            account = self.account(data.get('account'))
            profile = deepcopy(account.profile)
            try:
                receipt = service.purchase(profile, guid)
            except ValueError as error:
                raise ApiError(str(error), getattr(error, 'status', 400)) from None
            save_profile(profile, account.path)
            account.profile = profile
            self.lobby.push_profile(account)
            return {'status': 'ok', 'receipt': receipt, 'profile': profile_snapshot(profile)}

    SKIN_TYPES = ('Skin', 'WeaponSkin')
    SKIN_PAGE_SIZE = 48

    def _skin_unlocks(self):
        return [u for u in self.lobby.items.unlocks.values() if u.type in self.SKIN_TYPES and u.name]

    def skins(self, query):
        """Searchable skin catalog with ownership - for granting skins directly
        (including OWL/event skins that are no longer obtainable in the shop)."""
        account = self.account(query.get('account'))
        owned, unlock_all = account.profile.get_unlocked_set(), account.profile.unlock_all
        hero = (query.get('hero') or '').strip()
        text = (query.get('q') or '').strip().lower()
        owl_only = str(query.get('owl') or '').lower() in ('1', 'true', 'yes', 'on')
        try:
            page = max(1, int(query.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        rows = []
        for u in self._skin_unlocks():
            if hero and (u.hero or '') != hero:
                continue
            if owl_only and 'OWL' not in (u.categories or []):
                continue
            if text and text not in u.name.lower():
                continue
            rows.append(u)
        rank = {r: i for i, r in enumerate(('Common', 'Rare', 'Epic', 'Legendary'))}
        rows.sort(key=lambda u: (u.hero or '~', -rank.get(u.rarity, 0), u.name.lower()))
        total = len(rows)
        start = (page - 1) * self.SKIN_PAGE_SIZE
        window = rows[start:start + self.SKIN_PAGE_SIZE]
        heroes = sorted({u.hero for u in self._skin_unlocks() if u.hero})
        return {
            'items': [{'guid': f'0x{u.guid:016X}', 'name': u.name, 'hero': u.hero or '',
                       'rarity': u.rarity, 'type': u.type, 'owl': 'OWL' in (u.categories or []),
                       'owned': unlock_all or u.guid in owned} for u in window],
            'page': page, 'page_size': self.SKIN_PAGE_SIZE, 'total': total,
            'unlock_all': unlock_all, 'heroes': heroes,
        }

    def grant_skin(self, data):
        """Grant (or revoke with 'revoke': true) one or more skins by GUID."""
        raw = data.get('guids')
        if raw is None and 'guid' in data:
            raw = [data['guid']]
        if not isinstance(raw, list) or not raw:
            raise ApiError('No skin selected')
        guids = []
        for g in raw:
            try:
                value = int(g, 0) if isinstance(g, str) else int(g)
            except (TypeError, ValueError):
                raise ApiError('Invalid item') from None
            if self.lobby.items.get(value) is None:
                raise ApiError('Invalid item')
            guids.append(value)
        revoke = boolean(data['revoke']) if 'revoke' in data else False
        with self.lock:
            account = self.account(data.get('account'))
            profile = deepcopy(account.profile)
            owned = profile.get_unlocked_set()
            owned = (owned - set(guids)) if revoke else (owned | set(guids))
            profile.unlocked_items = [f'0x{g:016X}' for g in sorted(owned)]
            save_profile(profile, account.path)
            account.profile = profile
            self.lobby.push_profile(account)
            tag = [f'0x{g:016X}' for g in guids]
            return {'status': 'ok', 'granted': [] if revoke else tag,
                    'revoked': tag if revoke else [], 'unlocked_count': len(owned),
                    'profile': profile_snapshot(profile)}
