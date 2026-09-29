"""Fixed byte vectors for the cipher, the handshake blob and the message codec.

The client speaks this wire format exactly, so any refactor must reproduce these bytes.
"""

import copy
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.jam.cipher import Jam
from ow174.jam.codec import Schemas
from ow174.jam.handshake import build_state_blob


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CipherTests(unittest.TestCase):
    KEY = bytes(range(32))
    PLAIN = bytes(range(256)) * 4

    def test_keystream_vector(self):
        self.assertEqual(
            sha(Jam(self.KEY).crypt(self.PLAIN)),
            "60a4fe537212e3bb121055bcf8b65569329f886a6c50f56bef66188a3360645b",
        )

    def test_decrypting_restores_the_plaintext(self):
        cipher = Jam(self.KEY).crypt(self.PLAIN)
        self.assertEqual(Jam(self.KEY).crypt(cipher), self.PLAIN)

    def test_chunking_does_not_change_the_stream(self):
        whole = Jam(self.KEY).crypt(self.PLAIN)
        pieces = Jam(self.KEY)
        joined = b"".join(pieces.crypt(self.PLAIN[i : i + 37]) for i in range(0, len(self.PLAIN), 37))
        self.assertEqual(joined, whole)


class HandshakeBlobTests(unittest.TestCase):
    def test_state_blob_vector(self):
        blob = build_state_blob(0x12345678, "127.0.0.1", 3724)
        self.assertEqual(len(blob), 292)
        self.assertEqual(sha(blob), "8921b1aa09174ced2ccf3f5900603a09337d9c7f20f8459dde71a93d63cccef2")


class CodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = Schemas()

    def test_empty_array_message(self):
        self.assertEqual(self.schemas.encode(0x9529F0ED, 52204, {"+0x78": []}), bytes(4))

    def test_login_message_round_trip_and_vector(self):
        value = self.schemas.empty(0x3C7E3468, 21800)
        value["+0x78"] = "Player"
        body = self.schemas.encode(0x3C7E3468, 21800, value)
        self.assertEqual(self.schemas.decode(0x3C7E3468, 21800, body)["+0x78"], "Player")
        self.assertEqual(sha(body), "5ef774494c2150e5b721533367200cd1ff1d64c9b44ef776dc8f9a16200cdba1")

    def test_a_run_of_bools_goes_on_across_struct_edges(self):
        # Tested in game: in 20802 the 5 flags of +0x108 and the first 4 of +0x10D form one run of
        # 9 bits. With a new byte for +0x10D the client read the friends options from the wrong bits.
        base = self.schemas.empty(0x1A9879A4, 20802)
        plain = self.schemas.encode(0x1A9879A4, 20802, base)

        def changed_bit(block, key):
            value = copy.deepcopy(base)
            value[block][key] = True
            body = self.schemas.encode(0x1A9879A4, 20802, value)
            self.assertTrue(self.schemas.decode(0x1A9879A4, 20802, body)[block][key])
            (index,) = [i for i in range(len(body)) if body[i] != plain[i]]
            return index, body[index] ^ plain[index]

        first, bit = changed_bit("+0x108", "+0x0")
        self.assertEqual(bit, 1)
        self.assertEqual(changed_bit("+0x10D", "+0x0"), (first, 1 << 5))
        self.assertEqual(changed_bit("+0x10D", "+0x3"), (first + 1, 1))

    def test_prefixed_protocols_carry_a_u32_before_the_message_id(self):
        # Header flag 8 of the client's registration: the account features and the group finder.
        self.assertEqual(self.schemas.header(0xBDDBF58A, 70, 52301), bytes([70, 0, 0, 0, 0, 1]))
        self.assertEqual(self.schemas.header(0xBCD57A46, 69, 55500), bytes([69, 0, 0, 0, 0, 0]))
        self.assertEqual(self.schemas.header(0x2411DE56, 18, 20703), bytes([18, 3]))
        body = self.schemas.encode(0xBDDBF58A, 52300, {"+0x78": [], "+0x90": []})
        self.assertEqual(body, bytes(8))


if __name__ == "__main__":
    unittest.main()
