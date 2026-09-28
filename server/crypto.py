"""
Overwatch 1.74 Crypto & Handshake Module
Implements PANAMA / Jam stream cipher and State-4 blob generator.
"""

import socket
import hmac
import hashlib
import struct

M32 = 0xFFFFFFFF
IC_IDX = (0, 2, 4, 6, 1, 3, 5, 7)
IA_IDX = (5, 7, 0, 2, 4, 6, 1, 3)
KS_WORDS = (14, 10, 6, 2, 15, 11, 7, 3)

def rol32(x: int, n: int) -> int:
    x &= M32
    return ((x << n) | (x >> (32 - n))) & M32

def jam_round(s: list, ctrB: int) -> list:
    c1 = [((~s[4]) & M32 | s[8]) ^ s[12],
          ((~s[5]) & M32 | s[9]) ^ s[13],
          ((~s[6]) & M32 | s[10]) ^ s[14],
          ((~s[7]) & M32 | s[11]) ^ s[15]]
    c2 = [((~s[0]) & M32 | s[4]) ^ s[8],
          ((~s[1]) & M32 | s[5]) ^ s[9],
          ((~s[2]) & M32 | s[6]) ^ s[10],
          ((~s[3]) & M32 | s[7]) ^ s[11]]
    c3 = [((~s[13]) & M32 | s[0]) ^ s[4],
          ((~s[14]) & M32 | s[1]) ^ s[5],
          ((~s[15]) & M32 | s[2]) ^ s[6],
          ((~ctrB) & M32 | s[3]) ^ s[7]]
    c4 = [((~s[9]) & M32 | s[13]) ^ s[0],
          ((~s[10]) & M32 | s[14]) ^ s[1],
          ((~s[11]) & M32 | s[15]) ^ s[2],
          ((~s[12]) & M32 | ctrB) ^ s[3]]
    o = [0] * 16
    o[13] = rol32(c1[0], 15); o[1]  = rol32(c1[1], 4);  o[6]  = rol32(c1[2], 2);  o[11] = rol32(c1[3], 9)
    o[10] = rol32(c2[0], 23); o[15] = rol32(c2[1], 27); o[3]  = rol32(c2[2], 8);  o[8]  = rol32(c2[3], 3)
    o[7]  = rol32(c3[0], 24); o[12] = rol32(c3[1], 1);  o[0]  = rol32(c3[2], 10); o[5]  = rol32(c3[3], 28)
    o[4]  = rol32(c4[0], 6);  o[9]  = rol32(c4[1], 21); o[14] = rol32(c4[2], 13); o[2]  = rol32(c4[3], 14)
    return o

class Jam:
    """PANAMA stream cipher used by Blizzard's JAM protocol."""
    def __init__(self, key: bytes):
        assert len(key) == 32
        self.keyw = list(struct.unpack("<8I", key))
        self.s = [0] * 16
        self.ctrB = 0
        self.counter = 0
        self.ring = [[0] * 8 for _ in range(32)]
        self.cur = bytearray()
        self.pos = 0
        self.initialized = False

    def _absorb(self, kw):
        A = self.ring[((self.counter + 0x20) & 0x3E0) >> 5]
        B = self.ring[((self.counter + 0x100) & 0x3E0) >> 5]
        for k in range(8):
            t = A[IC_IDX[k]]
            A[IC_IDX[k]] = (t ^ kw[k]) & M32
            B[IA_IDX[k]] ^= t
        sp = jam_round(self.s, self.ctrB)
        ctrB_mix = (((~self.s[8]) & M32 | self.s[12]) ^ self.ctrB) & M32
        new_ctrB = (sp[0] ^ sp[12] ^ ctrB_mix ^ 1) & M32
        C = self.ring[((self.counter - 0x200) & 0x3E0) >> 5]
        s17 = sp + [ctrB_mix]
        core = [(sp[i] ^ s17[(i + 1) % 17] ^ s17[(i - 4) % 17]) & M32 for i in range(16)]
        extra = [kw[3], kw[7], C[6], C[7],
                 kw[2], kw[6], C[4], C[5],
                 kw[1], kw[5], C[2], C[3],
                 kw[0], kw[4], C[0], C[1]]
        self.s = [(core[i] ^ extra[i]) & M32 for i in range(16)]
        self.ctrB = new_ctrB
        self.counter = (self.counter + 0x20) & M32

    def _permute(self, count):
        for _ in range(count):
            old = self.s[:]
            ctrB_mix = (((~old[8]) & M32 | old[12]) ^ self.ctrB) & M32
            sp = jam_round(old, self.ctrB)
            Q = self.ring[((self.counter + 0x20) & 0x3E0) >> 5]
            P = self.ring[((self.counter + 0x100) & 0x3E0) >> 5]
            v41 = Q[0:4]
            for j, w in enumerate((old[12], old[13], old[8], old[9])):
                Q[j] ^= w
            P[4] ^= v41[1]; P[5] ^= v41[0]; P[6] ^= v41[3]; P[7] ^= v41[2]
            v42 = Q[4:8]
            for j, w in enumerate((old[4], old[5], old[0], old[1])):
                Q[4 + j] ^= w
            for j in range(4):
                P[j] ^= v42[j]
            T = self.ring[((self.counter - 0x80) & 0x3E0) >> 5]
            R = self.ring[((self.counter + 0x200) & 0x3E0) >> 5]
            s17 = sp + [ctrB_mix]
            core = [(sp[i] ^ s17[(i + 1) % 17] ^ s17[(i - 4) % 17]) & M32 for i in range(16)]
            ringw = [T[6], T[7], R[6], R[7],
                     T[4], T[5], R[4], R[5],
                     T[2], T[3], R[2], R[3],
                     T[0], T[1], R[0], R[1]]
            self.s = [(core[i] ^ ringw[i]) & M32 for i in range(16)]
            self.ctrB = (sp[0] ^ sp[12] ^ ctrB_mix ^ 1) & M32
            self.counter = (self.counter + 0x20) & M32

    def _init(self):
        self.s = [0] * 16
        self.ctrB = 0
        self.counter = 1
        self.ring = [[0] * 8 for _ in range(32)]
        for _ in range(2):
            self._absorb(self.keyw)
        self._permute(32)
        self.initialized = True

    def _squeeze_block(self):
        w = self.s
        out = b"".join(struct.pack("<I", w[i]) for i in KS_WORDS)
        self._permute(1)
        return out

    def crypt(self, data: bytes) -> bytes:
        if not self.initialized:
            self._init()
        out = bytearray()
        for b in data:
            if self.pos >= len(self.cur):
                self.cur = bytearray(self._squeeze_block())
                self.pos = 0
            out.append(b ^ self.cur[self.pos])
            self.pos += 1
        return bytes(out)

# Handshake Constants
HELLO_CLIENT = b"HELLO PRO CLIENT\x00"
HELLO_SERVER = b"HELLO PRO SERVER\x00"
ZERO_KEY     = b"\x00" * 32
DIFFICULTY   = 0

BLOB_KEY = bytes([
    0x35, 0x86, 0xF3, 0x62, 0x8A, 0x63, 0x1B, 0x70,
    0x57, 0x12, 0x40, 0x5B, 0x8A, 0xCC, 0x71, 0xD4,
    0x0F, 0xD1, 0x67, 0x0C, 0xC1, 0xB0, 0x3E, 0xA3,
    0x84, 0x97, 0x4A, 0x6F, 0xB1, 0xA7, 0x61, 0x96,
    0xB1, 0x42, 0xF0, 0xB7, 0x23, 0x10, 0xEA, 0x81,
    0x16, 0xD0, 0x0A, 0x4C, 0x35, 0x2F, 0x09, 0xAC,
    0xDB, 0xFB, 0x50, 0xA6, 0x3E, 0xC5, 0x15, 0x3E,
    0x62, 0xE4, 0xD6, 0x7F, 0xE0, 0x9B, 0xEE, 0xCC,
])

def build_state_blob_292(seq: int, host_ip: str, host_port: int) -> bytes:
    """
    Builds the 292-byte State-4 message with the correct 36-byte Type-13 trailer.
    Fixed per Ayaka PS / Blizless discovery:
      Bytes 256-271: Peer ID (Type 5)
      Bytes 272-287: Channel ID (Type 13 / 0x000D, port, IPv4)
      Bytes 288-291: u32 = 1
    """
    blob = bytearray(256)
    for i in range(256):
        blob[i] = i & 0xFF
    blob[0] = 0x02
    ip_bytes = socket.inet_aton(host_ip)
    blob[1:5] = ip_bytes
    blob[255] = 0xFF
    msg = bytes(blob[0:176]) + bytes(blob[208:256])
    blob[176:208] = hmac.new(BLOB_KEY, msg, hashlib.sha256).digest()

    # 36-byte Trailer
    peer_id = struct.pack("<IBHBH", seq, 0x00, 5, 0x01, 0xBEEF) + bytes(6)
    chan_id = struct.pack("<IBHBH", seq, 0x00, 13, 0x01, 0x0000) + struct.pack("<H", host_port) + ip_bytes
    trailer_u32 = struct.pack("<I", 1)

    res = bytes(blob) + peer_id + chan_id + trailer_u32
    assert len(res) == 292
    return res
