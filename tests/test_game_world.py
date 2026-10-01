"""World frames and entity records, read back the way the client reads them."""

import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game import world
from ow174.game.bits import BitReader, BitWriter
from ow174.game.world import OP_CREATE, EntityUpdate, Movement


def read_frame_start(payload: bytes):
    """The frame up to its entities: (reader, tick, ack, entity count)."""
    reader = BitReader(payload)
    tick = reader.var_a()
    assert reader.bit() == 0  # no messages
    ack = reader.var_a()
    assert reader.bits(4) == 0 and reader.bit() == 0  # flags, no ping
    assert reader.bit() == 1 and reader.bit() == 0  # ch4 keep, ch5 empty
    return reader, tick, ack, reader.bits(7)


class WorldTests(unittest.TestCase):
    def test_a_pose_record_is_as_long_as_procores_live_one(self):
        # ProCore's pose-only movement record (position and yaw) was 148 bits with its 13 framing bits.
        record = world.movement_record(Movement((54.56507, 1.0, 42.14788), yaw=-31000))
        self.assertEqual(record.count, 148)
        self.assertEqual(record.value & 0xFFF, 148)  # the length counts itself

    def test_a_frame_with_a_placeable_create(self):
        entity = 0x80000088
        payload = world.frame(
            1234,
            1234,
            (),
            (),
            [EntityUpdate(entity, OP_CREATE, lambda origin: world.create_placeable(origin, 0x88))],
        )
        reader, tick, ack, count = read_frame_start(payload)
        self.assertEqual((tick, ack, count), (1234, 1234, 1))
        self.assertEqual(reader.entity_id(), entity)
        self.assertEqual([reader.bit() for _ in range(7)], [1, 1, 0, 1, 0, 0, 1])  # ch0..ch3, empty
        self.assertEqual(reader.bit(), 0)  # ch3: no movement record
        self.assertEqual(reader.bit(), 1)  # ch4 runs
        self.assertEqual(reader.bits(2), OP_CREATE)
        length = reader.bits(14)
        start = reader.pos
        self.assertEqual((reader.bit(), reader.bits(32), reader.bit(), reader.bits(8)), (1, 0x88, 0, 0))
        self.assertEqual(reader.pos - start, length)
        self.assertEqual(reader.bit(), 0)  # ch5

    def test_component_values_start_on_a_byte_of_the_payload(self):
        payload = world.frame(
            7,
            7,
            (),
            (),
            [
                EntityUpdate(
                    0xA0000100,
                    OP_CREATE,
                    lambda origin: world.create(origin, 0x0400000000000001, {26: [0x800000]}),
                )
            ],
        )
        reader, _, _, _ = read_frame_start(payload)
        reader.entity_id()
        reader.bits(9)  # the channel bits, ch3's empty record bit and ch4's run bit
        self.assertEqual(reader.bits(2), OP_CREATE)
        reader.bits(14)
        self.assertEqual(reader.bit(), 0)  # not a placeable
        self.assertEqual((reader.bit(), reader.bits(12)), (1, 1))  # the definition's index, short form
        flags = reader.bits(8)
        self.assertEqual(flags, world.WITH_TRANSFORM | world.WITH_SCALE)
        floats = [struct.unpack("<f", struct.pack("<I", reader.bits(32)))[0] for _ in range(10)]
        self.assertEqual(floats[-3:], [1.0, 1.0, 1.0])
        self.assertEqual((reader.bits(8), reader.bits(8)), (1, 26))  # one component: 26
        reader.align()  # the pad is to the payload's byte
        self.assertEqual((reader.bits(8), reader.bits(8)), (1, 1))  # mask length, mask
        reader.align()  # the u64 is a byte mask with the non-zero bytes, aligned
        self.assertEqual((reader.bits(8), reader.bits(8)), (0b100, 0x80))

    def test_the_movement_state_keeps_the_position_on_the_1024_grid(self):
        out = BitWriter()
        world.write_movement(out, Movement((1.5, -2.25, 3.0)), None)
        reader = BitReader(out.getvalue())
        self.assertEqual([reader.bit() for _ in range(5)], [0, 0, 0, 0, 0])  # flags, +4, frame, +12, pitch
        reader.bits(8)
        self.assertEqual([reader.bit() for _ in range(3)], [0, 0, 0])  # no Euler angles
        axes = []
        for _ in range(3):
            self.assertEqual([reader.bit() for _ in range(4)], [1, 0, 0, 1])  # present, the 24-bit width
            axes.append(reader.signed(24) / 1024)
        self.assertEqual(axes, [1.5, -2.25, 3.0])
        self.assertTrue(math.isclose(axes[0], 1.5))

    def test_a_skin_theme_follows_the_flags_byte(self):
        # Doomfist (body 012F) in skin theme 467C with his golden gauntlet: flags 0x01 | 0x40 and the
        # transform flags, then 32 bits of the theme (bit 31, the remap, off) and the golden bit.
        record = world.create(0, 0x040000000000012F, {}, skin=(0x0A5000000000467C, True))
        reader = BitReader(record.getvalue())
        self.assertEqual(reader.bit(), 0)  # not a map placeable
        self.assertEqual((reader.bit(), reader.bits(12)), (1, 0x12F))  # the definition's 12-bit index
        self.assertEqual(reader.bits(8), world.WITH_SKIN | world.SKIN_THEME | world.WITH_TRANSFORM | 0x04)
        self.assertEqual((reader.bits(32), reader.bit()), (0x467C, 1))
        floats = [struct.unpack("<f", struct.pack("<I", reader.bits(32)))[0] for _ in range(10)]
        self.assertEqual(floats, [0.0] * 6 + [1.0] * 4)  # position, rotation (0, 0, 0, 1), scale


if __name__ == "__main__":
    unittest.main()
