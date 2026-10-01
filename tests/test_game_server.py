"""The game server with a pretend game on UDP: handshake, 20300, and the world after loading."""

import dataclasses
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game import match as match_module
from ow174.game import messages
from ow174.game.bits import BitReader, BitWriter
from ow174.game.content import PRACTICE_RANGE
from ow174.game.link import ACK, KIND_FRAME, SYN, LinkCipher, Packet
from ow174.game.server import GameServer, Handoff

REAPER = 0x02E0000000000002


def pick(entity: int, hero: int) -> dict:
    """21616 as the hero select screen sends it: game message 1361.025 with the hero."""
    return {
        "+0x78": {"+0x0": entity},
        "+0x80": bytes.fromhex("00 00 00 00 01 c1 5d 04") + hero.to_bytes(8, "little") + bytes(8),
        "+0xA8": 0x0240000000001361,
        "+0xB0": 3,
    }


class PretendGame:
    """Speaks the client's side of the link with the keys of a 20600."""

    def __init__(self, port: int, handoff) -> None:
        self.address = ("127.0.0.1", port)
        self.conn = handoff.conn
        self.cipher = LinkCipher(key_in=handoff.key_out, key_out=handoff.key_in)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(2.0)
        self.seq = 0
        self.expected = 0
        self.reliable = 0

    def send(self, flags: int, kind: int = 0, payload: bytes = b"") -> None:
        packet = Packet(self.conn, kind, self.seq, self.expected, 1, flags, payload)
        self.seq += 1
        self.sock.sendto(self.cipher.seal(packet), self.address)

    def send_message(self, msg_id: int, value: dict) -> None:
        channel = BitWriter()
        messages.write_channel(channel, [(self.reliable, messages.encode(msg_id, value))])
        self.reliable += 1
        self.send(ACK, KIND_FRAME, channel.getvalue())

    def receive(self) -> Packet:
        data, _ = self.sock.recvfrom(4096)
        packet = self.cipher.open(data)
        self.expected = packet.seq + 1
        return packet

    def frames_until(self, wanted, seconds: float = 3.0):
        """Read frames until wanted(messages, entity count) is true; returns what it saw."""
        seen = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            packet = self.receive()
            if packet.kind != KIND_FRAME:
                continue
            reader = BitReader(packet.payload)
            reader.var_a()
            reliable, _ = messages.read_channel(reader)
            reader.var_a()
            reader.bits(4 + 1 + 1 + 1)
            count = reader.bits(7)
            seen.append(([msg_id for _, msg_id, _ in reliable], count))
            if wanted(seen[-1][0], count):
                return seen
        raise AssertionError(f"not seen in time: {seen[-5:]}")


class GameServerTests(unittest.TestCase):
    def setUp(self):
        self.left = []
        self.server = GameServer("127.0.0.1", 0, on_leave=self.left.append)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_a_game_connects_loads_and_gets_its_world(self):
        (handoff,) = self.server.create_match(PRACTICE_RANGE, [(1, "Alpha", 0, 0, False)])
        game = PretendGame(self.server.port, handoff)
        self.addCleanup(game.sock.close)
        game.send(SYN)
        answer = game.receive()
        self.assertEqual(answer.flags & (SYN | ACK), SYN | ACK)
        game.send(ACK)
        game.frames_until(lambda ids, count: 20300 in ids)
        game.send_message(21601, {})  # the map is loaded
        seen = game.frames_until(lambda ids, count: count >= 6)
        sent = [msg_id for ids, _ in seen for msg_id in ids]
        self.assertLess(sent.index(20302), sent.index(20301))
        # four placeables, the player and the game mode entity in one frame; the body comes with the pick
        self.assertEqual(seen[-1][1], 6)
        self.assertEqual(self.server.snapshot()[0]["players"][0]["state"], "playing")

    def test_two_players_see_each_others_body(self):
        handoffs = self.server.create_match(
            PRACTICE_RANGE, [(1, "Alpha", 0, 0, False), (2, "Beta", 0, 1, False)]
        )
        games = [PretendGame(self.server.port, handoff) for handoff in handoffs]
        for game in games:
            self.addCleanup(game.sock.close)
            game.send(SYN)
            game.receive()
            game.send(ACK)
            game.frames_until(lambda ids, count: 20300 in ids)
            game.send_message(21601, {})
            game.frames_until(lambda ids, count: count >= 6)
        games[0].send_message(21616, pick(0xA0000100, REAPER))
        # Alpha gets the body and the player's update; Beta gets Alpha's body, then its movement
        games[0].frames_until(lambda ids, count: count >= 2)
        games[1].frames_until(lambda ids, count: count >= 1)
        self.assertEqual(self.server.snapshot()[0]["players"][0]["hero"], "Reaper")

    def test_a_hero_pick_switches_the_hero(self):
        (handoff,) = self.server.create_match(PRACTICE_RANGE, [(1, "Alpha", 0, 0, False)])
        game = PretendGame(self.server.port, handoff)
        self.addCleanup(game.sock.close)
        game.send(SYN)
        game.receive()
        game.send(ACK)
        game.send_message(21601, {})
        game.frames_until(lambda ids, count: count >= 6)
        game.send_message(21616, pick(0xA0000100, REAPER))
        game.frames_until(lambda ids, count: self.server.snapshot()[0]["players"][0]["hero"] == "Reaper")

    def test_a_new_skin_on_the_same_hero_brings_a_new_body(self):
        skins = {REAPER: (0x0A5000000000160F, False)}
        self.server.skin_of = lambda account, hero: skins.get(hero, (0, False))
        (handoff,) = self.server.create_match(PRACTICE_RANGE, [(1, "Alpha", 0, 0, False)])
        player = self.server.matches[0].players[0]
        game = PretendGame(self.server.port, handoff)
        self.addCleanup(game.sock.close)
        game.send(SYN)
        game.receive()
        game.send(ACK)
        game.send_message(21601, {})
        game.frames_until(lambda ids, count: count >= 6)
        game.send_message(21616, pick(0xA0000100, REAPER))
        game.frames_until(lambda ids, count: player.has_body)
        self.assertEqual(player.skin, (0x0A5000000000160F, False))
        first = player.body
        skins[REAPER] = (0x0A500000000016C3, True)  # the lobby got 24500 from the skin selector
        game.send_message(21616, pick(0xA0000100, REAPER))
        game.frames_until(lambda ids, count: player.body != first)
        self.assertEqual(player.skin, (0x0A500000000016C3, True))

    def test_heroes_are_assembled_in_a_pvp_mode(self):
        # A PvP mode (any other than the Practice Range's): the screen stays open after the pick until
        # the countdown ends, then closes.
        pvp = dataclasses.replace(PRACTICE_RANGE, mode_guid=0x0230000000000002, mode_name="Control")
        with mock.patch.object(match_module, "ASSEMBLE_SECONDS", 0.6):
            (handoff,) = self.server.create_match(pvp, [(1, "Alpha", 0, 0, False)])
            match = self.server.matches[0]
            player = match.players[0]
            game = PretendGame(self.server.port, handoff)
            self.addCleanup(game.sock.close)
            game.send(SYN)
            game.receive()
            game.send(ACK)
            game.send_message(21601, {})
            game.frames_until(lambda ids, count: match.assembling())
            game.frames_until(lambda ids, count: player.steps_done >= 4)
            game.send_message(21616, pick(0xA0000100, REAPER))
            game.frames_until(lambda ids, count: player.has_body)
            self.assertTrue(player.select_open)  # still assembling
            game.frames_until(lambda ids, count: match.assembled, seconds=2.0)
            self.assertFalse(player.select_open)
            self.assertGreater(player.mode_script.last, 0)  # the countdown went to the game mode entity

    def test_a_ping_gets_its_pong(self):
        (handoff,) = self.server.create_match(PRACTICE_RANGE, [(1, "Alpha", 0, 0, False)])
        game = PretendGame(self.server.port, handoff)
        self.addCleanup(game.sock.close)
        game.send(SYN)
        game.receive()
        game.send(ACK)
        game.send_message(21610, {"+0x78": 3, "+0x80": [111, 222]})
        game.frames_until(lambda ids, count: 20306 in ids)

    def test_a_datagram_with_other_keys_is_ignored(self):
        (handoff,) = self.server.create_match(PRACTICE_RANGE, [(1, "Alpha", 0, 0, False)])
        wrong = Handoff(handoff.conn, bytes(32), handoff.key_out, handoff.match_id)  # a key the server lacks
        game = PretendGame(self.server.port, wrong)
        self.addCleanup(game.sock.close)
        game.sock.settimeout(0.3)
        game.send(SYN)
        with self.assertRaises(TimeoutError):
            game.receive()


if __name__ == "__main__":
    unittest.main()
