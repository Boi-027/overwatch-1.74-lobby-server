#!/usr/bin/env python3
"""
Write data/game_heroes_174.json: each playable hero's GUID, name and gameplay entity (the 003 body
the game server creates for it).

Reads DataTool's STU dumps of the heroes (type 075, `extract-stu-type <out> 075 --xml`) and the
names in data/extracted_heroes.json.

    py tools/extract_game_heroes.py D:\\OW174_stu_xml\\075
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ENTITY_REF = re.compile(r'hml:name="m_gameplayEntity" GUID="([0-9A-F]+)\.003"')


def guid(index: int, file_type: int) -> int:
    """A GUID from its index and file type: the type sits bit-reversed at bits 48..59."""
    reversed_type = int(f"{file_type - 1:012b}"[::-1], 2)
    return reversed_type << 48 | index


def main(argv: list[str]) -> None:
    folder = Path(argv[1]) if len(argv) > 1 else Path(r"D:\OW174_stu_xml\075")
    heroes = json.loads((DATA_DIR / "extracted_heroes.json").read_text(encoding="utf-8"))
    table = {}
    for key, hero in heroes.items():
        if not hero.get("IsHero"):
            continue
        dump = folder / f"{key}.xml"
        match = ENTITY_REF.search(dump.read_text(encoding="utf-8")) if dump.is_file() else None
        if match is None:
            print(f"skipped {hero['Name']}: no gameplay entity in {dump}")
            continue
        hero_guid = guid(int(key.split(".")[0], 16), 0x75)
        table[f"0x{hero_guid:016X}"] = {
            "name": hero["Name"],
            "class": hero.get("Class", ""),
            "body": f"0x{guid(int(match.group(1), 16), 0x3):016X}",
        }
    out = DATA_DIR / "game_heroes_174.json"
    out.write_text(
        json.dumps(dict(sorted(table.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(table)} heroes -> {out}")


if __name__ == "__main__":
    main(sys.argv)
