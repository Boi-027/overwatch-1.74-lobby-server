"""The PANAMA stream cipher that encrypts the JAM lobby connection.

PANAMA keeps a state `a` of 17 words and a buffer of 32 stages of 8 words. Each step runs one round
on the state (gamma, pi, theta, sigma) and feeds 8 words into the buffer. Key setup pushes the 8 key
words twice and then runs 32 blank steps. After that, every blank step gives 32 bytes of keystream.

The state words are stored in a different order than in the PANAMA paper, so the index tables below
do not match the paper's. Buffer stages are picked relative to a counter that grows by 0x20 per step.
"""

import struct

M32 = 0xFFFFFFFF

# Gamma: new word i is (~a[x] | a[y]) ^ a[z] for the (x, y, z) at position i.
GAMMA_INPUTS = (
    (4, 8, 12), (5, 9, 13), (6, 10, 14), (7, 11, 15),
    (0, 4, 8), (1, 5, 9), (2, 6, 10), (3, 7, 11),
    (13, 0, 4), (14, 1, 5), (15, 2, 6), (16, 3, 7),
    (9, 13, 0), (10, 14, 1), (11, 15, 2), (12, 16, 3),
    (8, 12, 16),
)  # fmt: skip
# Pi: where each of the first 16 gamma words lands, and by how many bits it is rotated. Word 16 stays.
PI_OUTPUT = (
    (13, 15), (1, 4), (6, 2), (11, 9),
    (10, 23), (15, 27), (3, 8), (8, 3),
    (7, 24), (12, 1), (0, 10), (5, 28),
    (4, 6), (9, 21), (14, 13), (2, 14),
)  # fmt: skip
# Buffer feed: input word i is XORed into slot NEAR_SLOTS[i] of the next stage, and the old value of
# that slot is XORed into slot FAR_SLOTS[i] of the stage eight ahead.
NEAR_SLOTS = (0, 2, 4, 6, 1, 3, 5, 7)
FAR_SLOTS = (5, 7, 0, 2, 4, 6, 1, 3)
# The state words that make up one 32-byte keystream block, in output order.
KEYSTREAM_WORDS = (14, 10, 6, 2, 15, 11, 7, 3)


def rol32(x: int, n: int) -> int:
    x &= M32
    return ((x << n) | (x >> (32 - n))) & M32


def panama_round(a: list[int], inject: list[int], stage: list[int]) -> list[int]:
    """One round on the 17-word state. `inject` and `stage` are the 8 words that sigma mixes in."""
    gamma = []
    for x, y, z in GAMMA_INPUTS:
        gamma.append(((~a[x]) & M32 | a[y]) ^ a[z])

    pi = [0] * 17
    for word, (index, shift) in zip(gamma[:16], PI_OUTPUT, strict=True):
        pi[index] = rol32(word, shift)
    pi[16] = gamma[16]

    theta = []
    for i in range(17):
        theta.append(pi[i] ^ pi[(i + 1) % 17] ^ pi[(i - 4) % 17])

    theta[16] ^= 1
    for group, pair in enumerate((3, 2, 1, 0)):
        first = 4 * group
        theta[first] ^= inject[2 * pair]
        theta[first + 1] ^= inject[2 * pair + 1]
        theta[first + 2] ^= stage[2 * pair]
        theta[first + 3] ^= stage[2 * pair + 1]
    return [word & M32 for word in theta]


class Jam:
    """PANAMA stream cipher used by Blizzard's JAM protocol. The same call encrypts and decrypts."""

    def __init__(self, key: bytes):
        assert len(key) == 32
        self.a = [0] * 17
        self.buffer = [[0] * 8 for _ in range(32)]
        self.counter = 1
        self._block = b""
        self._pos = 0
        key_words = list(struct.unpack("<8I", key))
        for _ in range(2):
            self._push(key_words)
        for _ in range(32):
            self._pull()

    def _stage(self, offset: int) -> list[int]:
        return self.buffer[((self.counter + offset) & 0x3E0) >> 5]

    def _feed_buffer(self, words: list[int]) -> None:
        near = self._stage(0x20)
        far = self._stage(0x100)
        for word, near_slot, far_slot in zip(words, NEAR_SLOTS, FAR_SLOTS, strict=True):
            old = near[near_slot]
            near[near_slot] = (old ^ word) & M32
            far[far_slot] ^= old

    def _push(self, words: list[int]) -> None:
        """A step that absorbs 8 input words (used for the key)."""
        self._feed_buffer(words)
        inject = [words[0], words[4], words[1], words[5], words[2], words[6], words[3], words[7]]
        self.a = panama_round(self.a, inject, self._stage(-0x200))
        self.counter = (self.counter + 0x20) & M32

    def _pull(self) -> None:
        """A blank step: the buffer is fed from the state, and sigma mixes in an older stage."""
        a = self.a
        self._feed_buffer([a[12], a[8], a[4], a[0], a[13], a[9], a[5], a[1]])
        self.a = panama_round(a, self._stage(-0x80), self._stage(0x200))
        self.counter = (self.counter + 0x20) & M32

    def _next_block(self) -> bytes:
        block = b"".join(struct.pack("<I", self.a[i]) for i in KEYSTREAM_WORDS)
        self._pull()
        return block

    def crypt(self, data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            if self._pos >= len(self._block):
                self._block = self._next_block()
                self._pos = 0
            out.append(byte ^ self._block[self._pos])
            self._pos += 1
        return bytes(out)
