"""Make data/game_queues_174.json: what each queue card (0C7) needs to build a match.

1. Dump the STUs and the English strings with DataTool (OWLib) from an Overwatch 1.74 install:

    DataTool.exe <game> extract-stu-type <dump> --xml
    DataTool.exe <game> dump-strings --json --out=<dump>\\07C\\strings_enUS.json --language=enUS

2. Run this script on the dump folder:

    py tools/extract_game_queues.py <dump>

It reads the cards (0C7), their rulesets (0C0), game modes (0C5), map catalogs (039), map headers
(09F), card titles (0D9), the Arcade menu (0EE nodes and their 0CC tags) and the strings. For each
card it writes the name, the rulesets (game mode, players per team, free-for-all), the role slots,
the maps and the largest party. The client ships only 4 of the 43 catalogs the cards name. For the
other cards the maps come from the retail capture (data/retail_templates.json: message 36300 lists
each competitive card with its maps) or else from the map headers that support the card's modes,
narrowed to the maps its Arcade tags name. "maps_source" says which.
"""

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "game_queues_174.json"
CAPTURE = ROOT / "data" / "retail_templates.json"
NS = "{https://yretenai.com/dragonml/v1}"
INDEX = 0xFFFFFFFFFFFF
TEAMS = {"TeamBlue": "blue", "TeamRed": "red", "FFA": "ffa"}
AI_LEVELS = {"x65913A19": 0, "x78D9134A": 1, "x5F5E3636": 2, "xB54E4F48": 3}
# Cards the client picks in code, with no name in the data (0x7FF78934DC70, 0x7FF78934AA51).
CODE_NAMES = {
    0x6D: "Quick Play (no role queue)",
    0xED: "Quick Play",
    0x70: "Play vs AI (AI level 1)",
    0x71: "Play vs AI (AI level 2)",
    0x72: "Play vs AI (AI level 3)",
}


def guid(text: str) -> int:
    """A DataTool GUID like "0000000000ED.0C7" as the 64-bit key the game sends."""
    index, kind = text.split(".")
    type_bits = int(f"{int(kind, 16) - 1:012b}"[::-1], 2)  # the type is stored minus one, bits reversed
    return type_bits << 48 | int(index, 16)


def key(text: str) -> str:
    return f"0x{guid(text):016X}"


def value(element: ET.Element):
    """An STU element as plain data: a reference is its GUID, an array a list, an object a dict."""
    tag = element.tag.split("}")[-1]
    if tag == "ref":
        return element.get("GUID")
    if tag == "array":
        return [value(child) for child in element]
    if len(element) == 0 and element.text and element.text.strip():
        return element.text.strip()
    data = {
        name: None if text == "{null}" else text for name, text in element.attrib.items() if name[0] != "{"
    }
    for child in element:
        data[child.get(NS + "name")] = value(child)
    return data


def load(dump: Path, kind: str) -> dict[str, dict]:
    return {
        path.name[:-4]: value(ET.parse(path).getroot())
        for path in sorted((dump / kind).glob(f"*.{kind}.xml"))
    }


def scene(header: dict) -> str:
    """A map header's scene path, like Maps\\China\\China - Garden - CTF."""
    return (header.get("m_mapName") or {}).get("Value", "")


def folder(header: dict) -> str:
    """The folder of a map's scene, like "China"."""
    parts = re.split(r"\\+", scene(header))
    return parts[1] if len(parts) > 2 else ""


class Queues:
    def __init__(self, dump: Path) -> None:
        strings = json.loads((dump / "07C" / "strings_enUS.json").read_text(encoding="utf-8"))
        self.strings = {name: entry["Value"] for name, entry in strings.items()}
        self.cards = load(dump, "0C7")
        self.rulesets = load(dump, "0C0")
        self.modes = load(dump, "0C5")
        self.catalogs = load(dump, "039")
        self.headers = load(dump, "09F")
        self.titles = load(dump, "0D9")
        self.menu_names, self.menu_tags = self._menu(load(dump, "0EE"), load(dump, "0CC"))
        self.captured = self._captured()
        self.variant_of = self._variants()
        # The map names each catalog's cards show in the Arcade ("MAPS: Hanamura, King's Row").
        self.tag_maps = {}
        for name, card in self.cards.items():
            for tag in self.menu_tags.get(name, []):
                if re.match(r"MAPS?: ", tag) and tag != "MAP: Random":
                    names = re.split(r",\s*(?:and\s+)?", tag.split(": ", 1)[1])
                    self.tag_maps.setdefault(card.get("m_catalog"), set()).update(
                        n.strip().lower() for n in names
                    )

    def text(self, reference: str | None) -> str | None:
        return self.strings.get(reference) if reference else None

    def _menu(self, nodes: dict, tags: dict) -> tuple[dict, dict]:
        """Each card's name and tags in the Arcade menu. A node names a card (m_5DC61E59) and lists tags
        (0CC, m_5797DE13); a group node (m_children) passes its tags to children that have none, and its
        name to them too ("Junkenstein's Revenge: Normal")."""
        parent = {child: name for name, node in nodes.items() for child in node.get("m_children") or []}

        def own_tags(name: str) -> list[str]:
            found = [
                self.text(tags[t].get("m_name")) for t in nodes[name].get("m_5797DE13") or [] if t in tags
            ]
            return [tag for tag in found if tag]

        names, card_tags = {}, {}
        for name, node in nodes.items():
            card = node.get("m_5DC61E59")
            if not card or card in names:
                continue
            title, found, up = self.text(node.get("m_name")) or "", own_tags(name), parent.get(name)
            if not found and up:
                title = f"{self.text(nodes[up].get('m_name'))}: {title.title()}"
            while not found and up:
                found, up = own_tags(up), parent.get(up)
            names[card], card_tags[card] = title, found
        return names, card_tags

    def _captured(self) -> dict[str, list[int]]:
        """The maps of each catalog the retail capture shows: message 36300 lists the competitive cards
        (+0x98), each with its card (+0x18) and maps (+0xD8)."""
        templates = json.loads(CAPTURE.read_text(encoding="utf-8"))
        ranked = next(message for _, number, message in templates["server"] if number == 36300)
        maps = {}
        for entry in ranked["+0x98"]:
            card = self.cards.get(f"{entry['+0x18'] & INDEX:012X}.0C7")
            if card and card.get("m_catalog"):
                maps[card["m_catalog"]] = entry["+0xD8"]
        return maps

    def _variants(self) -> dict[str, set[str]]:
        """Event versions of maps (Winter, Halloween, Lunar New Year) and the headers they replace."""
        variants = {}
        for name, header in self.headers.items():
            if header.get("m_baseMap"):
                variants.setdefault(name, set()).add(header["m_baseMap"].replace(".002", ".09F"))
            for override in header.get("m_celebrationOverrides") or []:
                variants.setdefault(override["m_map"].replace(".002", ".09F"), set()).add(name)
        return variants

    def replaces(self, name: str, mode: str) -> bool:
        """Whether a header is the event version of a map that supports the mode itself."""
        bases = [self.headers[base] for base in self.variant_of.get(name, ()) if base in self.headers]
        return any(mode in (base.get("m_supportedGamemodes") or []) for base in bases)

    def card_name(self, name: str, card: dict) -> str | None:
        index = guid(name) & INDEX
        title = self.text((self.titles.get(card.get("m_A848F2C7")) or {}).get("m_name"))
        ranked = self.text((card.get("m_2226CBD8") or {}).get("m_1E4E5957"))
        found = CODE_NAMES.get(index)
        if not found and title and int(card["m_DF9B21EF"]):  # a season or year: the title names it
            found = title
        found = found or self.menu_names.get(name) or title
        if not found and ranked:
            found = ranked if ranked.startswith("Competitive") else f"Competitive {ranked}"
        if not found and len(card.get("m_rulesets") or []) == 1:
            found = self.text(self.rulesets[card["m_rulesets"][0]]["m_gamemode"].get("m_description"))
        if not found:
            return key(card["m_A848F2C7"]) if card.get("m_A848F2C7") else None
        return re.sub(r"\s*<[^>]*>", "", found)

    def ruleset(self, name: str) -> dict:
        """A ruleset's game mode and players per team. The mode gives each team a default size
        (m_170AA4B8); the ruleset overrides it (m_341EF5FA), for one team or all, 0 keeping it."""
        game = self.rulesets[name]["m_gamemode"]
        mode = self.modes[game["m_gamemode"]]
        sizes = {
            TEAMS.get(team["m_team"], team["m_team"]): int(team["m_170AA4B8"]) for team in mode["m_teams"]
        }
        entries = game.get("m_teams") or []
        for entry in sorted(entries, key=lambda entry: "m_team" in entry["m_team"]):  # all teams first
            size, team = int(entry["m_341EF5FA"]), entry["m_team"].get("m_team")
            for side in [TEAMS.get(team, team)] if team else list(sizes):
                if size:
                    sizes[side] = size
        free_for_all = "ffa" in sizes
        playing = {side: size for side, size in sizes.items() if size and side != "ffa"}
        return {
            "ruleset": key(name),
            "name": self.text(game.get("m_description")),
            "mode": key(game["m_gamemode"]),
            "mode_name": self.text(mode.get("m_displayName")),
            "free_for_all": free_for_all,
            "teams": len(playing),
            "team_sizes": playing,
            "players": sizes["ffa"] if free_for_all else sum(playing.values()),
        }

    def maps(self, card: dict, modes: list[str]) -> tuple[str, list[dict]]:
        catalog = card["m_catalog"]
        if catalog in self.catalogs:
            source, found = "catalog", self.catalogs[catalog].get("m_mapGUIDs") or []
        elif catalog in self.captured:
            source, found = "retail capture", self.captured[catalog]
        else:
            source, found = self.derived(catalog, modes)
        maps = []
        for map_guid in found:
            header = self.headers[f"{int(map_guid) & INDEX:012X}.09F"]
            maps.append(
                {
                    "map": f"0x{int(map_guid):016X}",
                    "name": self.text(header.get("m_displayName")),
                    "modes": [
                        key(mode) for mode in modes if mode in (header.get("m_supportedGamemodes") or [])
                    ],
                }
            )
        return source, maps

    def derived(self, catalog: str, modes: list[str]) -> tuple[str, list[int]]:
        """Maps whose header supports one of the modes, without menu scenes (Lobby), Workshop maps and
        event versions of a map that supports the mode itself. A PvE mode takes PvE maps if it has any.
        When the catalog's cards name maps in their Arcade tags, only those are kept; a name that is
        no header's ("London", "LiJiang Tower") keeps the maps in that map's folder."""
        found = []
        for mode in modes:
            headers = [
                (name, header)
                for name, header in self.headers.items()
                if mode in (header.get("m_supportedGamemodes") or [])
                and "lobby" not in scene(header).lower()
                and folder(header) != "Workshop"
                and not self.replaces(name, mode)
            ]
            if self.modes[mode]["m_gameModeType"] == "PVE" and any(
                h["m_mapType"] == "PVE" for _, h in headers
            ):
                headers = [(name, header) for name, header in headers if header["m_mapType"] == "PVE"]
            found += [name for name, _ in headers if name not in found]
        names = self.tag_maps.get(catalog)
        if not names:
            return "game modes", sorted(guid(name.replace(".09F", ".002")) for name in found)
        display = {name: (self.text(self.headers[name].get("m_displayName")) or "").lower() for name in found}
        kept = [name for name in found if display[name] in names]
        for missing in names - set(display.values()):
            folders = {
                folder(header)
                for name, header in self.headers.items()
                if (self.text(header.get("m_displayName")) or "").lower() == missing
                or folder(header).lower() == missing
            }
            kept += [name for name in found if folder(self.headers[name]) in folders and name not in kept]
        return "arcade tags", sorted(guid(name.replace(".09F", ".002")) for name in kept or found)

    def card(self, name: str, card: dict) -> dict:
        rulesets = [self.ruleset(ruleset) for ruleset in card["m_rulesets"]]
        modes = list(dict.fromkeys(self.rulesets[r]["m_gamemode"]["m_gamemode"] for r in card["m_rulesets"]))
        ai_teams = {}
        for rule in card.get("m_7FB46D96") or []:
            level = AI_LEVELS[rule["m_97CFD9DA"]]
            team = rule["m_team"].get("m_team")
            if level:
                ai_teams[TEAMS.get(team, team or "all")] = level
        # No card field limits a party. It has to fit in one team, in every ruleset; in free-for-all
        # there is no team to hold it.
        max_party = min(
            1
            if ruleset["free_for_all"]
            else max((n for side, n in ruleset["team_sizes"].items() if side not in ai_teams), default=0)
            for ruleset in rulesets
        )
        source, maps = self.maps(card, modes)
        return {
            "name": self.card_name(name, card),
            "competitive": card["m_157AF68C"] == "1",
            "groups_only": card["m_E566638A"] == "1",
            "parent": key(card["m_2593569C"]) if card.get("m_2593569C") else None,
            "queue_with": key(card["m_679737B3"]) if card.get("m_679737B3") else None,
            "rulesets": rulesets,
            "ai_teams": ai_teams,
            "roles": {
                key(slot["m_1F753B44"]): int(slot["m_AC4BF158"]) for slot in card.get("m_66C923AF") or []
            },
            "max_party": max_party,
            "catalog": key(card["m_catalog"]),
            "maps_source": source,
            "maps": maps,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dump", type=Path, help="the STU dump folder that holds 0C7, 0C0, 0C5, 039 and 07C")
    args = parser.parse_args()
    queues = Queues(args.dump)
    cards, skipped = {}, {}
    for name, card in queues.cards.items():
        if not card.get("m_rulesets"):
            skipped[key(name)] = f"{queues.card_name(name, card)}: no rulesets, so no game to start"
            continue
        cards[key(name)] = queues.card(name, card)
    # The event version of each map: the celebration (the event kind in catalog/events.py) and its map.
    variants = {}
    for header in queues.headers.values():
        if "lobby" in scene(header).lower():
            continue
        for override in header.get("m_celebrationOverrides") or []:
            variants.setdefault(key(header["m_map"]), []).append(
                {"celebration": key(override["m_celebrationType"]), "map": key(override["m_map"])}
            )
    table = {"cards": cards, "skipped": skipped, "variants": variants}
    OUT.write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cards)} cards, {len(skipped)} skipped -> {OUT}")


if __name__ == "__main__":
    main()
