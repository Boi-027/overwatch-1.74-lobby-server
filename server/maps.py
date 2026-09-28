"""
Overwatch 1.74 Authoritative Map Registry & Dynamic Background Rotator
----------------------------------------------------------------------
Generated directly from client CASC metadata (Overwatch 1.74.0.0.104319).
Covers all 70 official maps with verified Russian & English names,
game modes, and official 3D main menu panoramas.
"""

import random
from dataclasses import dataclass
from typing import Optional, List, Dict

@dataclass
class MapInfo:
    guid: int
    name_en: str
    name_ru: str
    mode: str
    is_background: bool = False

    @property
    def hex_guid(self) -> str:
        return f"0x{self.guid:016X}"

    @property
    def index(self) -> int:
        return self.guid & 0xFFFFFFFF

    def display_name(self) -> str:
        return f"{self.name_ru} / {self.name_en} ({self.mode})"

# All 70 Official Overwatch 1.74 Maps
MAP_REGISTRY: Dict[int, MapInfo] = {
    0x080000000000005B: MapInfo(0x080000000000005B, "Temple of Anubis", "Храм Анубиса", "Захват точек", is_background=False),
    0x08000000000000D4: MapInfo(0x08000000000000D4, "King's Row", "Кингс Роу", "Гибридный режим", is_background=False),
    0x0800000000000165: MapInfo(0x0800000000000165, "Hanamura", "Ханамура", "Захват точек", is_background=False),
    0x0800000000000184: MapInfo(0x0800000000000184, "Watchpoint: Gibraltar", "Пост наблюдения: Гибралтар", "Сопровождение", is_background=False),
    0x08000000000001D4: MapInfo(0x08000000000001D4, "Numbani", "Нумбани", "Гибридный режим", is_background=False),
    0x08000000000001DB: MapInfo(0x08000000000001DB, "Volskaya Industries", "КБ Вольской", "Захват точек", is_background=False),
    0x08000000000002AF: MapInfo(0x08000000000002AF, "Hollywood", "Голливуд", "Гибридный режим", is_background=False),
    0x08000000000002C3: MapInfo(0x08000000000002C3, "Dorado", "Дорадо", "Сопровождение", is_background=False),
    0x08000000000004B7: MapInfo(0x08000000000004B7, "Nepal", "Непал", "Контроль", is_background=False),
    0x08000000000005BB: MapInfo(0x08000000000005BB, "Route 66", "Шоссе 66", "Сопровождение", is_background=False),
    0x0800000000000662: MapInfo(0x0800000000000662, "Lijiang Tower", "Башня Лицзян", "Контроль", is_background=False),
    0x080000000000066D: MapInfo(0x080000000000066D, "Ilios", "Илиос", "Контроль", is_background=False),
    0x0800000000000688: MapInfo(0x0800000000000688, "Practice Range", "УЧЕБНЫЙ ПОЛИГОН", "Учебный полигон", is_background=True),
    0x080000000000068D: MapInfo(0x080000000000068D, "Eichenwalde", "Айхенвальд", "Гибридный режим", is_background=False),
    0x080000000000069E: MapInfo(0x080000000000069E, "Oasis", "Оазис", "Контроль", is_background=False),
    0x08000000000006AB: MapInfo(0x08000000000006AB, "Hollywood", "Голливуд", "Гибридный режим", is_background=False),
    0x08000000000006B1: MapInfo(0x08000000000006B1, "King's Row", "Кингс Роу", "Гибридный режим", is_background=False),
    0x08000000000006B3: MapInfo(0x08000000000006B3, "Estádio das Rãs", "«Эстадиу ди Ранс»", "Лусиобол", is_background=False),
    0x08000000000006B5: MapInfo(0x08000000000006B5, "Hanamura", "Ханамура", "Захват точек", is_background=False),
    0x08000000000006B7: MapInfo(0x08000000000006B7, "Lijiang Tower", "Башня Лицзян", "Контроль", is_background=False),
    0x08000000000006C9: MapInfo(0x08000000000006C9, "Junkenstein's Revenge", "Месть Крысенштейна", "Месть Крысенштейна", is_background=False),
    0x08000000000006D1: MapInfo(0x08000000000006D1, "Ecopoint: Antarctica", "Экостанция: Антарктика", "Ликвидация", is_background=False),
    0x08000000000006D3: MapInfo(0x08000000000006D3, "Horizon Lunar Colony", "Лунная колония «Горизонт»", "Захват точек", is_background=False),
    0x0800000000000705: MapInfo(0x0800000000000705, "Necropolis", "Некрополь", "Ликвидация", is_background=False),
    0x080000000000070C: MapInfo(0x080000000000070C, "Black Forest", "Черный лес", "Ликвидация", is_background=False),
    0x080000000000070D: MapInfo(0x080000000000070D, "Ecopoint: Antarctica", "Экостанция: Антарктика", "Ликвидация", is_background=False),
    0x0800000000000711: MapInfo(0x0800000000000711, "Lijiang Garden", "Сад Лицзян", "Захват флага", is_background=False),
    0x0800000000000712: MapInfo(0x0800000000000712, "Lijiang Night Market", "Ночной рынок Лицзян", "Захват флага", is_background=False),
    0x0800000000000717: MapInfo(0x0800000000000717, "Nepal Sanctum", "Непал: святилище", "Захват флага", is_background=False),
    0x080000000000071A: MapInfo(0x080000000000071A, "Lijiang Control Center", "Командный центр Лицзян", "Захват флага", is_background=False),
    0x080000000000071C: MapInfo(0x080000000000071C, "Castillo", "Кастильо", "Ликвидация", is_background=False),
    0x0800000000000736: MapInfo(0x0800000000000736, "Nepal Village", "Непал: деревня", "Захват флага", is_background=False),
    0x0800000000000738: MapInfo(0x0800000000000738, "Nepal Shrine", "Непал: алтарь", "Захват флага", is_background=False),
    0x080000000000073A: MapInfo(0x080000000000073A, "Ilios Well", "Илиос: колодец", "Захват флага", is_background=False),
    0x080000000000073D: MapInfo(0x080000000000073D, "Ilios Lighthouse", "Илиос: маяк", "Захват флага", is_background=False),
    0x080000000000073E: MapInfo(0x080000000000073E, "Ilios Ruins", "Илиос: развалины", "Захват флага", is_background=False),
    0x0800000000000744: MapInfo(0x0800000000000744, "Lijiang Control Center", "Лицзян: командный центр", "Захват флага", is_background=False),
    0x0800000000000745: MapInfo(0x0800000000000745, "Lijiang Garden", "Лицзян: сад", "Захват флага", is_background=False),
    0x0800000000000746: MapInfo(0x0800000000000746, "Lijiang Night Market", "Лицзян: ночной рынок", "Захват флага", is_background=False),
    0x080000000000074A: MapInfo(0x080000000000074A, "Oasis City Center", "Оазис: центр города", "Захват флага", is_background=False),
    0x080000000000074C: MapInfo(0x080000000000074C, "Oasis Gardens", "Оазис: сады", "Захват флага", is_background=False),
    0x080000000000074D: MapInfo(0x080000000000074D, "Oasis University", "Оазис: университет", "Захват флага", is_background=False),
    0x0800000000000756: MapInfo(0x0800000000000756, "Junkertown", "Джанкертаун", "Сопровождение", is_background=False),
    0x080000000000075E: MapInfo(0x080000000000075E, "Blizzard World", "Blizzard World", "Гибридный режим", is_background=False),
    0x0800000000000793: MapInfo(0x0800000000000793, "Sydney Harbour Arena Classic", "Классическая «Арена Сидней-Харбор»", "Лусиобол", is_background=False),
    0x08000000000007A1: MapInfo(0x08000000000007A1, "Ayutthaya", "Аюттайя", "Захват флага", is_background=False),
    0x08000000000007A4: MapInfo(0x08000000000007A4, "Château Guillard", "Шато-Гийяр", "Схватка", is_background=False),
    0x08000000000007E2: MapInfo(0x08000000000007E2, "Busan", "Пусан", "Контроль", is_background=False),
    0x08000000000007F4: MapInfo(0x08000000000007F4, "Eichenwalde", "Айхенвальд", "Гибридный режим", is_background=False),
    0x08000000000007F7: MapInfo(0x08000000000007F7, "Black Forest", "Черный лес", "Ликвидация", is_background=False),
    0x08000000000007FD: MapInfo(0x08000000000007FD, "Nepal Village (Winter)", "Непал: деревня (зима)", "Охота на йети", is_background=False),
    0x0800000000000836: MapInfo(0x0800000000000836, "Château Guillard", "Шато-Гийяр", "Схватка", is_background=False),
    0x0800000000000871: MapInfo(0x0800000000000871, "Rialto", "Риальто", "Сопровождение", is_background=False),
    0x0800000000000890: MapInfo(0x0800000000000890, "Petra", "Петра", "Схватка", is_background=False),
    0x0800000000000891: MapInfo(0x0800000000000891, "Paris", "Париж", "Захват точек", is_background=False),
    0x080000000000092A: MapInfo(0x080000000000092A, "Busan Stadium Classic", "Классический стадион Пусана", "Лусиобол", is_background=False),
    0x0800000000000A44: MapInfo(0x0800000000000A44, "Havana", "Гавана", "Сопровождение", is_background=False),
    0x0800000000000A5B: MapInfo(0x0800000000000A5B, "Blizzard World", "Blizzard World", "Гибридный режим", is_background=False),
    0x0800000000000A7A: MapInfo(0x0800000000000A7A, "Busan Sanctuary", "Святилище Пусана", "Захват флага", is_background=False),
    0x0800000000000A86: MapInfo(0x0800000000000A86, "Busan Downtown", "Центр Пусана", "Захват флага", is_background=False),
    0x0800000000000C40: MapInfo(0x0800000000000C40, "Workshop Island", "Мастерская: остров", "Ликвидация", is_background=False),
    0x0800000000000C44: MapInfo(0x0800000000000C44, "Workshop Expanse", "Мастерская: поле", "Ликвидация", is_background=False),
    0x0800000000000C48: MapInfo(0x0800000000000C48, "Workshop Chamber", "Мастерская: комната", "Ликвидация", is_background=False),
    0x0800000000000CD0: MapInfo(0x0800000000000CD0, "Workshop Expanse (Night)", "Мастерская: поле (ночь)", "Ликвидация", is_background=False),
    0x0800000000000CD1: MapInfo(0x0800000000000CD1, "Workshop Island (Night)", "Мастерская: остров (ночь)", "Ликвидация", is_background=False),
    0x0800000000000CD3: MapInfo(0x0800000000000CD3, "Kanezaka", "Канедзака", "Схватка", is_background=False),
    0x0800000000000CD7: MapInfo(0x0800000000000CD7, "Malevento", "Малевенто", "Схватка", is_background=False),
    0x0800000000000CDC: MapInfo(0x0800000000000CDC, "Busan Stadium", "Стадион Пусана", "Лусиобол", is_background=False),
    0x0800000000000CEB: MapInfo(0x0800000000000CEB, "Sydney Harbour Arena", "«Арена Сидней-Харбор»", "Лусиобол", is_background=False),
    0x0800000000000D4C: MapInfo(0x0800000000000D4C, "Workshop Green Screen", "Мастерская: зеленый экран", "Ликвидация", is_background=False),
}

# Official 3D Main Menu Panorama Scenes (Menu/* and Practice Range)
BACKGROUND_MAPS: Dict[int, str] = {
    0x0800000000000688: "Учебный полигон / Practice Range (Default)",
    0x0800000000000664: "Menu/Hanamura / Menu/Hanamura",
    0x0800000000000675: "Menu/Gibraltar / Menu/Gibraltar",
    0x0800000000000676: "Menu/Kings Row / Menu/Kings Row",
    0x0800000000000677: "Menu/Volskaya / Menu/Volskaya",
    0x0800000000000678: "Menu/Hollywood / Menu/Hollywood",
    0x08000000000006E0: "Menu/Eichenwalde (Halloween) / Menu/Eichenwalde (Halloween)",
    0x0800000000000700: "Menu/Kings Row (Winter) (2019) / Menu/Kings Row (Winter) (2019)",
    0x0800000000000710: "Menu/Busan (Lunar) / Menu/Busan (Lunar)",
    0x0800000000000715: "Menu/Lijiang (Lunar) / Menu/Lijiang (Lunar)",
    0x0800000000000764: "Menu/Kings Row (Archives) / Menu/Kings Row (Archives)",
    0x0800000000000794: "Menu/Horizon Lunar Colony / Menu/Horizon Lunar Colony",
    0x0800000000000801: "Menu/Junkertown / Menu/Junkertown",
    0x0800000000000817: "Menu/World Cup / Menu/World Cup",
    0x080000000000085F: "Menu/Black Forest (Winter) (Day) / Menu/Black Forest (Winter) (Day)",
    0x0800000000000862: "Menu/OWL / Menu/OWL",
    0x0800000000000864: "Menu/OWL / Menu/OWL",
    0x08000000000008B4: "Menu/Rialto (Archives) / Menu/Rialto (Archives)",
    0x08000000000008BE: "Menu/Rialto / Menu/Rialto",
    0x0800000000000A13: "Menu/OWL / Menu/OWL",
    0x0800000000000A5A: "Menu/Chateau (Halloween) / Menu/Chateau (Halloween)",
    0x0800000000000A9E: "Menu/Blizzard World (Winter) / Menu/Blizzard World (Winter)",
    0x0800000000000AF0: "Menu/Paris / Menu/Paris",
    0x0800000000000B2D: "Menu/OWL All Stars (2020) / Menu/OWL All Stars (2020)",
    0x0800000000000B55: "Menu/Havana / Menu/Havana",
    0x0800000000000B56: "Menu/Havana (Archives) / Menu/Havana (Archives)",
    0x0800000000000BCE: "Menu/Summer Games (2020) / Menu/Summer Games (2020)",
    0x0800000000000C04: "Menu/Eichenwalde (Alt) / Menu/Eichenwalde (Alt)",
    0x0800000000000C46: "Menu/Temple of Anubis (Alt) / Menu/Temple of Anubis (Alt)",
    0x0800000000000C61: "Menu/Black Forest (Winter) (Night) / Menu/Black Forest (Winter) (Night)",
    0x0800000000000C83: "Menu/OWL / Menu/OWL",
    0x0800000000000C8F: "Menu/Anniversary / Menu/Anniversary",
    0x0800000000000CAC: "Menu/Route 66 (Alt) / Menu/Route 66 (Alt)",
    0x0800000000000CCF: "Menu/Anniversary / Menu/Anniversary",
    0x0800000000000CE0: "Menu/Anniversary / Menu/Anniversary",
    0x0800000000000CE6: "Menu/Paris (Alt) / Menu/Paris (Alt)",
    0x0800000000000CFE: "Menu/Sydney (Lucioball) / Menu/Sydney (Lucioball)",
    0x0800000000000CFF: "Menu/Busan (Lucioball) / Menu/Busan (Lucioball)",
    0x0800000000000D0A: "Menu/OWL / Menu/OWL",
    0x0800000000000D2C: "Menu/Tracer Comic Challenge / Menu/Tracer Comic Challenge",
    0x0800000000000D2D: "Menu/OWL Grand Finals (2020) / Menu/OWL Grand Finals (2020)",
    0x0800000000000D67: "Menu/Symmetra Challenge / Menu/Symmetra Challenge",
    0x0800000000000D77: "Menu/Kings Row (Winter) (2020) / Menu/Kings Row (Winter) (2020)",
    0x0800000000000D7A: "Menu/Antarctica (Winter) / Menu/Antarctica (Winter)",
}

_CYCLE_INDEX = 0

def select_background_map(mode: str = "random") -> int:
    """
    Selects a 3D panorama background map for the main menu.
    Modes:
      - 'random': Random selection from all available 3D scenes.
      - 'cycle': Sequentially advances to the next panorama map.
      - 'default': Practice Range (0x0800000000000688).
      - Specific Map Name or Hex GUID.
    """
    global _CYCLE_INDEX
    pool = list(BACKGROUND_MAPS.keys())
    if not pool:
        return 0x0800000000000688

    mode_str = str(mode).strip().lower()

    if mode_str == "default":
        return 0x0800000000000688

    if mode_str == "cycle":
        selected = pool[_CYCLE_INDEX % len(pool)]
        _CYCLE_INDEX += 1
        return selected

    if mode_str.startswith("0x"):
        try:
            target_guid = int(mode_str, 16)
            if target_guid in BACKGROUND_MAPS or target_guid in MAP_REGISTRY:
                return target_guid
        except ValueError:
            pass

    for guid, name in BACKGROUND_MAPS.items():
        if mode_str in name.lower():
            return guid

    for guid, info in MAP_REGISTRY.items():
        if mode_str in info.name_en.lower() or mode_str in info.name_ru.lower():
            return guid

    # Default to random
    return random.choice(pool)

def get_map_by_guid(guid: int) -> Optional[MapInfo]:
    return MAP_REGISTRY.get(guid)

def resolve_map(query) -> Optional[MapInfo]:
    """Resolves a map by GUID int, hex string, or name."""
    if isinstance(query, int):
        return MAP_REGISTRY.get(query)
    if isinstance(query, str):
        q = query.strip()
        if q.startswith("0x") or q.startswith("0X"):
            try:
                val = int(q, 16)
                if val in MAP_REGISTRY:
                    return MAP_REGISTRY[val]
            except ValueError:
                pass
        for m in MAP_REGISTRY.values():
            if q.lower() in m.name_en.lower() or q.lower() in m.name_ru.lower():
                return m
    return None

def get_all_maps_list() -> List[int]:
    return list(MAP_REGISTRY.keys())
