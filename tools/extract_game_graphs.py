#!/usr/bin/env python3
"""Make data/game_graphs_174.json.gz: the statescript graphs the game server runs.

Reads DataTool's XML dumps of Overwatch 1.74 (`extract-stu-type <dump> 01B --xml`, and 003 for the hero
bodies) and keeps every graph reachable from the 32 hero bodies (their statescript component's initial
graphs and their weapon scripts) and from the controller, hero select, HUD and mode graphs, following every
graph reference (a GUID of type 01B anywhere in a kept graph).

    py tools/extract_game_graphs.py [--xml D:\\OW174_stu_xml]

Per graph it keeps what a runtime needs and drops the editor layout (positions, comments, display names,
colours). The file format is described in ow174/game/script/graph.py, which reads it.

How the XML is read (DataTool's DragonML):
- An object is an element named after its STU class. Scalar fields are attributes ("{null}" = null, decimals
  use a comma), object and array fields are child elements with dragon:name. An object met a second time is a
  `dragon:ref` to its dragon:id; a `tank:ref` with a GUID is an asset reference.
- Nodes live in m_nodes (the index is the node's m_871A8203) and in m_graph.m_items with the editor's other
  items (subgraph boxes). A node is identified here by its position in m_items.
- A plug holds its links; a link names the input plug it enters, and that plug's m_parentNode is the node
  (or, without one, the node whose field holds the plug). The XML often writes an input plug inside the link
  that first uses it, so ownership comes from those fields and not from where the element sits.
"""

import argparse
import gzip
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "game_graphs_174.json.gz"
HEROES = ROOT / "data" / "game_heroes_174.json"
NS = "{https://yretenai.com/dragonml/v1}"
ID = NS + "id"
NAME = NS + "name"

ROOT_GRAPHS = {
    0x13C1: "Practice Range controller",
    0x0C90: "PvP controller",
    0x288A: "hero select host",
    0x288B: "hero select screen",
    0x288D: "hero select team list",
    0x20E4: "weapon and ability HUD",
    0x13C0: "Practice Range mode",
    0x0F4A: "PvP mode",
    0x0EC2: "PvP mode",
    0x0C8E: "PvP mode body",
}
LAYOUT = {"m_pos", "m_displayName", "m_comment", "m_F008EA57"}  # STUGraphItem's editor fields
NODE_KEYS = {
    "m_uniqueID": "uid",
    "m_871A8203": "rid",
    "m_602E1F8F": "remote",
    "m_26B6454C": "state",
    "m_clientOnly": "client_only",
    "m_serverOnly": "server_only",
}
LISTS = {"m_states": "states", "m_entries": "entries", "m_remoteSyncNodes": "remote"}
INT = re.compile(r"-?\d+$")
FLOAT = re.compile(r"-?\d+(,\d+)?(E[-+]?\d+)?$")
CS_CLASS = re.compile(r"public class (\w+)(?:\s*:\s*(\w+))?\s*\{(.*?)\n    \}", re.S)
CS_FIELD = re.compile(r"public ([\w<>]+)(?:\[\])? (m_\w+)\s*(?:=[^;]*)?;")
CS_ENUM = re.compile(r"public enum (\w+)\s*:\s*\w+\s*\{(.*?)\}", re.S)
CS_MEMBER = re.compile(r"\]\s*(\w+)\s*=\s*(-?(?:0x)?[0-9A-Fa-f]+)")


class EnumTables:
    """Enum member names -> values, from the STU types of the OWLib build that made the dump.

    DataTool writes an enum field as its member name (x1F0B0C4D, TeamBlue); the asset holds the number,
    so the data file stores the number again."""

    def __init__(self, types_dir: Path) -> None:
        self.bases, self.fields, self.enums = {}, {}, {}
        for path in sorted(types_dir.rglob("*.cs")):
            text = path.read_text(encoding="utf-8")
            for name, body in CS_ENUM.findall(text):
                self.enums[name] = {member: int(number, 0) for member, number in CS_MEMBER.findall(body)}
            for name, base, body in CS_CLASS.findall(text):
                self.bases[name] = base
                self.fields[name] = {field: kind for kind, field in CS_FIELD.findall(body)}
        self.missed = {}

    def field_enum(self, cls: str, field: str) -> str | None:
        while cls:
            kind = self.fields.get(cls, {}).get(field)
            if kind is not None:
                return kind if kind in self.enums else None
            cls = self.bases.get(cls)
        return None

    def number(self, enum: str | None, text, where: str):
        """The member's number, or the text itself when it is not an enum member."""
        if not isinstance(text, str):
            return text
        members = self.enums.get(enum or "")
        if members is not None and text in members:
            return members[text]
        if re.fullmatch(r"x[0-9A-F]{8}", text):
            self.missed[where] = self.missed.get(where, 0) + 1
        return text


def tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def scalar(text: str | None):
    """An attribute or text value: None, int, float, or the text itself (enum values such as x1F0B0C4D)."""
    if text is None or text == "{null}":
        return None
    if INT.match(text):
        return int(text)
    if FLOAT.match(text):
        return float(text.replace(",", "."))
    return text


def full_guid(text: str) -> int:
    """ "000000001D8E.01C" -> 0x0D80000000001D8E: the type sits bit-reversed at bits 48..59."""
    index, kind = text.split(".")
    if kind == "1000":  # all type bits set: DataTool's form of the invalid GUID
        return 0xFFFFFFFFFFFFFFFF
    return int(f"{int(kind, 16) - 1:012b}"[::-1], 2) << 48 | int(index, 16)


def guid_text(text: str) -> str:
    return f"0x{full_guid(text):016X}"


def guid_index(text: str) -> int:
    return int(text.split(".")[0], 16)


class StuXml:
    """One DataTool XML file with its object references resolved."""

    def __init__(self, path: Path, enums: EnumTables) -> None:
        self.enums = enums
        self.root = ET.parse(path).getroot()
        self.objects = {}
        for element in self.root.iter():
            key = element.get(ID)
            if key is not None and not (tag(element) == "ref" and element.get("GUID") is None):
                self.objects.setdefault(key, element)
        self.nodes = {}  # id(element) -> position of a node in m_items
        self.items = {}  # id(element) -> position of another m_items entry
        self.encoding = set()

    def resolve(self, element: ET.Element | None) -> ET.Element | None:
        if element is not None and tag(element) == "ref" and element.get("GUID") is None:
            return self.objects[element.get(ID)]
        return element

    def field(self, element: ET.Element, name: str) -> ET.Element | None:
        for child in element:
            if child.get(NAME) == name:
                resolved = self.resolve(child)
                return None if resolved is None or tag(resolved) == "null" else resolved
        return None

    def is_plug(self, element: ET.Element) -> bool:
        """A plug (an STUGraphPlug): it has both m_links and m_parentNode."""
        names = set(element.attrib) | {child.get(NAME) for child in element}
        return "m_links" in names and "m_parentNode" in names

    def fields(self, element: ET.Element, skip=()) -> dict:
        out = {}
        cls = tag(element)
        for key, text in element.attrib.items():
            if not key.startswith(NS) and key not in skip:
                out[key] = self.enums.number(self.enums.field_enum(cls, key), scalar(text), f"{cls}.{key}")
        for child in element:
            key = child.get(NAME)
            if key is not None and key not in skip:
                out[key] = self.value(child)
        return out

    def value(self, element: ET.Element):
        """A field or array element in the generic form: None, a number, a string, a list, an asset GUID
        ("0x" and 16 hex digits), {"node": position}, {"item": position}, or an object {"$": class, ...}."""
        if tag(element) == "ref" and element.get("GUID") is not None:
            return guid_text(element.get("GUID"))
        element = self.resolve(element)
        kind = tag(element)
        if kind == "null":
            return None
        if kind == "ref":
            return guid_text(element.get("GUID"))
        if kind == "array":
            return [self.value(child) for child in element]
        if id(element) in self.nodes:
            return {"node": self.nodes[id(element)]}
        if id(element) in self.items:
            return {"item": self.items[id(element)]}
        if element.text and element.text.strip() and not len(element):
            return self.enums.number(kind, scalar(element.text.strip()), kind)
        if self.is_plug(element):
            return self.plug(element)
        if id(element) in self.encoding:
            raise ValueError(f"reference cycle through {kind} {element.get(ID)}")
        self.encoding.add(id(element))
        try:
            return {"$": kind, **self.fields(element)}
        finally:
            self.encoding.discard(id(element))

    def plug(self, element: ET.Element) -> dict:
        """A plug: its fields and "links", the [node position, input plug path] of each link in order."""
        return {"$": tag(element), **self.fields(element, skip={"m_links", "m_parentNode"}), "links": []}


class GraphXml(StuXml):
    """A 01B graph: its nodes, items and plugs."""

    def __init__(self, path: Path, enums: EnumTables) -> None:
        super().__init__(path, enums)
        self.top = {child.get(NAME): child for child in self.root}
        graph = self.top.get("m_graph")
        items = self.field(graph, "m_items") if graph is not None else None
        self.item_list = [self.resolve(child) for child in items] if items is not None else []
        self.node_list = [self.resolve(child) for child in self.top.get("m_nodes", [])]
        known = {id(node) for node in self.node_list if tag(node) != "null"}
        for position, item in enumerate(self.item_list):
            if tag(item) == "null":
                continue
            if id(item) in known:
                self.nodes[id(item)] = position
            else:
                self.items[id(item)] = position
        self.owners = {}  # id(plug) -> [(node position, field path)]
        self.problems = []
        for node in self.node_list:
            if tag(node) != "null" and id(node) in self.nodes:
                for path, plug in self.plugs_in(node, ""):
                    self.owners.setdefault(id(plug), []).append((self.nodes[id(node)], path))

    def plugs_in(self, element: ET.Element, prefix: str):
        """(field path, plug) for every plug a node or one of its sub-objects holds."""
        for child in element:
            key = child.get(NAME)
            if key is None:
                continue
            yield from self.plugs_below(self.resolve(child), prefix + key)

    def plugs_below(self, element: ET.Element, path: str):
        kind = tag(element)
        if kind in ("null", "ref") or id(element) in self.nodes or id(element) in self.items:
            return
        if kind == "array":
            for number, child in enumerate(element):
                yield from self.plugs_below(self.resolve(child), f"{path}[{number}]")
        elif self.is_plug(element):
            yield path, element
        else:
            yield from self.plugs_in(element, path + ".")

    def plug(self, element: ET.Element) -> dict:
        out = super().plug(element)
        links = self.field(element, "m_links")
        if links is not None:
            out["links"] = [self.target(self.resolve(link)) for link in links]
        return out

    def target(self, link: ET.Element) -> list:
        """[node position, the input plug's field path in that node] of a link; [None, name] when the plug
        belongs to no node of m_nodes."""
        plug = self.field(link, "m_inputPlug")
        if plug is None:
            self.problems.append("a link without an input plug")
            return [None, None]
        owners = self.owners.get(id(plug), [])
        parent = self.field(plug, "m_parentNode")
        position = self.nodes.get(id(parent)) if parent is not None else None
        if position is not None:
            paths = [path for where, path in owners if where == position]
            return [position, paths[0] if paths else plug.get(NAME)]
        if parent is None and owners:
            return list(owners[0])
        self.problems.append(f"a link into {tag(plug)} {plug.get(NAME)} of no known node")
        return [None, plug.get(NAME)]

    def node(self, element: ET.Element) -> dict:
        record = {"pos": self.nodes[id(element)], "class": tag(element)}
        for key, short in NODE_KEYS.items():
            record[short] = scalar(element.get(key))
        record["fields"] = self.fields(element, skip=LAYOUT | set(NODE_KEYS))
        return record

    def positions(self, name: str) -> list:
        out = []
        for child in self.top.get(name, []):
            element = self.resolve(child)
            out.append(None if tag(element) == "null" else self.nodes.get(id(element)))
        return out

    def sync_vars(self) -> list:
        out = []
        for child in self.top.get("m_syncVars", []):
            entry = self.resolve(child)
            ident = self.field(entry, "m_0D09D2D9")
            var = guid_index(ident.get("GUID")) if ident is not None else None
            fields = self.fields(entry, skip={"m_0D09D2D9"})
            out.append([var, fields["m_56341592"], fields["m_AC9480C7"]])
        return out

    def record(self, index: int) -> dict:
        header = self.fields(self.root, skip={child.get(NAME) for child in self.root})
        skip = {"m_graph", "m_nodes", "m_syncVars", *LISTS}
        return {
            "index": index,
            "header": {key: value for key, value in header.items() if value is not None},
            "fields": {
                child.get(NAME): self.value(child) for child in self.root if child.get(NAME) not in skip
            },
            "items": len(self.item_list),
            "containers": {str(position): tag(self.item_list[position]) for position in self.items.values()},
            "nodes": [self.node(node) for node in self.ordered()],
            "unknown_nodes": [rid for rid, node in enumerate(self.node_list) if tag(node) == "null"],
            **{short: self.positions(name) for name, short in LISTS.items()},
            "sync_vars": self.sync_vars(),
        }

    def ordered(self) -> list:
        """The nodes in m_items order."""
        nodes = [node for node in self.node_list if id(node) in self.nodes]
        return sorted(nodes, key=lambda node: self.nodes[id(node)])

    def graph_refs(self) -> set[int]:
        return {
            guid_index(element.get("GUID"))
            for element in self.root.iter()
            if (element.get("GUID") or "").endswith(".01B")
        }


def graph_ref(xml: StuXml, element: ET.Element, name: str) -> int | None:
    """The graph index a field names, or None."""
    ref = xml.field(element, name)
    return guid_index(ref.get("GUID")) if ref is not None else None


def body_record(path: Path, enums: EnumTables, hero: str, name: str) -> tuple[dict, set[int]]:
    """A hero body (003): its statescript component's initial graphs in order, with their schema
    overrides, and its weapon component's scripts."""
    xml = StuXml(path, enums)
    graphs, weapons, manager = [], [], None
    for element in xml.root.iter():
        kind = tag(element)
        if kind == "STUStatescriptComponent" and xml.field(element, "m_B634821A") is not None:
            for entry in xml.field(element, "m_B634821A"):
                entry = xml.resolve(entry)
                overrides = xml.fields(entry, skip={"m_graph"})
                graphs.append({"graph": graph_ref(xml, entry, "m_graph"), **overrides})
        elif kind == "STUWeaponComponent" and element.get(NAME) == "dragon:value":
            manager = graph_ref(xml, element, "m_managerScript")
            weapon_list = xml.field(element, "m_weapons")
            for weapon in weapon_list if weapon_list is not None else []:
                weapons.append(graph_ref(xml, xml.resolve(weapon), "m_script"))
    record = {"hero": hero, "name": name, "graphs": graphs, "manager": manager, "weapons": weapons}
    used = {entry["graph"] for entry in graphs} | {manager, *weapons}
    return record, {graph for graph in used if graph is not None}


def collect(xml_dir: Path, enums: EnumTables) -> dict:
    heroes = json.loads(HEROES.read_text(encoding="utf-8"))
    bodies, reason, queue = {}, {}, []
    for hero, entry in heroes.items():
        body = int(entry["body"], 16)
        path = xml_dir / "003" / f"{body & 0xFFFFFFFFFFFF:012X}.003.xml"
        record, used = body_record(path, enums, hero, entry["name"])
        bodies[f"0x{body:016X}"] = record
        for graph in sorted(used):
            queue.append((graph, entry["name"]))
    queue += list(ROOT_GRAPHS.items())
    graphs, missing = {}, set()
    while queue:
        index, why = queue.pop(0)
        if index in graphs or index in missing:
            continue
        path = xml_dir / "01B" / f"{index:012X}.01B.xml"
        if not path.is_file():
            missing.add(index)
            continue
        xml = GraphXml(path, enums)
        graphs[index] = xml.record(index)
        reason[index] = why
        for problem in sorted(set(xml.problems)):
            print(f"  {index:04X}: {xml.problems.count(problem)} x {problem}")
        queue += [(child, why) for child in sorted(xml.graph_refs())]
    return {
        "version": 1,
        "roots": {"bodies": sorted(bodies), "graphs": sorted(ROOT_GRAPHS)},
        "bodies": bodies,
        "missing": sorted(missing),
        "graphs": [graphs[index] for index in sorted(graphs)],
        "_reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--xml", type=Path, default=Path(r"D:\OW174_stu_xml"), help="DataTool XML dump")
    parser.add_argument("--owlib", type=Path, default=Path(r"D:\OWLib"), help="the OWLib that made the dump")
    args = parser.parse_args()
    enums = EnumTables(args.owlib / "TankLib" / "STU" / "Types")
    data = collect(args.xml, enums)
    for where, count in sorted(enums.missed.items()):
        print(f"  enum value without a member: {where} x{count}")
    reason = data.pop("_reason")
    by_root = {}
    for index, why in reason.items():
        by_root.setdefault(why, []).append(index)
    for why, indexes in by_root.items():
        print(f"{why}: {' '.join(f'{index:04X}' for index in sorted(indexes))}")
    if data["missing"]:
        print("referenced but not in the dump: " + " ".join(f"{index:04X}" for index in data["missing"]))
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    OUT.write_bytes(gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0))
    nodes = sum(len(graph["nodes"]) for graph in data["graphs"])
    print(f"{len(data['graphs'])} graphs, {nodes} nodes, {len(data['bodies'])} bodies -> {OUT}")
    print(f"{OUT.stat().st_size / 1e6:.2f} MB ({len(text) / 1e6:.1f} MB of JSON)")


if __name__ == "__main__":
    main()
