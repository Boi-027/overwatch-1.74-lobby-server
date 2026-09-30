"""Heroes and maps the game server knows."""

import json
import math
import re
from dataclasses import dataclass
from functools import cache

from ow174.paths import DATA_DIR

HEROES_PATH = DATA_DIR / "game_heroes_174.json"
MAPS_PATH = DATA_DIR / "game_maps_174.json"  # tools/extract_game_maps.py
SOLDIER = 0x02E000000000006E
PRACTICE_RANGE_MODE = 0x0230000000000018
# Modes whose loading screen waits for a state of the mode's own script (m_0FC17230 = 1 in the
# 0C5 asset; the loader's gate at 0x7FF789A271E0): a server has to run that script first. Mei's
# Snowball Offensive, two PvE modes, Retribution and Storm Rising.
SCRIPTED_LOADING_MODES = {
    0x0230000000000008,
    0x023000000000000F,
    0x023000000000001A,
    0x0230000000000025,
    0x0230000000000043,
}


@dataclass(frozen=True)
class Hero:
    guid: int  # the 075 hero
    name: str
    role: str
    body: int  # the 003 entity the client plays as (the hero's m_gameplayEntity)


@cache
def heroes() -> dict[int, Hero]:
    table = json.loads(HEROES_PATH.read_text(encoding="utf-8"))
    return {
        int(key, 16): Hero(int(key, 16), entry["name"], entry["class"], int(entry["body"], 16))
        for key, entry in table.items()
    }


def _plain(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower().replace("ö", "o").replace("ú", "u"))


def find_hero(text: str) -> Hero | None:
    """A hero by its name ("soldier", "Torbjörn", "dva") or its GUID."""
    wanted = _plain(text)
    if not wanted:
        return None
    by_name = {_plain(hero.name): hero for hero in heroes().values()}
    if wanted in by_name:
        return by_name[wanted]
    starts = [hero for name, hero in by_name.items() if name.startswith(wanted)]
    if len(starts) == 1:
        return starts[0]
    try:
        return heroes().get(int(text, 16))
    except ValueError:
        return None


@dataclass(frozen=True)
class Spawn:
    team: int | None  # None: any team (free for all)
    position: tuple[float, float, float]
    yaw: float  # degrees


@dataclass(frozen=True)
class GameMap:
    name: str
    map_guid: int  # 002; the type bits reversed: 0x0800...
    mode_guid: int  # the 0C5 game mode it is played with
    placeables: tuple[int, ...]  # map entities the server creates; the loading screen waits for them
    spawns: tuple[Spawn, ...]
    mode_name: str = ""


# Practice Range (0688.002 with 0018.0C5): its four server-owned placeables, the ones ProCore read
# in the running client and the rule in tools/extract_game_maps.py gives, and the spawn point.
PRACTICE_RANGE = GameMap(
    name="Practice Range",
    map_guid=0x0800000000000688,
    mode_guid=PRACTICE_RANGE_MODE,
    placeables=(0x88, 0x22E, 0x234, 0x289),
    spawns=(Spawn(None, (54.56507, 1.0, 42.14788), -170.3434),),
    mode_name="Practice Range",
)


@cache
def _map_entries() -> dict[int, dict]:
    if not MAPS_PATH.is_file():
        return {}
    return {int(key, 16): entry for key, entry in json.loads(MAPS_PATH.read_text(encoding="utf-8")).items()}


def _first_round_spawns(entry: dict, free_for_all: bool) -> tuple[Spawn, ...]:
    """Each team's spawn points of the first objective (team 1 attacks it, team 0 defends), or of any
    objective when the map names none; the free-for-all points in a free-for-all mode."""
    spawns = entry.get("spawns") or []
    if free_for_all:
        chosen = [spawn for spawn in spawns if spawn.get("ffa") or spawn.get("team") is None]
    else:
        chosen = []
        for team in (0, 1):
            mine = [spawn for spawn in spawns if spawn.get("team") == team and not spawn.get("ffa")]
            first = [spawn for spawn in mine if any(goal[0] == 0 for goal in spawn.get("objectives") or [])]
            chosen += first or mine
    return tuple(
        Spawn(spawn.get("team"), tuple(spawn["position"]), float(spawn.get("yaw", 0.0))) for spawn in chosen
    )


def game_map(
    map_guid: int, mode_guid: int, mode_name: str = "", free_for_all: bool = False
) -> GameMap | None:
    """The map as it is played in that mode, or None when the server can't host it: no data, a mode
    the map doesn't support, no spawn points, or a mode whose loading needs its own script."""
    if map_guid == PRACTICE_RANGE.map_guid:
        return PRACTICE_RANGE
    entry = _map_entries().get(map_guid)
    if entry is None or mode_guid in SCRIPTED_LOADING_MODES:
        return None
    if mode_guid not in {int(mode, 16) for mode in entry.get("modes", [])}:
        return None
    by_mode = {int(mode, 16): indexes for mode, indexes in (entry.get("placeables_by_mode") or {}).items()}
    placeables = by_mode.get(mode_guid, entry.get("placeables", []))
    spawns = _first_round_spawns(entry, free_for_all)
    if not spawns:
        return None
    return GameMap(entry["name"], map_guid, mode_guid, tuple(placeables), spawns, mode_name)


SPAWN_SPACING = 1.5  # metres between players who get the same spawn point


def spawn_point(game_map: GameMap, team: int, number: int) -> tuple[tuple[float, float, float], float]:
    """Where the number-th player of a team starts: that team's spawn points in turn, and players who
    share a point stand side by side along its right-hand axis (-cos yaw, 0, sin yaw)."""
    points = [spawn for spawn in game_map.spawns if spawn.team in (team, None)] or list(game_map.spawns)
    spawn = points[number % len(points)]
    row = number // len(points)
    side = (row + 1) // 2 * (1 if row % 2 else -1) * SPAWN_SPACING
    yaw = math.radians(spawn.yaw)
    x, y, z = spawn.position
    return (x - math.cos(yaw) * side, y, z + math.sin(yaw) * side), spawn.yaw
