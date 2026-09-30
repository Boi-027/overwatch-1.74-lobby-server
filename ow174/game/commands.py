"""The client's input record: what it pressed, frame by frame. It ends every client datagram.

The client never sends its position (only the message channel and this record have writers,
0x7FF7896D0930), so a server that shows a player to others has to move the player itself.

Layout (writer 0x7FF789B3DDC0, command writer 0x7FF789B3DD00):
    1 bit present, u32 length in bits, then u16 latency ms, u8 key flags, u8 count (1..32), and the
    commands oldest first. The first is complete:
        u32 frame | throttles | s16 yaw | s16 pitch | 24 bits buttons | u8 action |
        [buttons & 0x10000: u8 sub-frame, s16, s16] | u16 extra
    Each later one is a change against the one before:
        frame + 1 if bit 0, else + U(2, 6 bits or 32) | bit 0 = the same command again, else
        throttles | bit -> pitch += S(8, 10) | bit -> yaw += S(8, 10) | bit -> extra += S(5, 8) |
        bit -> buttons | bit -> action | [sub-frame data]
    Throttles: bit present; bit compact: 1 -> bit (0 = none, 1 = 3 bits direction), else bit ->
    s8 right, bit -> s8 forward.
Angles are s16 units of 2 pi / 65536; forward is (sin yaw, 0, cos yaw) and right (-cos yaw, 0,
sin yaw). The pitch is positive looking down. One command frame is one tick of the game.
"""

from dataclasses import dataclass

from ow174.game.bits import BitReader

MAX_COMMANDS = 32
SUBFRAME = 0x10000
# The compact directions as (forward, right).
DIRECTIONS = ((0, 127), (127, 127), (127, 0), (127, -127), (0, -127), (-127, -127), (-127, 0), (-127, 127))

FIRE, SECONDARY, JUMP, CROUCH = 0x01, 0x02, 0x40, 0x80


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    frame: int
    forward: int = 0
    right: int = 0
    yaw: int = 0
    pitch: int = 0
    buttons: int = 0
    action: int = 0


def _unsigned(reader: BitReader, widths, full: int) -> int:
    for width in widths:
        if reader.bit():
            return reader.bits(width)
    return reader.bits(full)


def _signed(reader: BitReader, widths) -> int:
    for width in widths:
        if reader.bit():
            return reader.signed(width)
    return reader.signed(16)


def _throttles(reader: BitReader, forward: int, right: int) -> tuple[int, int]:
    if not reader.bit():
        return forward, right
    if reader.bit():
        if not reader.bit():
            return 0, 0
        return DIRECTIONS[reader.bits(3)]
    if reader.bit():
        right = reader.signed(8)
    if reader.bit():
        forward = reader.signed(8)
    return forward, right


def _skip_subframe(reader: BitReader, buttons: int) -> None:
    if buttons & SUBFRAME:
        reader.bits(8 + 16 + 16)


def read_commands(reader: BitReader) -> list[Command]:
    """The commands of the record at the reader, oldest first; [] when there is none."""
    try:
        if not reader.left() or not reader.bit():
            return []
        length = reader.bits(32)
        if length > reader.left():
            raise InputError(f"the record says {length} bits, {reader.left()} are left")
        reader.bits(16 + 8)  # latency, key flags
        count = reader.bits(8)
        if not 1 <= count <= MAX_COMMANDS:
            raise InputError(f"{count} commands")
        frame = reader.bits(32)
        forward, right = _throttles(reader, 0, 0)
        yaw, pitch = reader.signed(16), reader.signed(16)
        buttons, action = reader.bits(24), reader.bits(8)
        _skip_subframe(reader, buttons)
        reader.bits(16)  # extra (the latency again)
        commands = [Command(frame, forward, right, yaw, pitch, buttons, action)]
        for _ in range(count - 1):
            last = commands[-1]
            frame = last.frame + (_unsigned(reader, (2, 6), 32) if reader.bit() else 1)
            if not reader.bit():
                commands.append(
                    Command(frame, last.forward, last.right, last.yaw, last.pitch, last.buttons, last.action)
                )
                continue
            forward, right = _throttles(reader, last.forward, last.right)
            pitch, yaw, buttons, action = last.pitch, last.yaw, last.buttons, last.action
            if reader.bit():
                pitch += _signed(reader, (8, 10))
            if reader.bit():
                yaw += _signed(reader, (8, 10))
            if reader.bit():
                _signed(reader, (5, 8))  # extra
            if reader.bit():
                buttons = reader.bits(24)
            if reader.bit():
                action = reader.bits(8)
            _skip_subframe(reader, buttons)
            commands.append(Command(frame, forward, right, yaw, pitch, buttons, action))
        return commands
    except EOFError:
        raise InputError("the record runs past the datagram") from None


class CommandQueue:
    """Hands each command frame of the overlapping windows over once, oldest first."""

    RESTART_GAP = 1024  # a newest frame this far below the last one means the client started over

    def __init__(self) -> None:
        self.last_frame: int | None = None

    def new(self, commands: list[Command]) -> list[Command]:
        if not commands:
            return []
        if self.last_frame is not None and commands[-1].frame + self.RESTART_GAP < self.last_frame:
            self.last_frame = None
        fresh = [
            command for command in commands if self.last_frame is None or command.frame > self.last_frame
        ]
        if fresh:
            self.last_frame = fresh[-1].frame
        return fresh
