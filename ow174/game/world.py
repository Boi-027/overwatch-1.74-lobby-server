"""World frames: what the server sends in a kind-1 datagram after the message channel.

A frame, as the client reads it (0x7FF7896DE110):
    var_a packet frame
    ch0 the message channel (messages.py)
    ch3 var_a ack, 4 bits flags, 1 bit "ping follows"   the client drops its commands below ack - 1
    ch4 1 bit: 1 = keep, 0 resets the entity channel
    ch5 1 bit: the end of an empty list
    7 bits: how many entities follow, then per entity its id and one bit per channel in order; a 0
    ends the entity, a 1 runs that channel's reader, which has presence bits of its own.

The entity channel (ch4, reader 0x7FF78969BBC0) takes 2 bits op: 1 create, 2 update, 3 destroy; a
create or update carries 14 bits of length and that many bits.

A create (0x7FF78969C360, body 0x7FF78969DAB0):
    bit from map: 1 -> 32 bits placeable index, 1 bit transform override (0), then the update body
    else the 003 definition's GUID index, 8 bits flags,
         flags & 0x01: the skin: with 0x40 32 bits of a skin theme (0A6; bit 31 asks for a remap)
         and 1 bit for the theme's second (golden) weapon, else a skin (076) GUID index,
         flags & 0x08: 3 f32 position and a quaternion (x y z w); flags & 0x08 or 0x04: 3 f32 scale,
         flags & 0x02: a movement-state block, then the update body.
    Flag 0x10 is the player's own entity (the client adds its command components when 20302 named
    it), 0x20 adds component 74. The transform floats are thrown away on that path. The skin theme's
    overrides swap the body's models, looks and effects when the client loads it.

The update body (0x7FF78969E5D0 / 0x7FF78969B870): 8 bits count, then per component its index, a
pad to the next byte of the payload, a u8 mask length and the mask (one bit per replicated field in
order), then the values of the fields that are set. Scalars go as whole bytes; a u64 marked as an
id is a byte mask and its non-zero bytes.

The movement-state block (0x7FF789B6F680) is a delta against a zero state. Its field order was read
in IDA; only the command frame, the Euler angles, the position, the velocity and the aim pitch are
sent here. The same block, after one bit (0 = against that zero state), is a ch3 movement record,
which is how a moving entity gets its position. A movement state is only kept when its command
frame is newer than the newest one the entity has (the gate at 0x7FF7896DA7D3).
"""

import math
import struct

from ow174.game import messages
from ow174.game.bits import BitWriter

OP_CREATE, OP_UPDATE, OP_DESTROY = 1, 2, 3

# Create flags.
WITH_SKIN = 0x01
WITH_MOVEMENT = 0x02
WITH_SCALE = 0x04
WITH_TRANSFORM = 0x08
PLAYER_PATH = 0x10
WITH_PARTICIPANT = 0x20
SKIN_THEME = 0x40  # the skin is a skin theme, not a skin

# Replicated fields of the components we send, in order (the client's type objects; checked in IDA
# for 26, 44, 74, 75 and 123). "id" is a u64 sent as a byte mask, "entity" 4 bytes, "blob:N" N raw
# bytes of a nested struct.
COMPONENT_FIELDS = {
    26: ["id"],  # MFilterBitsComponentData: filter bits (team, spectator)
    44: ["entity", "entity", "entity"],  # MECPossessorData: current, view target, primary possessable
    # STUHealthComponent: 16 health, 16 armour, 16 shield parts {u32 on, f32 now, f32 max, 4 bytes,
    # 4 x u8}, then u32, u32, entity, u8, u8.
    51: ["blob:20"] * 48 + ["u32", "u32", "entity", "u8", "u8"],
    # MECGameModeParticipant: portrait frame, s16, slot, u8, u8, s8, can select a hero, u8, u8.
    74: ["id", "s16", "s8", "u8", "u8", "s8", "u8", "u8", "u8"],
    75: ["id"] * 6,  # MECCharacterController: current hero, skin, ..., last valid hero
    # MECGameMode: u32[], the game mode GUID, ...
    114: ["u32[]", "id", "id", "u64", "u64", "u64", "u64", "s32", "f32", "f32", "u32"] + ["u8"] * 9,
    123: ["id"],  # MECCharacterBody: the body's hero
}
SCALAR_FORMATS = {
    "u8": "<B",
    "s8": "<b",
    "s16": "<h",
    "u16": "<H",
    "u32": "<I",
    "s32": "<i",
    "u64": "<Q",
    "f32": "<f",
}

ANGLE_UNITS = 65536 / (2 * math.pi)  # s16 angle units per radian (the commands' yaw and pitch)
POSITION_UNITS = 1024  # movement states hold positions and velocities on a 1/1024 m grid


class RecordWriter(BitWriter):
    """A record that knows where it will sit in the frame payload, for its byte padding."""

    def __init__(self, origin: int) -> None:
        super().__init__()
        self.origin = origin

    def pad(self) -> None:
        missing = (-(self.origin + self.count)) & 7
        if missing:
            self.bits(0, missing)


def _write_value(out: RecordWriter, kind: str, value) -> None:
    if kind == "id":
        number = int(value) & 0xFFFFFFFFFFFFFFFF
        mask, body = 0, bytearray()
        for k in range(8):
            byte = (number >> (8 * k)) & 0xFF
            if byte:
                mask |= 1 << k
                body.append(byte)
        out.pad()
        out.raw(bytes([mask]) + bytes(body))
    elif kind == "entity":
        out.raw(struct.pack("<I", int(value) & 0xFFFFFFFF))
    elif kind.startswith("blob:"):
        data = bytes.fromhex(value) if isinstance(value, str) else bytes(value)
        if len(data) != int(kind[5:]):
            raise ValueError(f"{kind} needs {kind[5:]} bytes, got {len(data)}")
        out.raw(data)
    elif kind.endswith("[]"):
        items = list(value)
        out.pad()
        out.raw(struct.pack("<I", len(items)))
        for item in items:
            out.raw(struct.pack(SCALAR_FORMATS[kind[:-2]], item))
    else:
        out.raw(struct.pack(SCALAR_FORMATS[kind], value))


def _write_components(out: RecordWriter, components: dict) -> None:
    """The update body. `components` maps a component index to its field values; None skips a field."""
    out.bits(len(components), 8)
    for index, values in components.items():
        fields = COMPONENT_FIELDS[index]
        values = list(values) + [None] * (len(fields) - len(values))
        mask = bytearray((len(fields) + 7) // 8)
        for k, value in enumerate(values):
            if value is not None:
                mask[k >> 3] |= 1 << (k & 7)
        out.bits(index, 8)
        out.pad()
        out.bits(len(mask), 8)
        out.raw(bytes(mask))
        for kind, value in zip(fields, values, strict=True):
            if value is not None:
                _write_value(out, kind, value)


def health(now: float, most: float) -> dict:
    """Component 51 with one health part, as the HUD bar reads it."""
    part = struct.pack("<IffII", 1, now, most, 1, 1)
    return {51: [part]}


class Movement:
    """One movement state. yaw and pitch are the commands' s16 angle units."""

    def __init__(self, position, yaw=0, pitch=0, velocity=(0.0, 0.0, 0.0), flags=0) -> None:
        self.position = tuple(position)
        self.yaw = int(yaw)
        self.pitch = int(pitch)
        self.velocity = tuple(velocity)
        self.flags = int(flags)


def _selector(out: BitWriter, value: int, width_index: int, width: int) -> None:
    """A present value at the width that a unary selector picks: 1 = the first width, 01 the second,
    001 the third."""
    out.bit(1)
    for _ in range(width_index):
        out.bit(0)
    out.bit(1)
    out.bits(value & ((1 << width) - 1), width)


def write_movement(out: BitWriter, state: Movement, frame_back: int | None) -> None:
    """The movement-state block. `frame_back` None puts the state at the packet frame (ch3); a number
    puts it that many frames before the create path's base of 0xFFFFFFFF."""
    flags = state.flags & 0xFFFFFFFF
    if flags:
        out.bit(1)
        for k in range(4):  # four optional bytes, low first
            byte = (flags >> (8 * k)) & 0xFF
            out.bit(1 if byte else 0)
            if byte:
                out.bits(byte, 8)
    else:
        out.bit(0)
    out.bit(0)  # +4: positions and velocity at 1/1024 m
    if frame_back is None:
        out.bit(0)  # +8: the command frame is the base
    elif 1 <= frame_back < 16:
        out.bit(1)
        out.bit(1)
        out.bits(frame_back, 4)
    else:
        out.bit(1)
        out.bits(0, 2)  # no width picked: 32 bits follow
        out.bits(frame_back & 0xFFFFFFFF, 32)
    out.bit(0)  # +12
    pitch = state.pitch & 0xFFFF
    if pitch:
        _selector(out, pitch, 2, 16)  # +66 the aim pitch; widths 8, 10, 16
    else:
        out.bit(0)
    out.bits(0, 8)  # +16, +20, +24, +28, then the two of +36/+40 and +32, +33 kept
    for angle in (state.yaw & 0xFFFF, 0, 0):  # Euler yaw, pitch, roll; widths 8, 10, 16
        if angle:
            _selector(out, angle, 2, 16)
        else:
            out.bit(0)
    for axis in state.position:  # always three; widths 8, 10, 24
        _selector(out, round(axis * POSITION_UNITS), 2, 24)
    velocity = [round(axis * POSITION_UNITS) for axis in state.velocity]
    if any(velocity):
        out.bit(1)
        for axis in velocity:
            if axis:
                _selector(out, axis, 2, 24)
            else:
                out.bit(0)
    else:
        out.bit(0)
    out.bits(0, 9)  # nine optional parts
    out.bit(0)  # no parent: world space
    out.bits(0, 5)  # +52, +60, +56, +384, +832


def create(
    origin: int,
    definition: int,
    components: dict,
    flags: int = WITH_TRANSFORM | WITH_SCALE,
    position=(0.0, 0.0, 0.0),
    rotation=(0.0, 0.0, 0.0, 1.0),
    movement: Movement | None = None,
    frame_back: int = 1,
    skin: tuple[int, bool] = (0, False),
) -> RecordWriter:
    """A create record for an entity of a 003 definition. `skin` is (skin theme GUID, golden weapon);
    theme 0 leaves the definition's own look."""
    if movement is not None:
        flags |= WITH_MOVEMENT
    theme, golden = skin
    if theme:
        flags |= WITH_SKIN | SKIN_THEME
    out = RecordWriter(origin)
    out.bit(0)
    out.guid_index(definition)
    out.bits(flags, 8)
    if theme:
        out.bits(theme & 0x7FFFFFFF, 32)  # bit 31 off: no remap
        out.bit(golden)
    if flags & WITH_TRANSFORM:
        for value in (*position, *rotation):
            out.f32(value)
    if flags & (WITH_TRANSFORM | WITH_SCALE):
        for _ in range(3):
            out.f32(1.0)
    if movement is not None:
        write_movement(out, movement, frame_back)
    _write_components(out, components)
    return out


def create_placeable(origin: int, index: int) -> RecordWriter:
    """A create record for a map placeable the server owns: the client takes the definition and the
    transform from its map data. The entity id must be 0x80000000 | n."""
    out = RecordWriter(origin)
    out.bit(1)
    out.bits(index, 32)
    out.bit(0)  # keep the map's transform
    _write_components(out, {})
    return out


def update(origin: int, components: dict) -> RecordWriter:
    out = RecordWriter(origin)
    _write_components(out, components)
    return out


def movement_record(state: Movement) -> BitWriter:
    """A ch3 record: 12 bits of length (counting themselves), a 0 bit, the movement state at the packet
    frame."""
    body = BitWriter()
    body.bit(0)
    write_movement(body, state, None)
    out = BitWriter()
    out.bits(12 + body.count, 12)
    out.append(body)
    return out


class EntityUpdate:
    """What one frame says about one entity: a statescript chunk (ch1), a movement record (ch3) and/or
    an entity-channel record (ch4)."""

    def __init__(self, entity: int, op: int = 0, build=None, movement: Movement | None = None, chunk=None):
        self.entity = entity
        self.op = op
        self.build = build  # origin -> RecordWriter, for create and update
        self.movement = movement
        self.chunk = chunk  # a statescript.chunk()
        self.chunk_last = 0  # the chunk's last frame, to know when the client has it
        self.stream = None  # the statescript.Stream told when the chunk arrives
        self.resend = True  # send it again when its datagram is lost


def _write_entity(out: BitWriter, item: EntityUpdate) -> None:
    out.entity_id(item.entity)
    out.bit(1)  # ch0 reads nothing but takes its bit
    if not item.op and item.movement is None and item.chunk is None:
        out.bit(0)
        return
    out.bit(1)  # ch1 runs
    if item.chunk is not None:
        out.bit(1)  # a chunk follows
        out.bit(0)  # a bit the reader skips
        out.append(item.chunk)
    else:
        out.bit(0)
    if not item.op and item.movement is None:
        out.bit(0)
        return
    out.bits(0b001, 3)  # ch2 runs, no event, no state
    out.bit(1)  # ch3 runs
    if item.movement is not None:
        out.bit(1)
        out.append(movement_record(item.movement))
    else:
        out.bit(0)
    if not item.op:
        out.bit(0)
        return
    out.bit(1)  # ch4 runs
    out.bits(item.op, 2)
    if item.op != OP_DESTROY:
        record = item.build(out.count + 14)
        if record.count >= 1 << 14:
            raise ValueError("an entity record is at most 16383 bits")
        out.bits(record.count, 14)
        out.append(record)
    out.bit(0)  # ch5: nothing, the end of this entity


def frame(tick: int, ack: int, reliable=(), unreliable=(), entities=()) -> bytes:
    """A kind-1 payload. The messages are messages.write_channel's; they are written into the frame
    itself, because their padding is to a byte of the payload."""
    out = BitWriter()
    out.var_a(tick)
    messages.write_channel(out, reliable, unreliable)
    out.var_a(ack)
    out.bits(0, 4)
    out.bit(0)  # no ping time
    out.bit(1)  # ch4: keep
    out.bit(0)  # ch5: empty list
    entities = list(entities)
    if len(entities) > 127:
        raise ValueError("at most 127 entities per frame")
    out.bits(len(entities), 7)
    for item in entities:
        _write_entity(out, item)
    return out.getvalue()
