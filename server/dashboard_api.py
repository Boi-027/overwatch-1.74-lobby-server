"""Account-scoped dashboard operations, independent of HTML and HTTP."""
from collections import Counter
from copy import deepcopy
from datetime import date
import threading
import time

try:
    from storage import BOX_TYPE_NAMES, save_profile
    from content import EVENT_PRESETS
    from social import account_id_for
except ImportError:
    from .storage import BOX_TYPE_NAMES, save_profile
    from .content import EVENT_PRESETS
    from .social import account_id_for

BOX_LABELS = {0: 'Обычный', 1: 'Летние игры', 2: 'Хэллоуин', 3: 'Зимняя сказка',
              4: 'Лунный Новый год', 5: 'Архивы', 6: 'Годовщина', 7: 'Золотой',
              9: 'Легендарная годовщина', 10: 'Таран', 12: 'Легендарный'}
EVENT_LABELS = {'goodbye': 'Прощание с Overwatch', 'halloween': 'Хэллоуин',
                'winter': 'Зимняя сказка', 'lunar': 'Лунный Новый год',
                'anniversary': 'Годовщина', 'summer': 'Летние игры',
                'archives': 'Архивы', 'owl': 'Overwatch League',
                'cassidy': 'Новая кровь Кэссиди', 'malevento': 'Малевенто',
                'reaper': 'Кодекс насилия', 'contenders': 'Contenders',
                'tracer': 'Комикс Tracer', 'tracer_comic': 'Комикс Tracer'}


class ApiError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def integer(value, label, low=0, high=2**31 - 1):
    if isinstance(value, bool) or isinstance(value, float):
        raise ApiError(f'{label}: введите целое число')
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(f'{label}: введите целое число') from None
    if not low <= number <= high:
        raise ApiError(f'{label}: допустимо от {low} до {high}')
    return number


def boolean(value):
    if isinstance(value, bool):
        return value
    if value in ('true', 'false'):
        return value == 'true'
    raise ApiError('Ожидается значение true или false')


def profile_snapshot(profile):
    fields = ('player_name', 'level', 'credits', 'comp_points', 'league_tokens',
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
            raise ApiError('Некорректное имя профиля')
        names = self.lobby.accounts.all_saved()
        canonical = next((n for n in names if n.lower() == name.strip().lower()), None)
        if canonical is None:
            raise ApiError('Профиль не найден. Выберите сохранённого игрока.', 404)
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
                  'description': 'Оформление и сезонный контент', 'category': 'special',
                  'scene_status': 'unverified', 'scene_note': 'Переключение сцены проверяется'})
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
        allowed = {'account', 'level', 'credits', 'comp_points', 'league_tokens',
                   'endorsement_level', 'lobby_hero', 'events', 'server_date',
                   'challenge', 'challenge_wins', 'unlock_all'}
        unknown = set(data) - allowed
        if unknown:
            raise ApiError('Неизвестное поле: ' + ', '.join(sorted(unknown)))
        with self.lock:
            account = self.account(data.get('account'))
            profile = deepcopy(account.profile)
            for field in ('level', 'credits', 'comp_points', 'league_tokens', 'challenge_wins'):
                if field in data:
                    setattr(profile, field, integer(data[field], field, 1 if field == 'level' else 0))
            if 'endorsement_level' in data:
                profile.endorsement_level = integer(data['endorsement_level'], 'Уровень одобрения', 1, 5)
            if 'unlock_all' in data:
                profile.unlock_all = boolean(data['unlock_all'])
            if 'events' in data:
                events = data['events']
                if isinstance(events, str):
                    events = [e.strip().lower() for e in events.split(',') if e.strip()]
                if not isinstance(events, list) or len(events) > 1 or any(not isinstance(e, str) or e not in EVENT_PRESETS for e in events):
                    raise ApiError('Выберите одно событие из списка или отключите события')
                profile.events = list(events)
            if 'lobby_hero' in data:
                hero = data['lobby_hero']
                allowed_heroes = set(self.lobby.items.hero_names.values()) | {'random', 'none'}
                if not isinstance(hero, str) or hero not in allowed_heroes:
                    raise ApiError('Выберите героя из списка')
                profile.lobby_hero = hero
            if 'server_date' in data:
                value = data['server_date']
                if not isinstance(value, str):
                    raise ApiError('Дата должна быть в формате ГГГГ-ММ-ДД')
                value = value.strip()
                if value not in ('', 'now'):
                    try:
                        parsed_date = date.fromisoformat(value)
                        if parsed_date.isoformat() != value:
                            raise ValueError()
                    except ValueError:
                        raise ApiError('Дата должна быть в формате ГГГГ-ММ-ДД') from None
                    # Event start is two days earlier (unsigned STU year >=2000),
                    # and CONFIG36602 holds a u32 Unix timestamp at UTC noon.
                    if not date(2000, 1, 3) <= parsed_date <= date(2106, 2, 6):
                        raise ApiError('Дата сервера должна быть от 2000-01-03 до 2106-02-06')
                profile.server_date = value
            if 'challenge' in data:
                challenge = data['challenge']
                if not isinstance(challenge, str) or (challenge and challenge not in self.lobby.items.challenges()):
                    raise ApiError('Испытание не найдено')
                profile.challenge = challenge
            save_profile(profile, account.path)
            account.profile = profile
            self.lobby.push_profile(account)
            return {'status': 'ok', 'profile': profile_snapshot(profile)}

    def add_boxes(self, data):
        kind = integer(data.get('type', 0), 'Тип контейнера')
        count = integer(data.get('count', 10), 'Количество контейнеров', 1, 100)
        if kind not in BOX_TYPE_NAMES:
            raise ApiError('Такого типа контейнера нет в каталоге')
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
            raise ApiError('Каталог магазина ещё не подключён', 503)
        return service.catalog(self.account(query.get('account')).profile,
                               q=query.get('q', ''), hero=query.get('hero', ''),
                               currency=query.get('currency', ''),
                               page=integer(query.get('page', 1), 'Страница', 1),
                               page_size=integer(query.get('page_size', 24), 'Размер страницы', 1, 100))

    def purchase(self, data):
        service = getattr(self.lobby, 'shop', None)
        if service is None:
            raise ApiError('Каталог магазина ещё не подключён', 503)
        try:
            guid = int(data.get('guid', ''), 0) if isinstance(data.get('guid'), str) else int(data['guid'])
        except (TypeError, ValueError, KeyError):
            raise ApiError('Некорректный предмет') from None
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
