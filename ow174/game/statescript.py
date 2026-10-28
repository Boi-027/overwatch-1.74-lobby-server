"""Statescript on ch1: frames that put graph instances on an entity and set their states and
variables. The client runs the graphs; the server says which instances exist and what is on.

Read in the client (our IDA; the chunk reader 0x7FF7898A6450, the frame 0x7FF78991E3C0):
- A chunk is `w_u32 first | w_var span | bit fragmented | w_var size in bits | payload`. first 0 is a
  full frame. The client keeps the highest `last` (first + span) it applied and drops chunks at or
  below it, so an unchanged chunk can be sent again safely.
- The payload starts `bit lead | bit H`; with H (an owner frame): `bit C | w_var CmFD`, and in a delta
  also `w_var LBSS` (the body's length). An owner frame is skipped unless its CmFD is larger than the
  last one (unsigned, so 0 never applies).
- A full frame lists the instances (w_u16 index steps, each with a descriptor: 16 bits graph, bit other
  entity, bit flag, bit parent -> w_var parent instance and w_var parent STATE index, bit references),
  then the entity's variables, then per instance its variables, one bit per state, and in an owner
  frame its event list; then the frame's event list. It destroys the network instances it does not
  list and first clears the variables the network set, so every full frame carries everything.
- In an owner frame the states are the graph's m_states minus the client-only and server-only ones.
  A SubScript state that is on carries its child's instance id (w_u16) and a bit.
- Instances the server makes never run their own Entry nodes (only client-only ones run), so the
  server sends what those would have done: the states that are on and the variables.
"""

import struct
from dataclasses import dataclass, field

from ow174.game.bits import BitWriter
from ow174.game.world import EntityUpdate


@dataclass(frozen=True)
class Graph:
    index: int  # the 01B graph's 16-bit index
    owner_states: int  # state bits in an owner frame
    sync_vars: int  # per-instance variable presence bits


# From the graph data (the counts match what worked in ProCore's live tests).
CONTROLLER = Graph(0x13C1, 43, 12)  # the player's controller in the Practice Range mode
PVP_CONTROLLER = Graph(0x0C90, 118, 29)  # the controller of both teams in the PvP modes
HUD = Graph(0x20E4, 1, 0)  # 13C1's weapon and ability HUD (0C90 starts its own)
HERO_SELECT_HOST = Graph(0x288A, 6, 0)
HERO_SELECT = Graph(0x288B, 30, 4)  # presents the hero select screen 008C.05A
TEAM_ENTRY = Graph(0x288D, 0, 0)  # client-only: posts its entity to the local player's team list


class Value:
    tag: int

    def write(self, out: BitWriter) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class Bool(Value):
    value: bool

    def write(self, out: BitWriter) -> None:
        out.bits(0, 4)
        out.bit(self.value)


@dataclass(frozen=True)
class Int(Value):
    value: int

    def write(self, out: BitWriter) -> None:
        out.bits(4, 4)
        out.bits(self.value & 0xFFFFFFFF, 32)


@dataclass(frozen=True)
class Float(Value):
    value: float

    def write(self, out: BitWriter) -> None:
        out.bits(8, 4)
        out.bits(struct.unpack("<I", struct.pack("<f", self.value))[0], 32)


@dataclass(frozen=True)
class Asset(Value):
    """A GUID read as an asset (a hero, for example): its type bits and its low 32 bits."""

    guid: int

    def write(self, out: BitWriter) -> None:
        out.bits(9, 4)
        out.bit(1)
        out.bits(self.guid >> 48, 16)
        if self.guid >> 48:
            out.w_var(self.guid & 0xFFFFFFFF)


@dataclass(frozen=True)
class Entity(Value):
    entity: int

    def write(self, out: BitWriter) -> None:
        out.bits(13, 4)
        out.bit(1)
        out.bits(self.entity & 0xFFFFFFFF, 32)


def write_variable(out: BitWriter, value: Value) -> None:
    value.write(out)
    out.bit(0)  # no bindings


def subscript(child: int) -> BitWriter:
    """The payload of a SubScript state that is on: its child instance."""
    out = BitWriter()
    out.w_u16(child)
    out.bit(0)
    return out


def switch_data(branch: int = 0) -> BitWriter:
    """The state data of an active BooleanSwitch: one branch bit (the reader at 0xa099e0). A bare
    active bit is not enough: the client then parses the following instance's data as states and the
    whole frame tail shifts."""
    out = BitWriter()
    out.bit(branch)
    return out


def stack_data() -> BitWriter:
    """The state data of an active Stack: two bits (the value they encode is 0). Like
    switch_data, the bare active bit desyncs the frame."""
    out = BitWriter()
    out.bits(0, 2)
    return out


def sender_data() -> BitWriter:
    """The state data of an active GameMessageEntry (the sender class): a compact u16 follows the
    active bit, whose zero form is one 0 bit."""
    out = BitWriter()
    out.bit(0)
    return out


def volley_data(start_time: int = 0) -> BitWriter:
    """The state data of a WeaponVolley entry (reader 0xd60400): the start time as a sign-magnitude
    int - sign bit, then a varint width (one 0 bit means 8, 1-0 means 16, 1-1 means 32) and the
    value - a flag (a second int follows when set), a flag (a part follows when set) and six more
    bits. A zero start is 18 bits and is the form the live firing runs sent. The data belongs to
    the has-data bit, not to the active bit: a stopped volley must send it as well, or the frame
    tail shifts."""
    out = BitWriter()
    out.bit(0)  # sign
    out.w_var(start_time)
    out.bit(0)  # no second int
    out.bit(0)  # no part
    out.bits(0, 6)
    return out


def button_data(count: int = 0) -> BitWriter:
    """The state data of a LogicalButton entry: a 4-bit press counter."""
    out = BitWriter()
    out.bits(count & 0xF, 4)
    return out


def expiry_data(ticks: int = 0) -> BitWriter:
    """The state data of the unnamed two-bit class (0x691BFA55, reader 0xc91150): one
    sign-magnitude int. The reader subtracts it from the owner snapshot's frame count and stores
    the result as the state's expiry tick, so the number is a delay in frames; zero is an
    immediate expiry and the smallest safe payload (ten bits)."""
    out = BitWriter()
    out.bit(0)  # sign: positive
    out.w_var(ticks)
    return out


def anim_data(reference: tuple[int, int, int] | None = None) -> BitWriter:
    """The state data of an active Anim (reader 0xcbd370): an optional state reference (present
    bit, then the instance id as a varint - one 0 bit means 4 bits, 1-0 means 8, 1-1 means 16 -
    and a state index of the referenced graph's stored width), a 3-bit field, and a gate bit
    (set: one more bit, set: a sign-magnitude int follows). With no reference the state plays
    nothing - the form a static picture sends; `reference` is (instance, width_bits, state) for
    the graph whose CosmeticEntity should host the animation."""
    out = BitWriter()
    if reference is None:
        out.bit(0)
    else:
        instance, width, state = reference
        out.bit(1)
        if instance < 16:
            out.bit(0)
            out.bits(instance, 4)
        elif instance < 256:
            out.bits(0b01, 2)
            out.bits(instance, 8)
        else:
            out.bits(0b11, 2)
            out.bits(instance, 16)
        out.bits(state, width)
    out.bits(0, 3)  # the field
    out.bit(0)  # gate: no trailing int
    return out


@dataclass(frozen=True)
class HasData:
    """A state entry of a class whose form is has-data + active + state data (WeaponVolley,
    LogicalButton; see DATA_STATES below and loadout.HAS_DATA_STATES). The payload is read by
    the client whenever has-data is set, active or not, so stopping such a state means sending
    it again inactive, not dropping the entry."""

    payload: BitWriter
    active: bool = True


# The state-data forms of the entries in a full snapshot (the native predicate at 0xf5e960 over
# the graph metadata's state types): the classes in DATA_STATES take a bare active bit plus their
# own small payload - the BooleanSwitch's branch bit, the Stack's two bits, the sender's compact
# u16 and the Anim's optional reference. The classes in HAS_DATA_STATES take the two-bit has-data
# form instead. The predicate's fourth true class, 0x2F07046F, has no state in any graph of our
# data, so its reader can stay unread. Everything else needs no data bits.
DATA_STATES = {
    "STUStatescriptStateBooleanSwitch": switch_data,
    "STUStatescriptStateStack": stack_data,
    "STUStatescriptStateGameMessageEntry": sender_data,
    "STUStatescriptStateAnim": anim_data,
}

HAS_DATA_STATES = {
    "STUStatescriptStateWeaponVolley": volley_data,
    "STUStatescriptStateLogicalButton": button_data,
    "STU_691BFA55": expiry_data,  # one sign-magnitude int: the delay until the state expires
}


def active_data(cls: str):
    """The state-data payload builder this class needs when active, or None when the bare bit is
    enough for it. The has-data classes take the entry form HasData(payload) instead - see
    HAS_DATA_STATES for which builder belongs to which class."""
    return DATA_STATES.get(cls)


def has_data_entry(cls: str, **fields):
    """The HasData entry for a two-bit class, built with the class's own payload (start_time for
    a volley, count for a button), or None for a class without a known two-bit form."""
    builder = HAS_DATA_STATES.get(cls)
    return None if builder is None else HasData(builder(**fields))


@dataclass
class Instance:
    index: int
    graph: Graph
    parent: tuple[int, int] | None = None  # (parent instance, the parent graph's state index)
    presence: dict[int, Value] = field(default_factory=dict)  # presence bit -> value
    extra: dict[int, Value] = field(default_factory=dict)  # variable id -> value
    active: dict[int, BitWriter | None] = field(default_factory=dict)  # owner state bit -> payload


def _descriptor(out: BitWriter, instance: Instance) -> None:
    out.bits(instance.graph.index, 16)
    out.bit(0)  # the instance lives on this entity
    out.bit(0)  # flag
    if instance.parent is None:
        out.bit(0)
    else:
        out.bit(1)
        out.w_var(instance.parent[0])
        out.w_var(instance.parent[1])
    out.bit(0)  # no references


def _variables(out: BitWriter, variables: dict[int, Value]) -> None:
    for number, value in variables.items():
        out.w_var(number)
        write_variable(out, value)
    out.w_var(0)


def owner_full_frame(
    cmfd: int, instances: list[Instance], entity_vars: dict[int, Value], lbss: int | None = None
) -> BitWriter:
    """An owner (H=1) full frame. `cmfd` must be larger than any CmFD sent to this entity before.
    `lbss` (the "bits to skip if this frame was already applied" varint) is required when the frame
    is wrapped in a chunk at a non-zero base: a chunk below the manager's last applied frame must
    say how far to jump over the body, and without it the skip lands mid-frame."""
    if cmfd < 1:
        raise ValueError("CmFD 0 is always stale")
    out = BitWriter()
    out.bit(0)  # lead
    out.bit(1)  # H
    out.bit(0)  # C
    out.w_var(cmfd)
    if lbss is not None:
        out.w_var(lbss)
    last = 0
    for instance in sorted(instances, key=lambda item: item.index):
        out.w_u16(instance.index - last)
        last = instance.index
        _descriptor(out, instance)
    out.w_u16(0)
    if entity_vars:
        out.bit(1)
        _variables(out, entity_vars)
    else:
        out.bit(0)
    for instance in sorted(instances, key=lambda item: item.index):
        for bit in range(instance.graph.sync_vars):
            value = instance.presence.get(bit)
            out.bit(value is not None)
            if value is not None:
                write_variable(out, value)
        if instance.extra:
            out.bit(1)
            _variables(out, instance.extra)
        else:
            out.bit(0)
        for bit in range(instance.graph.owner_states):
            entry = instance.active.get(bit)
            if isinstance(entry, HasData):
                out.bit(1)  # has-data
                out.bit(entry.active)
                out.append(entry.payload)  # read whenever has-data is set, active or not
            else:
                on = bit in instance.active
                out.bit(on)
                if on and entry is not None:
                    out.append(entry)
        out.bits(0b11, 2)  # the instance's event list ends (code 3)
    out.bits(0, 2)  # the frame's event lists: none
    return out


def graph_create_frame(instances: list[Instance]) -> BitWriter:
    """The graph-info frame that rides an entity's create: the instance list with plain create
    descriptors, no variables, no states, CmFD 0 (the interpreter takes the client's own tick). It
    only creates the instances; the states and variables go in the owner full snapshot that follows.
    Listing states here too creates them twice, and an instance the create frame does not mention is
    not created by the later snapshot."""
    out = BitWriter()
    out.bit(0)  # lead
    out.bit(1)  # H
    out.bit(0)  # C
    out.w_var(0)  # no CmFD: the client tick is stored as the base
    last = 0
    for instance in sorted(instances, key=lambda item: item.index):
        out.w_u16(instance.index - last)
        last = instance.index
        _descriptor(out, instance)
    out.w_u16(0)
    out.bit(0)  # no entity variables
    out.bits(0, 2)  # no frame events
    return out


def variables_frame(entity_vars: dict[int, Value]) -> BitWriter:
    """A plain (H=0) full frame with no instances, only entity variables: how an entity without
    graphs of its own, such as the game mode entity, gets values that other graphs read from it."""
    out = BitWriter()
    out.bit(0)  # lead
    out.bit(0)  # H
    out.w_u16(0)  # no instances
    if entity_vars:
        out.bit(1)
        _variables(out, entity_vars)
    else:
        out.bit(0)
    out.bits(0, 2)  # the frame's event lists: none
    return out


def owner_ack(cmfd: int) -> BitWriter:
    """An owner delta that only moves CmFD on: the client then replays fewer of its own commands when it
    rolls its prediction back."""
    out = BitWriter()
    out.bit(0)  # lead
    out.bit(1)  # H
    out.bit(0)  # C
    out.w_var(cmfd)
    out.w_var(7)  # LBSS: the body below, up to the "more" bit
    out.w_u16(0)  # no instances
    out.bit(0)  # no entity variables
    out.bit(0)  # no instance events
    out.bit(0)  # no more sub-frames
    out.bits(0, 2)  # no frame events
    return out


def chunk(payload: BitWriter, first: int, span: int) -> BitWriter:
    out = BitWriter()
    out.w_u32(first)
    out.w_var(span)
    out.bit(0)  # not fragmented
    out.w_var(payload.count)
    out.append(payload)
    return out


class Stream:
    """One entity's statescript as one client gets it: the frame numbers of the chunks sent and
    delivered, and the last CmFD. The client applies a chunk only when its last frame is new to it and
    drops a queued chunk whose range a newer chunk covers, so every chunk gets the next number and a
    chunk with data must arrive before an ack-only delta may cover it."""

    def __init__(self, entity: int) -> None:
        self.entity = entity
        self.last = 0  # the last frame number sent
        self.delivered = 0  # the highest last frame the client has
        self.data_last = 0  # the last chunk that carries data, not only an ack
        self.cmfd = 0
        self.next_ack = 0.0  # when the next ack-only delta may go

    def next_cmfd(self, newest: int) -> int:
        """The client skips an owner frame whose CmFD is not larger than the last one, and the
        interpreter requires it to exceed the tick stamp the graph-create frame left (set to the
        client's own tick): frames numbered below that tick leave the game "waiting for updates
        that never came". The working rule is therefore CmFD = max(prev + 1, newest + 8)."""
        self.cmfd = max(self.cmfd + 1, newest + 8)
        return self.cmfd

    @property
    def data_in_flight(self) -> bool:
        return self.delivered < self.data_last

    def full_frame(self, frame: BitWriter) -> EntityUpdate:
        """A full frame; sent again unchanged when its datagram is lost."""
        self.last += 1
        self.data_last = self.last
        return self._update(chunk(frame, 0, self.last), resend=True)

    def ack_delta(self, frame: BitWriter) -> EntityUpdate:
        """A delta that only moves CmFD on. A later one covers it, so a lost one is not sent again."""
        first = self.delivered + 1
        self.last += 1
        return self._update(chunk(frame, first, self.last - first), resend=False)

    def arrived(self, last: int) -> None:
        self.delivered = max(self.delivered, last)

    def _update(self, data: BitWriter, resend: bool) -> EntityUpdate:
        update = EntityUpdate(self.entity, chunk=data)
        update.chunk_last = self.last
        update.stream = self
        update.resend = resend
        return update
