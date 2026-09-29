"""Make data/arcade_cards_174.json from an STU dump of the game's Arcade cards (type 0C7).

1. Dump the cards with DataTool (OWLib) from an Overwatch 1.74 install:

    DataTool.exe <game> extract-stu-type <dump>\\0C7 0C7 --xml

2. Run this script on the dump folder (the one that holds 0C7):

    py tools/extract_arcade_cards.py <dump>

A card in message 39802 is the wire form of its 0C7 asset: the identifier, the display entry, the
reward unlocks and seven of its eleven flags come straight from the asset. The server adds the rest
(how long a card stays, whether it is permanent).
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "arcade_cards_174.json"
# Card flags 13-19 of message 39802, in wire order, and the asset fields they come from.
ASSET_FLAGS = (
    "m_157AF68C",
    "m_5E1EE04A",
    "m_D66FABE0",
    "m_7F08E444",
    "m_313C5ED5",
    "m_F011ED7A",
    "m_8F862EC7",
)
REF = re.compile(r'hml:name="(\w+)" GUID="([0-9A-F]{12}\.[0-9A-F]{3})"')
REWARDS = re.compile(r'dragon:name="m_B1449DF7">(.*?)</dragon:array>', re.S)
GUID = re.compile(r'GUID="([0-9A-F]{12}\.[0-9A-F]{3})"')


def guid_key(text: str) -> int:
    """A DataTool GUID like "000000002F58.01C" as the 64-bit key the game sends."""
    index, kind = text.split(".")
    type_bits = int(f"{int(kind, 16) - 1:012b}"[::-1], 2)  # the type is stored minus one, bits reversed
    return type_bits << 48 | int(index, 16)


def card(xml: str) -> dict:
    refs = dict(REF.findall(xml))
    rewards = REWARDS.search(xml)
    return {
        "identifier": f"0x{guid_key(refs['m_0503A846']):016X}" if "m_0503A846" in refs else "0x0",
        "display": f"0x{guid_key(refs['m_A848F2C7']):016X}" if "m_A848F2C7" in refs else "0x0",
        "rewards": [f"0x{guid_key(g):016X}" for g in GUID.findall(rewards.group(1))] if rewards else [],
        "flags": [re.search(rf'{name}="(\d+)"', xml).group(1) == "1" for name in ASSET_FLAGS],
        # Role queue cards list their role slots (the role in m_1F753B44, 2 players each).
        "roles": 'hml:name="m_1F753B44"' in xml,
        # The priority pass pool of a role queue (the client reads it at card +0x250).
        "passes": f"0x{guid_key(refs['m_DD50F6A1']):016X}" if "m_DD50F6A1" in refs else "0x0",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dump", type=Path, help="the STU dump folder that holds 0C7")
    args = parser.parse_args()
    cards = {}
    for path in sorted((args.dump / "0C7").glob("*.0C7.xml")):
        index = int(path.name.split(".")[0], 16)
        cards[f"0x{index:X}"] = card(path.read_text(encoding="utf-8"))
    OUT.write_text(json.dumps(cards, indent=1) + "\n", encoding="utf-8")
    print(f"{len(cards)} cards -> {OUT}")


if __name__ == "__main__":
    main()
