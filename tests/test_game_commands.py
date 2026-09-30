"""The client's input record: its commands, as the client writes them."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game.bits import BitReader, BitWriter
from ow174.game.commands import CommandQueue, read_commands
from ow174.game.mover import FlatMover


def record(first_frame: int) -> bytes:
    """Two commands: W held facing yaw 0, then the same with the view turned by 300 units."""
    body = BitWriter()
    body.bits(35, 16)  # latency ms
    body.bits(0, 8)  # key flags
    body.bits(2, 8)  # two commands
    body.bits(first_frame, 32)
    body.bits(0b111, 3)  # throttles present, compact, a direction
    body.bits(2, 3)  # direction 2 = straight forward
    body.signed(0, 16)  # yaw
    body.signed(-500, 16)  # pitch
    body.bits(0, 24)  # buttons
    body.bits(0, 8)  # action
    body.bits(35, 16)  # extra
    body.bit(0)  # the next frame
    body.bit(1)  # it differs
    body.bit(0)  # the same throttles
    body.bit(0)  # the same pitch
    body.bit(1)  # yaw changes, by a 10-bit amount (selector 0 then 1)
    body.bits(0b10, 2)
    body.signed(300, 10)
    body.bits(0, 3)  # extra, buttons, action unchanged
    out = BitWriter()
    out.bit(1)
    out.bits(body.count, 32)
    out.append(body)
    return out.getvalue()


class CommandTests(unittest.TestCase):
    def test_a_record_with_a_full_and_a_changed_command(self):
        commands = read_commands(BitReader(record(5000)))
        self.assertEqual([command.frame for command in commands], [5000, 5001])
        self.assertEqual([(command.forward, command.right) for command in commands], [(127, 0), (127, 0)])
        self.assertEqual([command.yaw for command in commands], [0, 300])
        self.assertEqual(commands[1].pitch, -500)

    def test_each_frame_is_handed_over_once(self):
        queue = CommandQueue()
        first = read_commands(BitReader(record(5000)))
        again = read_commands(BitReader(record(5001)))
        self.assertEqual(len(queue.new(first)), 2)
        self.assertEqual([command.frame for command in queue.new(again)], [5002])

    def test_forward_at_yaw_0_walks_along_z(self):
        mover = FlatMover((0.0, 1.0, 0.0))
        for command in read_commands(BitReader(record(1)))[:1]:
            mover.step(command, 1.0)
        self.assertAlmostEqual(mover.position[0], 0.0)
        self.assertAlmostEqual(mover.position[2], 5.5)
        self.assertEqual(mover.position[1], 1.0)


if __name__ == "__main__":
    unittest.main()
