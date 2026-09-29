"""Fixed byte vectors for the cipher, the handshake blob and the message codec.

The client speaks this wire format exactly, so any refactor must reproduce these bytes.
"""

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

    def test_prefixed_protocol_carries_a_leading_u32(self):
        body = self.schemas.encode(0xBCD57A46, 55500, {"+0x78": {"+0x0": []}})
        self.assertEqual(body[:4], bytes(4))
        self.assertEqual(self.schemas.decode(0xBCD57A46, 55500, body), {"+0x78": {"+0x0": []}})


if __name__ == "__main__":
    unittest.main()
