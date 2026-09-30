"""The game link's transport: sealing, opening and acks."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game.link import ACK, LinkCipher, Packet, ReceiveWindow, delivery, peek_connection


class LinkTests(unittest.TestCase):
    def setUp(self):
        client_key, server_key = os.urandom(32), os.urandom(32)
        self.server = LinkCipher(key_in=client_key, key_out=server_key)
        self.client = LinkCipher(key_in=server_key, key_out=client_key)

    def test_a_datagram_opens_with_the_other_sides_keys(self):
        packet = Packet(conn=77, kind=1, seq=5, ack=3, ack_bits=0b101, flags=ACK, payload=b"hello")
        data = self.client.seal(packet)
        self.assertEqual(len(data), 34 + 5)  # a 12-byte tag and 22 more header bytes
        self.assertEqual(data[33], 0xAD)
        self.assertEqual(peek_connection(data), 77)
        opened = self.server.open(data)
        self.assertEqual(
            (opened.conn, opened.kind, opened.seq, opened.ack, opened.ack_bits, opened.flags, opened.payload),
            (77, 1, 5, 3, 0b101, ACK, b"hello"),
        )

    def test_a_changed_byte_or_the_wrong_keys_do_not_open(self):
        data = bytearray(self.client.seal(Packet(conn=1, kind=1, seq=9, payload=b"frame")))
        self.assertIsNone(self.client.open(bytes(data)))  # our own keys the wrong way round
        data[20] ^= 1  # the header is authenticated too
        self.assertIsNone(self.server.open(bytes(data)))

    def test_acks_say_which_datagrams_arrived(self):
        window = ReceiveWindow()
        for seq in (0, 1, 3):
            self.assertTrue(window.accept(seq))
        self.assertFalse(window.accept(1))  # an old one is dropped, like the client does
        self.assertEqual((window.expected, window.bits), (4, 0b1101))
        self.assertTrue(delivery(3, window.expected, window.bits))
        self.assertFalse(delivery(2, window.expected, window.bits))
        self.assertTrue(delivery(0, window.expected, window.bits))
        self.assertIsNone(delivery(5, window.expected, window.bits))


if __name__ == "__main__":
    unittest.main()
