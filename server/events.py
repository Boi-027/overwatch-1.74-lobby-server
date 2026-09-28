"""Russian picker metadata, with scene support separate from event rewards.

Evidence: data/extracted_events_174.json and docs/STATE.md.
Unavailable entries intentionally have no EVENT_PRESETS record. 'verified'
refers to previously reported working scenes, not a new test of these changes.
"""


def _event(id, label, description, category, scene_status='unverified', scene_note=''):
    return dict(id=id, label=label, description=description, category=category,
                scene_status=scene_status, scene_note=scene_note)


EVENT_CATALOG = [
    _event('goodbye', 'Прощание с Overwatch', 'Общая сцена с героями первого Overwatch.', 'special',
           'verified', 'Работала в предыдущих проверках.'),
    _event('lunar', 'Лунный Новый год', 'Год Тигра: праздничные карты и контейнеры.', 'seasonal',
           'verified', 'Работала в предыдущих проверках.'),
    _event('halloween', 'Ужасы на Хеллоуин', 'Праздничный Айхенвальд и тематические контейнеры.', 'seasonal',
           'unverified', 'Сцена есть в клиенте; переключение нужно проверить в игре.'),
    _event('winter', 'Зимняя сказка', 'Зимние сцены и праздничные контейнеры.', 'seasonal',
           'verified', 'Работала в предыдущих проверках.'),
    _event('anniversary', 'Годовщина: ремикс 3', 'Сцена с героями в обликах годовщины.', 'seasonal',
           'verified', 'Переключение сцены подтверждено в игре после обновления.'),
    _event('anniversary_remix_1', 'Годовщина: ремикс 1', 'Ранняя версия события с той же сценой годовщины.', 'seasonal',
           'verified', 'Переключение сцены подтверждено в игре после обновления.'),
    _event('anniversary_remix_2', 'Годовщина: ремикс 2', 'Вторая версия события с той же сценой годовщины.', 'seasonal',
           'verified', 'Переключение сцены подтверждено в игре после обновления.'),
    _event('summer', 'Летние игры', 'Событие «Летние игры» и тематические контейнеры.', 'seasonal',
           'limited', 'В этой версии клиента событие не выбирает отдельную летнюю сцену.'),
    _event('archives', 'Архивы', 'Событие «Архивы» и тематические контейнеры.', 'seasonal',
           'limited', 'В этой версии клиента событие не выбирает отдельную сцену «Архивов».'),
    _event('reaper', 'Испытание Жнеца', 'Сцена испытания «Кодекс насилия».', 'special',
           'verified', 'Работала в предыдущих проверках.'),
    _event('cassidy', 'Испытание Кэссиди', 'Сцена испытания «Новая кровь».', 'special',
           'verified', 'Работала в предыдущих проверках.'),
    _event('malevento', 'Малевенто', 'Главное меню с видом Малевенто.', 'special',
           'verified', 'Работала в предыдущих проверках.'),
    _event('owl', 'Лига Overwatch: Гэндзи', 'Сцена Лиги Overwatch с Гэндзи.', 'esports',
           'verified', 'Работала в предыдущих проверках.'),
    _event('contenders', 'Overwatch Contenders', 'Киберспортивное событие с наградами за вход.', 'esports',
           'limited', 'Награды доступны, но отдельная сцена для этого события в клиенте не назначена.'),
    _event('tracer_comic', 'Трейсер: комикс', 'Историческая сцена Трейсер с панелью комикса.', 'special',
           'unavailable', 'В версии 1.74 прежняя сцена заменена сценой Жнеца. Включить оригинал через настройки события нельзя.'),
]
