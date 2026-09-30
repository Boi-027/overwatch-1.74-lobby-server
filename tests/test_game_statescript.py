"""Statescript frames, bit for bit against frames worked out from the client's readers."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game import heroselect
from ow174.game.bits import BitWriter
from ow174.game.statescript import Graph, Instance, chunk, owner_ack, owner_full_frame
from ow174.game.world import EntityUpdate, _write_entity


def bitstring(bits: BitWriter) -> str:
    return "".join(str((bits.value >> k) & 1) for k in range(bits.count))


class StatescriptTests(unittest.TestCase):
    def test_procores_live_game_mode_frame(self):
        # 13C0 on the game mode entity, as ProCore sent it live: owner frame, CmFD 10000000, 20 state bits.
        frame = owner_full_frame(10000000, [Instance(1, Graph(0x13C0, 20, 3))], {})
        self.assertEqual(
            bitstring(frame),
            "0101100000001011010010001100100000000010000000001111001000000000"
            "00000000000000000000000000001100",
        )

    def test_the_hero_select_entity_blocks(self):
        # The controller with HeroSelect on, 288A -> 288B (v25377 on: the skin selector) and 288D (the
        # team list entry), 13C1's HUD 20E4, hero select open, CmFD 123456, the first chunk: the entity
        # blocks as the spec's reference encoder wrote them.
        expected = {
            heroselect.PRACTICE: (
                446,
                "05 10 70 00 00 04 c8 37 40 03 89 07 00 88 e0 09 10 8a 28 14 60 85 58 44 21 01 00 04 39 08 05"
                " 50 21 1a 51 48 40 00 30 c3 04 a8 65 3d 08 00 08 01 00 00 00 04 20 92 00 6b 54 e0 10 00 00"
                " 00 00 b3 01",
            ),
            heroselect.PVP: (
                485,
                "05 10 70 00 00 04 a8 3c 40 03 89 07 00 08 48 06 10 8a 28 14 e0 8e 58 44 21 01 00 44 23 0a 09"
                " 08 00 66 98 00 b5 ac 07 01 00 00 40 08 00 01 00 00 00 00 00 00 00 00 00 00 00 40 02 00 58"
                " 23 02 87 00 00 00 00 d8 00",
            ),
        }
        for controller, (bits, data) in expected.items():
            frame = heroselect.controller_frame(controller, 123456, True, skins=True)
            self.assertEqual(frame.count, bits)
            block = BitWriter()
            _write_entity(block, EntityUpdate(0xA0000100, chunk=chunk(frame, 0, 1)))
            self.assertEqual(block.getvalue().hex(" "), data)

    def test_the_practice_range_hides_the_assemble_title(self):
        # v8535 in 288B's extra ids: its bit turns on, then w_var 8535 (18 bits), bool true (4 + 1),
        # no bindings (1) and the list's end (9): 33 bits more.
        practice = heroselect.PRACTICE
        shown = heroselect.controller_frame(practice, 123456, True, skins=True)
        hidden = heroselect.controller_frame(practice, 123456, True, skins=True, hide_assemble=True)
        self.assertEqual(hidden.count - shown.count, 33)

    def test_an_owner_ack_is_56_bits(self):
        self.assertEqual(owner_ack(123500).count, 56)

    def test_a_stale_cmfd_is_refused(self):
        with self.assertRaises(ValueError):
            owner_full_frame(0, [], {})

    def test_picks_and_open_requests(self):
        pick = bytes.fromhex("00 00 00 00 01 c1 5d 40 0a 0c 01 ff 04 6e 00 00 00 00 00 e0 02") + bytes(8)
        self.assertEqual(heroselect.picked_hero(pick), 0x02E000000000006E)
        self.assertIsNone(heroselect.picked_hero(bytes(20)))
        self.assertTrue(heroselect.wanted_open(bytes.fromhex("01 c3 a2 05 01 01") + bytes(8)))
        self.assertFalse(heroselect.wanted_open(bytes.fromhex("01 c3 a2 05 01 00") + bytes(8)))


if __name__ == "__main__":
    unittest.main()
