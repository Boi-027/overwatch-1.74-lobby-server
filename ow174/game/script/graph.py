"""The statescript graphs the game server runs, from data/game_graphs_174.json.gz (made by
tools/extract_game_graphs.py from DataTool's XML dump of 1.74).

The file is gzip JSON:
- "graphs": one record per 01B graph: "index" (the graph's 16-bit index); "header" (its scalar fields:
  m_nodesBitCount, m_statesBitCount, m_remoteSyncNodesBitCount, m_syncVarsBitCount, m_predictionBehavior,
  ...); "fields" (its other fields: m_publicSchema, m_9CEC6985, m_680A2CB2, ...); "items" (the length of
  m_graph.m_items); "containers" (item position -> class of the editor's subgraph boxes); "nodes";
  "unknown_nodes" (m_nodes indexes DataTool could not read); "states", "entries", "remote" (m_states,
  m_entries and m_remoteSyncNodes as node positions, None for an entry DataTool could not read);
  "sync_vars" ([variable id or None, scope, m_AC9480C7] per m_syncVars entry).
- A node: "pos" (its position in m_items, the key links and lists use), "class" (the STU class, STU_<hash>
  when unnamed), "uid" (m_uniqueID), "rid" (m_871A8203, its index in m_nodes), "remote" (m_602E1F8F, -1 =
  none), "state" (m_26B6454C, None for a node that is not a state), "client_only", "server_only" and
  "fields": every other field. The editor's layout (positions, comments, names, colours) is left out.
- Values: None, numbers (enum fields hold their numbers), lists, an asset GUID as "0x" and 16 hex digits
  (the full 64-bit key, type bits included), {"node": position}, {"item": position} (a subgraph box) and
  objects {"$": class, field: value, ...}. A plug is an object with "links": [node position, the input
  plug's field path in that node] per link, in the order the client follows them; the position is None
  when the target node is one DataTool could not read.
- "bodies": per hero body (the 003 GUID): its statescript component's initial graphs in definition order
  (graph index and schema overrides m_1EB5A024) and its weapon component's manager and weapon scripts.
- "missing": graph indexes referenced but not in the dump.

Which states a frame carries (spec 09 section 7.1, read in the client at 0x7FF78991BE30):
- an owner (H=1) frame has one entry per m_states entry that is neither client-only nor server-only;
- an H=0 frame has one per m_remoteSyncNodes entry that is a state (the client casts it to the state base;
  actions fail) and neither client-only nor server-only.
The per-instance variable list has a presence bit per m_syncVars entry that has an identifier and the
instance scope (0x7FF78991C270).
"""

import gzip
import json
import re
from dataclasses import dataclass
from functools import cache

from ow174.paths import DATA_DIR

GRAPHS_PATH = DATA_DIR / "game_graphs_174.json.gz"
INSTANCE, ENTITY = 0, 1  # variable scopes: sync var m_56341592, STUConfigVarDynamic.m_60DB8F99
GUID_TEXT = re.compile(r"0x[0-9A-F]{16}$")
GRAPH_TYPE = 0x0580  # a 01B GUID's type bits, bits 48..63
PATH_PART = re.compile(r"(\w+)((?:\[\d+\])*)")


def guid(value) -> int | None:
    """An asset GUID of the data as an int, None for any other value."""
    if isinstance(value, str) and GUID_TEXT.match(value):
        return int(value, 16)
    return None


def is_plug(value) -> bool:
    return isinstance(value, dict) and "$" in value and "links" in value


def field_at(fields: dict, path: str):
    """The value at a field path such as "m_onBeginPlug", "m_transitionPlug[0]" or "m_45216F79.m_D44EB181";
    None when it is not there."""
    value = fields
    for part in path.split("."):
        match = PATH_PART.fullmatch(part)
        if match is None or not isinstance(value, dict):
            return None
        value = value.get(match.group(1))
        for index in re.findall(r"\[(\d+)\]", match.group(2)):
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
    return value


def _plugs(value, path: str, out: dict) -> None:
    if is_plug(value):
        out[path] = value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "$":
                _plugs(item, f"{path}.{key}" if path else key, out)
    elif isinstance(value, list):
        for number, item in enumerate(value):
            _plugs(item, f"{path}[{number}]", out)


@dataclass(frozen=True)
class SyncVar:
    index: int  # the entry's position in m_syncVars
    var: int | None  # the variable id (the .01C identifier's index), None for padding
    scope: int  # INSTANCE or ENTITY
    flag: int  # m_AC9480C7

    @property
    def has_presence_bit(self) -> bool:
        return self.var is not None and self.scope == INSTANCE


class Node:
    """One node of a graph. `fields` holds every field the file keeps, in the generic form above."""

    def __init__(self, record: dict) -> None:
        self.pos: int = record["pos"]
        self.cls: str = record["class"]
        self.uid: int = record["uid"]
        self.rid: int = record["rid"]
        self.remote: int = record["remote"]
        self.state: int | None = record["state"]
        self.client_only = bool(record["client_only"])
        self.server_only = bool(record["server_only"])
        self.fields: dict = record["fields"]

    def __repr__(self) -> str:
        return f"<Node pos {self.pos} rid {self.rid} {self.cls}>"

    @property
    def is_state(self) -> bool:
        return bool(self.fields.get("m_2BBEEAB8"))

    @property
    def is_action(self) -> bool:
        return bool(self.fields.get("m_ADEB6E05"))

    @property
    def is_networked(self) -> bool:
        """Neither client-only nor server-only: the kind of state a frame carries."""
        return not self.client_only and not self.server_only

    def field(self, path: str):
        return field_at(self.fields, path)

    def plugs(self) -> dict[str, dict]:
        """Every plug of the node by field path."""
        out = {}
        _plugs(self.fields, "", out)
        return out

    def links(self, path: str) -> list[tuple[int | None, str]]:
        """(target node position, input plug path) of a plug's links, in order; [] for no such plug."""
        plug = self.field(path)
        return [tuple(link) for link in plug["links"]] if is_plug(plug) else []


class Graph:
    """One 01B graph."""

    def __init__(self, record: dict) -> None:
        self.index: int = record["index"]
        self.header: dict = record["header"]
        self.fields: dict = record["fields"]
        self.items: int = record["items"]
        self.containers = {int(position): cls for position, cls in record["containers"].items()}
        self.nodes = [Node(node) for node in record["nodes"]]
        self.unknown_rids: list[int] = record["unknown_nodes"]
        self._by_pos = {node.pos: node for node in self.nodes}
        self._by_rid = {node.rid: node for node in self.nodes}
        self.states = self._list(record["states"])
        self.entries = self._list(record["entries"])
        self.remote_nodes = self._list(record["remote"])
        self.sync_vars = [SyncVar(number, *entry) for number, entry in enumerate(record["sync_vars"])]
        self._owner = [node for node in self.states if node is not None and node.is_networked]
        self._remote = [
            node for node in self.remote_nodes if node is not None and node.is_state and node.is_networked
        ]
        self._presence = [entry for entry in self.sync_vars if entry.has_presence_bit]

    def __repr__(self) -> str:
        return f"<Graph {self.index:04X}: {len(self.nodes)} nodes>"

    def _list(self, positions: list) -> list:
        return [self._by_pos.get(position) if position is not None else None for position in positions]

    @property
    def nodes_bits(self) -> int:
        return self.header["m_nodesBitCount"]

    @property
    def states_bits(self) -> int:
        return self.header["m_statesBitCount"]

    @property
    def remote_bits(self) -> int:
        return self.header["m_remoteSyncNodesBitCount"]

    @property
    def sync_vars_bits(self) -> int:
        return self.header["m_syncVarsBitCount"]

    def node(self, position: int) -> Node | None:
        """The node at a position in m_items (what links, lists and {"node": ...} values name)."""
        return self._by_pos.get(position)

    def by_rid(self, rid: int) -> Node | None:
        """The node with an m_nodes index (what event lists name in an owner frame)."""
        return self._by_rid.get(rid)

    def state(self, index: int) -> Node | None:
        """The node of an m_states index (a descriptor's PSIn, an owner event's state)."""
        return self.states[index] if 0 <= index < len(self.states) else None

    def remote_node(self, index: int) -> Node | None:
        """The node of an m_remoteSyncNodes index (what H=0 event lists name)."""
        return self.remote_nodes[index] if 0 <= index < len(self.remote_nodes) else None

    def ref(self, value) -> Node | None:
        """The node a {"node": position} value names."""
        return self.node(value["node"]) if isinstance(value, dict) and "node" in value else None

    def owner_states(self) -> list[Node]:
        """The states of an owner (H=1) frame, in order: m_states minus client-only and server-only. An
        entry DataTool could not read counts as client-only (the only one in the data, 0D24 st75, sits
        under a client-only UI presenter)."""
        return self._owner

    def remote_states(self) -> list[Node]:
        """The states of an H=0 frame, in order: m_remoteSyncNodes entries that are states and neither
        client-only nor server-only."""
        return self._remote

    def presence_vars(self) -> list[SyncVar]:
        """The m_syncVars entries that have a presence bit in a full frame's per-instance list, in order."""
        return self._presence

    def owner_slot(self, state: int) -> int | None:
        """The bit position of an m_states index in an owner frame, None when it has none."""
        node = self.state(state)
        slots = self.owner_states()
        return slots.index(node) if node in slots else None

    def remote_slot(self, remote: int) -> int | None:
        """The bit position of an m_remoteSyncNodes index in an H=0 frame, None when it has none."""
        node = self.remote_node(remote)
        slots = self.remote_states()
        return slots.index(node) if node in slots else None

    def presence_bit(self, var: int) -> int | None:
        """The presence bit of an instance variable, None when the graph does not sync it."""
        for bit, entry in enumerate(self.presence_vars()):
            if entry.var == var:
                return bit
        return None

    def graph_refs(self) -> set[int]:
        """The indexes of the graphs this graph names (sub-scripts, play-script targets, schemas)."""
        found = set()
        _collect_graphs([self.fields, *[node.fields for node in self.nodes]], found)
        return found


def _collect_graphs(value, found: set) -> None:
    number = guid(value)
    if number is not None and number >> 48 == GRAPH_TYPE:
        found.add(number & 0xFFFFFFFFFFFF)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_graphs(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_graphs(item, found)


@cache
def _data() -> dict:
    return json.loads(gzip.decompress(GRAPHS_PATH.read_bytes()))


@cache
def _records() -> dict[int, dict]:
    return {record["index"]: record for record in _data()["graphs"]}


@cache
def graph(index: int) -> Graph | None:
    """The graph with a 16-bit index (0x13C1), or None when the server does not have it."""
    record = _records().get(index)
    return Graph(record) if record is not None else None


def graph_indexes() -> list[int]:
    return sorted(_records())


def bodies() -> dict[int, dict]:
    """Hero body (003 GUID) -> {"hero", "name", "graphs": [{"graph", "m_1EB5A024", ...}], "manager",
    "weapons"}."""
    return {int(key, 16): record for key, record in _data()["bodies"].items()}


def missing() -> list[int]:
    """Graphs a kept graph names that are not in the dump."""
    return list(_data()["missing"])
