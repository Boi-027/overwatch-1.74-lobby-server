"""Hero body scripts: the frames a fresh body gets, at the sizes the live client accepted."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game import loadout, statescript
from ow174.game.content import heroes


def bitstring(bits) -> str:
    return "".join(str((bits.value >> k) & 1) for k in range(bits.count))


class LoadoutTests(unittest.TestCase):
    def test_every_described_body_builds_the_pair_of_chunks(self):
        for body in sorted(loadout.graph.bodies()):
            create = loadout.body_graph_chunk(body)
            full = loadout.body_full_chunk(body, 9)
            self.assertIsNotNone(create, f"body {body:016X}")
            self.assertIsNotNone(full, f"body {body:016X}")
            self.assertLess(create.count, full.count)  # the empty frame is a prefix step, not the picture

    def test_the_create_chunk_carries_no_instances(self):
        # The create record is the empty graph-info frame in a base-1 chunk: header, size varint,
        # lead/H/C and CmFD 0, then only the empty instance list - 56 bits of nothing but structure.
        create = loadout.body_graph_chunk(0x0400000000002593)  # Echo
        self.assertEqual(create.count, 56)
        self.assertEqual(
            bitstring(create),
            "01000000000000000010000000000010100001000000000000000000",
        )

    def test_hero_frames_are_the_sizes_the_live_run_proved(self):
        # Unrestricted frames per hero body: instance counts (body graphs plus the manager and the
        # weapon scripts that build the gun), entity vars and the full snapshot's bit size (the
        # numbers a regression to the encoder must not change).
        expected = {
            "Echo": (10, 6, 1340, 1385),
            "Genji": (10, 7, 1358, 1403),
            "Soldier: 76": (11, 4, 1116, 1161),
            "Wrecking Ball": (12, 9, 1615, 1660),
        }
        for name, (graphs, evars, frame_bits, full_bits) in expected.items():
            hero = next(h for h in heroes().values() if h.name == name)
            instances, entity_vars = loadout.body_instances(hero.body, allow=None)
            self.assertEqual(len(instances), graphs, name)
            self.assertEqual(len(entity_vars), evars, name)
            self.assertEqual(loadout.body_frame(hero.body, 1, allow=None).count, frame_bits, name)
            self.assertEqual(loadout.body_full_chunk(hero.body, 9, allow=None).count, full_bits, name)

    def test_the_hero_asset_and_the_hud_gates_ride_the_entity_block(self):
        hero = next(h for h in heroes().values() if h.name == "Echo")
        _, entity_vars = loadout.body_instances(hero.body, allow=None)
        asset = entity_vars[0x03AC]  # every StateHero reads this; a null guid and the client exits
        self.assertIsInstance(asset, statescript.Asset)
        self.assertEqual(asset.guid, hero.guid)
        # no hero body in the data carries 20E4: the HUD's begin-condition vars belong to the
        # controller's frames (see heroselect), and the body snapshot must not invent them here
        self.assertNotIn(17906, entity_vars)

    def test_the_volley_target_points_at_the_gun(self):
        # The firing frames address the gun's WeaponVolley state by (instance, slot): Soldier: 76
        # keeps it in his rifle script graph, which rides as its own instance - after the nine body
        # graphs and the manager - at owner state 10.
        hero = next(h for h in heroes().values() if h.name == "Soldier: 76")
        self.assertEqual(loadout.volley_target(hero.body), (11, 10))
        self.assertIsNone(loadout.volley_target(0))

    def test_a_volley_entry_grows_the_frame_by_exactly_its_form(self):
        # Baseline: the state is one off-bit. The volley frame: has-data + active + the 18 bits.
        hero = next(h for h in heroes().values() if h.name == "Soldier: 76")
        base = loadout.body_full_chunk(hero.body, 9, allow=loadout.SHIP_STATES).count
        volley = loadout.body_full_chunk(
            hero.body, 9, allow=loadout.SHIP_STATES,
            entries={(11, 10): statescript.HasData(statescript.volley_data())},
        ).count
        self.assertEqual(volley, base + 19)  # +2 for the two-bit entry, +18 data, -1 off bit

    def test_the_state_allow_list_keeps_structure_and_shrinks_the_picture(self):
        # The shipped allow-list keeps every graph and launcher and the gun scripts, and drops a few
        # state payloads: the same body picture, minus the classes whose begin actions need
        # encoders that are not here yet.
        instances, entity_vars = loadout.body_instances(0x0400000000002593, allow=loadout.SHIP_STATES)
        self.assertEqual(len(instances), 10)  # the graphs stay: launchers and gates are structure
        self.assertEqual(len(entity_vars), 6)
        self.assertEqual(loadout.body_frame(0x0400000000002593, 1, allow=loadout.SHIP_STATES).count, 1280)
        launchers = [
            slot for instance in instances for slot in instance.active
            if instance.graph.index != statescript.HUD.index
        ]
        self.assertTrue(launchers)  # the SubScript launchers remain on


if __name__ == "__main__":
    unittest.main()
