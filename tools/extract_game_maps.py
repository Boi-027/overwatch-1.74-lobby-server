"""Make data/game_maps_174.json: per map, the placeables the server must create and the spawn points.

Inputs (all from an Overwatch 1.74 install through DataTool, OWLib):

    DataTool.exe <game> extract-stu-type <dump> --xml          (003, 09F, 0C5 and strings.json)
    DataTool.exe <game> extract-stu-type <raw> 0BC             (raw map chunks)
    DataTool.exe <game> dump-map-entities <ents> <09F index>... (entity instance data as XML)

    py tools/extract_game_maps.py --stu <dump> --raw <raw> --ents <ents>

Rules, from the client (IDA base 0x7FF788F10000):
- Map placeables get one flat index over the placeable types whose flag is set in the type table
  0x7FF78BD36CE0 (builder 0x7FF78AA52900): light, area, entity, ... in that order. So the first
  entity has index count(light) + count(area).
- While the map loads, every entity placeable whose definition (003) has component bit 58
  (STU 0x737C5C41, spawn point) or bit 94 (STU 0xCA1FB0B8) goes into the pending list
  (functor 0x7FF78969B9A0), with the placeable's first identifier when it is not zero. The loading
  screen ends when the list is empty. The server empties it with a ch4 "from map" create of each
  index (entity id 0x80000000 | index).
- Each tick the client drops entries whose identifier is not in the game mode's m_A43573F4
  (0x7FF78969EFD0). Entries without an identifier always stay.
- Spawn points are picked by target tag. The mode graphs give team 0 the tags 0x310 / 0x30D / 0x3CE
  and team 1 the tags 0x341 / 0x3CC / 0x3CD (control stages 0 / 1 / 2). Assault, escort and hybrid
  take the pair from the current objective: its var 10549 (0x2935) is the defenders' room and var
  10550 (0x2936) the attackers' room. Team 1 attacks first; sides swap between rounds.
"""

import argparse
import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "game_maps_174.json"
NS = "{https://yretenai.com/dragonml/v1}"
SPAWN = 0x737C5C41  # component bit 58
REQUIRED = 0xCA1FB0B8  # component bit 94
LIGHT, AREA, ENTITY = 7, 8, 9  # placeable types, byte 19 of each placeable
TEAM_TAGS = {0x310: 0, 0x30D: 0, 0x3CE: 0, 0x341: 1, 0x3CC: 1, 0x3CD: 1}
CONTROL_STAGES = {0x310: 0, 0x341: 0, 0x30D: 1, 0x3CC: 1, 0x3CE: 2, 0x3CD: 2}
CONTROL_POINT_TAGS = {0x41B: 0, 0x41C: 1, 0x41D: 2}
FFA_TAG = 0x413
OBJECTIVE_GRAPH = 0xF4C  # the objective graph that holds the spawn tags of assault/escort/hybrid
POINT_GRAPH = 0xE8E  # capture point graph (its var 0x1DDB is the point name)
PAYLOAD_GRAPH = 0x1225  # the payload's own graph
NAMES = {
    0x661: "Tutorial",
    0x751: "Numbani",
    0x6FE: "King's Row (Uprising)",
    0x79F: "Rialto (Retribution)",
    0xAFE: "Havana (Storm Rising)",
    0x6C9: "Junkenstein's Revenge",
}


def guid(index: int, file_type: int) -> int:
    """A GUID from its index and file type: the type sits bit-reversed at bits 48..59."""
    return int(f"{file_type - 1:012b}"[::-1], 2) << 48 | index


def hex_guid(value: int) -> str:
    return f"0x{value:016X}"


def ref_index(text: str) -> int:
    """ "000000002962.01C" -> 0x2962"""
    return int(text.split(".")[0], 16)


def f32(value: float) -> float:
    """The shortest decimal that is still the same 32-bit float."""
    for digits in range(1, 10):
        short = float(f"{value:.{digits}g}")
        if struct.pack("<f", short) == struct.pack("<f", value):
            return short
    return value


def yaw(q: tuple) -> float:
    """Rotation around +Y in degrees."""
    x, y, z, w = q
    return round(math.degrees(math.atan2(2 * (w * y + x * z), 1 - 2 * (x * x + y * y))), 3)


def read_placeables(data: bytes) -> list:
    """[(offset, type)] of one map chunk; [] for chunks in other formats."""
    count, _, offset, extra = struct.unpack_from("<4I", data, 0)
    if extra or not offset:
        return []
    out = []
    for _ in range(count):
        if offset + 24 > len(data):
            return []
        kind, length = struct.unpack_from("<BI", data, offset + 19)
        if length < 24 or offset + length > len(data):
            return []
        out.append((offset, kind))
        offset += length
    return out


def read_map(raw: Path, index: int) -> tuple:
    """(first entity index, entity placeables) of map 002 <index>."""
    counts = {}
    entities = []
    for path in raw.glob(f"????{index:08X}.0BC"):
        data = path.read_bytes()
        for offset, kind in read_placeables(data):
            counts[kind] = counts.get(kind, 0) + 1
            if kind != ENTITY:
                continue
            body = offset + 24
            definition, identifier = struct.unpack_from("<2Q", data, body)
            entities.append(
                {
                    "definition": definition,
                    "identifier": identifier,
                    "position": struct.unpack_from("<3f", data, body + 24),
                    "rotation": struct.unpack_from("<4f", data, body + 48),
                    "client_id": struct.unpack_from("<H", data, body + 104)[0],
                    "order": (int(path.name[:4], 16), offset),
                }
            )
    entities.sort(key=lambda e: e["order"])
    return counts.get(LIGHT, 0) + counts.get(AREA, 0), entities


def components(stu: Path) -> dict:
    """003 index -> set of component STU hashes (the definition's own m_componentMap)."""
    table = {}
    for path in (stu / "003").glob("*.003.xml"):
        root = ET.parse(path).getroot()
        found = set()
        for node in root.iter():
            if node.get(NS + "name") == "m_componentMap":
                found = {int(child.get(NS + "key")) & 0xFFFFFFFF for child in node if child.get(NS + "key")}
                break
        table[int(path.name.split(".")[0], 16)] = found
    return table


def modes(stu: Path) -> dict:
    """0C5 index -> m_A43573F4 identifiers."""
    table = {}
    for path in (stu / "0C5").glob("*.0C5.xml"):
        text = path.read_text(encoding="utf-8")
        match = re.search(r'dragon:name="m_A43573F4">(.*?)</dragon:array>', text, re.S)
        found = re.findall(r'GUID="([0-9A-F]+\.01C)"', match.group(1)) if match else []
        table[int(path.name.split(".")[0], 16)] = [guid(ref_index(g), 0x1C) for g in found]
    return table


def headers(stu: Path) -> list:
    out = []
    for path in sorted((stu / "09F").glob("*.09F.xml")):
        root = ET.parse(path).getroot()
        header = {"state": root.get("m_A125818B"), "modes": []}
        for child in root:
            name = child.get(NS + "name")
            if name == "m_map":
                header["map"] = ref_index(child.get("GUID"))
            elif name == "m_supportedGamemodes":
                refs = [r.get("GUID", "") for r in child]
                header["modes"] = [ref_index(r) for r in refs if r.endswith(".0C5")]
            elif name == "m_mapName":
                header["path"] = child.get("Value")
        header["index"] = int(path.name.split(".")[0], 16)
        out.append(header)
    return out


def instance_data(ents: Path, index: int) -> list:
    """Per entity placeable: the parsed instance data STUs, in placeable order."""
    path = ents / f"{index:X}_entities.xml"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    out = []
    for body in re.findall(r"<entity [^>]*>(.*?)</entity>", text, re.S):
        docs = [d.strip() for d in body.split('<?xml version="1.0" encoding="utf-8"?>') if d.strip()]
        out.append([ET.fromstring(d) for d in docs])
    return out


def target_tags(stus: list) -> list:
    for stu in stus:
        if stu.tag == "STUTargetTagInstanceData":
            return [ref_index(r.get("GUID")) for r in stu.iter() if r.get("GUID", "").endswith(".06C")]
    return []


def spawn_teams(stus: list) -> list:
    for stu in stus:
        if stu.tag == "STU_7D175935":
            return [t.text for t in stu.iter("TeamIndex")]
    return []


def graph_vars(stus: list) -> dict:
    """Graph index -> {var identifier index: value} of the statescript instance data."""
    out = {}
    for stu in stus:
        if stu.tag != "STUStatescriptComponentInstanceData":
            continue
        for graph in stu.iter("STUStatescriptGraphWithOverrides"):
            ref = next((c.get("GUID") for c in graph if c.get(NS + "name") == "m_graph"), None)
            if not ref:
                continue
            values = {}
            for entry in graph.iter("STUStatescriptSchemaEntry"):
                key = value = None
                for child in entry:
                    if child.get(NS + "name") == "m_0D09D2D9":
                        key = ref_index(child.get("GUID"))
                    elif child.get(NS + "name") == "m_value":
                        refs = [r.get("GUID") for r in child.iter() if r.get("GUID")]
                        refs = [r for r in refs if not r.startswith("000000000000")]
                        strings = [s.get("Value") for s in child.iter("teString")]
                        value = child.get("m_value") or next(iter(refs + strings), None)
                if key is not None:
                    values[key] = value
            out[ref_index(ref)] = values
    return out


def build_map(header: dict, raw: Path, ents: Path, defs: dict, mode_ids: dict) -> dict:
    index = header["map"]
    base, entities = read_map(raw, index)
    stus = instance_data(ents, header["index"])
    if len(stus) != len(entities):
        raise SystemExit(f"map {index:X}: {len(entities)} entities but {len(stus)} in the DataTool dump")
    placeables, identifiers, mirrored, spawns, objectives = [], {}, [], [], []
    defense_tags, attack_tags = {}, {}
    for number, (entity, data) in enumerate(zip(entities, stus, strict=True)):
        flat = base + number
        found = defs.get(entity["definition"] & 0xFFFFFFFF, set())
        graphs = graph_vars(data)
        if OBJECTIVE_GRAPH in graphs:
            values = graphs[OBJECTIVE_GRAPH]
            order = int(values.get(0x2934) or 0)
            defense = ref_index(values[0x2935]) if values.get(0x2935) else None
            attack = ref_index(values[0x2936]) if values.get(0x2936) else None
            objectives.append(
                {
                    "objective": order,
                    "name": graphs.get(POINT_GRAPH, {}).get(0x1DDB),
                    "kind": "checkpoint" if values.get(0x2931) else "point",
                    "placeable": flat,
                    "position": [f32(v) for v in entity["position"]],
                    "defense_tag": hex_guid(guid(defense, 0x6C)) if defense else None,
                    "attack_tag": hex_guid(guid(attack, 0x6C)) if attack else None,
                }
            )
            if defense:
                defense_tags.setdefault(defense, []).append(order)
            if attack:
                attack_tags.setdefault(attack, []).append(order)
        elif POINT_GRAPH in graphs:
            stage = next((CONTROL_POINT_TAGS[t] for t in target_tags(data) if t in CONTROL_POINT_TAGS), None)
            if stage is not None:
                objectives.append(
                    {
                        "stage": stage,
                        "name": graphs[POINT_GRAPH].get(0x6D9) or graphs[POINT_GRAPH].get(0x1DDB),
                        "kind": "control point",
                        "placeable": flat,
                        "position": [f32(v) for v in entity["position"]],
                    }
                )
        elif PAYLOAD_GRAPH in graphs:
            objectives.append(
                {
                    "kind": "payload",
                    "placeable": flat,
                    "position": [f32(v) for v in entity["position"]],
                    "rotation": [f32(v) for v in entity["rotation"]],
                }
            )
        if not found & {SPAWN, REQUIRED}:
            if entity["client_id"] == 0:
                mirrored.append(flat)
            continue
        placeables.append(flat)
        if entity["identifier"]:
            identifiers[str(flat)] = hex_guid(entity["identifier"])
        if SPAWN not in found:
            continue
        tags = target_tags(data)
        team = next((TEAM_TAGS[t] for t in tags if t in TEAM_TAGS), None)
        spawn = {
            "team": team,
            "position": [f32(v) for v in entity["position"]],
            "yaw": yaw(entity["rotation"]),
            "placeable": flat,
            "entity_index": number,
            "rotation": [f32(v) for v in entity["rotation"]],
            "tags": [hex_guid(guid(t, 0x6C)) for t in tags],
            "teams": spawn_teams(data),
        }
        if entity["identifier"]:
            spawn["identifier"] = hex_guid(entity["identifier"])
        if FFA_TAG in tags:
            spawn["ffa"] = True
        spawns.append(spawn)
    # How the first team mode of the map uses each room.
    control = 0x17 in header["modes"]
    for spawn in spawns:
        tags = [int(t, 16) & 0xFFFFFFFF for t in spawn["tags"]]
        if control:
            stages = sorted({CONTROL_STAGES[t] for t in tags if t in CONTROL_STAGES})
            if stages:
                spawn["stage"] = stages[0]
        else:
            use = []
            for t in tags:
                use += [[k, "defense"] for k in defense_tags.get(t, [])]
                use += [[k, "attack"] for k in attack_tags.get(t, [])]
            if use:
                spawn["objectives"] = sorted(use)
    objectives.sort(key=lambda o: (o["kind"] == "payload", o.get("stage", 0), o.get("objective", 0)))
    by_mode = {}
    for mode in header["modes"]:
        if mode not in mode_ids:
            continue  # the header names a mode that has no 0C5 asset in this build
        allowed = set(mode_ids[mode])
        by_mode[hex_guid(guid(mode, 0xC5))] = [
            p for p in placeables if str(p) not in identifiers or int(identifiers[str(p)], 16) in allowed
        ]
    return {
        "modes": [hex_guid(guid(m, 0xC5)) for m in header["modes"]],
        "placeables": placeables,
        "identifiers": identifiers,
        "placeables_by_mode": by_mode,
        "first_entity": base,
        "spawns": spawns,
        "objectives": objectives,
        "mirrored": mirrored,
    }


def map_name(header: dict, english: dict) -> str:
    if header["map"] in NAMES:
        return NAMES[header["map"]]
    path = header.get("path", "")
    name = english.get(f"{header['index']:012X}.09F", {}).get("Name") or path.split("\\")[-1]
    variant = re.search(r"\((\w[^)]*)\)", path)
    return f"{name} ({variant.group(1)})" if variant and variant.group(1) not in name else name


def dump(maps: dict) -> str:
    """JSON with one spawn, objective or list per line."""
    lines = ["{"]
    for number, (key, entry) in enumerate(maps.items()):
        lines.append(f" {json.dumps(key)}: {{")
        fields = list(entry.items())
        for i, (field, value) in enumerate(fields):
            end = "," if i < len(fields) - 1 else ""
            if field in ("spawns", "objectives") and value:
                items = [f"   {json.dumps(v, ensure_ascii=False)}" for v in value]
                lines.append(f"  {json.dumps(field)}: [")
                lines.append(",\n".join(items))
                lines.append(f"  ]{end}")
            else:
                lines.append(f"  {json.dumps(field)}: {json.dumps(value, ensure_ascii=False)}{end}")
        lines.append(" }" + ("," if number < len(maps) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stu", type=Path, required=True, help="STU dump with 003, 09F and 0C5 as XML")
    parser.add_argument("--raw", type=Path, required=True, help="folder that holds the raw 0BC map chunks")
    parser.add_argument("--ents", type=Path, required=True, help="dump-map-entities output folder")
    args = parser.parse_args()
    raw = args.raw / "0BC" if (args.raw / "0BC").is_dir() else args.raw
    defs, mode_ids = components(args.stu), modes(args.stu)
    english = json.loads((ROOT / "data" / "extracted_maps.json").read_text(encoding="utf-8"))
    maps = {}
    for header in headers(args.stu):
        if not header["modes"] or "map" not in header:
            continue
        entry = {"name": map_name(header, english), "state": header["state"]}
        entry.update(build_map(header, raw, args.ents, defs, mode_ids))
        maps[hex_guid(guid(header["map"], 0x2))] = entry
    OUT.write_text(dump(maps), encoding="utf-8")
    print(f"{len(maps)} maps -> {OUT}")


if __name__ == "__main__":
    main()
