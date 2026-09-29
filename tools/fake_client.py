#!/usr/bin/env python3
"""
Offline smoke test for the lobby server: plays the 1.74 client's side of the
handshake, synthesizes an announcement and login (or replays an optional log),
then strictly decodes every server message with the client's schemas.

    py tools/fake_client.py [--port 3724] [--log data/client_msgs_run3.log]
"""

import argparse
import hashlib
import hmac
import os
import re
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ow174.jam.cipher import Jam  # noqa: E402
from ow174.jam.codec import DecodeError, Schemas  # noqa: E402
from ow174.jam.handshake import HELLO_CLIENT, HELLO_SERVER, ZERO_KEY  # noqa: E402
from ow174.jam.values import to_jsonable  # noqa: E402


def recvn(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("server closed")
        buf += chunk
    return buf


def captured(log: Path, wire: int, offset: int) -> bytes:
    text = log.read_text(encoding="utf-8")
    m = re.search(rf"wire={wire} off={offset} len=\d+\n([0-9a-f ]*)\n", text)
    if not m:
        raise SystemExit(f"no wire={wire} off={offset} frame in {log}")
    return bytes.fromhex(m.group(1).replace(" ", ""))


class FakeClient:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=10)
        self.s.sendall(HELLO_CLIENT)
        assert recvn(self.s, len(HELLO_SERVER)) == HELLO_SERVER
        cn = os.urandom(32)
        self.s.sendall(b"\x00" * 8 + cn)
        reply = recvn(self.s, 65)
        sn = reply[33:65]
        self.s.sendall(struct.pack("<Q", 1) + hmac.new(ZERO_KEY, cn + sn, hashlib.sha256).digest())
        mac2 = recvn(self.s, 32)
        assert mac2 == hmac.new(ZERO_KEY, sn + cn, hashlib.sha256).digest(), "bad server proof"
        self.rx = Jam(hmac.new(ZERO_KEY, cn + sn, hashlib.sha256).digest())
        self.tx = Jam(hmac.new(ZERO_KEY, sn + cn, hashlib.sha256).digest())
        self.rx.crypt(recvn(self.s, 292))
        self.buf = b""

    def send(self, payload: bytes):
        self.s.sendall(self.tx.crypt(len(payload).to_bytes(3, "big") + payload))

    def frames(self, seconds: float):
        end = time.time() + seconds
        self.s.settimeout(0.3)
        while time.time() < end:
            try:
                data = self.s.recv(1 << 20)
            except TimeoutError:
                continue
            if not data:
                return
            self.buf += self.rx.crypt(data)
            while len(self.buf) >= 3:
                n = int.from_bytes(self.buf[:3], "big")
                if len(self.buf) < 3 + n:
                    break
                frame, self.buf = self.buf[3 : 3 + n], self.buf[3 + n :]
                yield frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3724)
    ap.add_argument("--log", default=None, help="Optional independently captured client log")
    ap.add_argument("--name", default=None, help="login name (default: Researcher, or the name from --log)")
    args = ap.parse_args()

    schemas = Schemas()
    log = Path(args.log) if args.log else None
    if log:
        announce = captured(log, 0, 0)
    else:
        groups = list(schemas.groups)
        announce = struct.pack("<I", len(groups)) + b"".join(struct.pack("<I", crc) for crc in groups)
    crcs = [
        struct.unpack_from("<I", announce, 4 + i * 4)[0] for i in range(struct.unpack_from("<I", announce)[0])
    ]
    crc_at = {i + 1: c for i, c in enumerate(crcs)}
    wire_of = {c: i for i, c in crc_at.items()}

    c = FakeClient(args.host, args.port)
    c.send(b"\x00\x00" + announce)
    login = (
        schemas.decode(0x3C7E3468, 21800, captured(log, 25, 0)) if log else schemas.empty(0x3C7E3468, 21800)
    )
    if not log:
        login["+0x78"] = "Researcher"
    if args.name:
        login["+0x78"] = args.name
    c.send(bytes([wire_of[0x3C7E3468], 0]) + schemas.encode(0x3C7E3468, 21800, login))

    def drain(seconds):
        ok = bad = 0
        for f in c.frames(seconds):
            if f[0] == 0:
                continue
            crc = crc_at[f[0]]
            mid, body = schemas.split(crc, f)
            try:
                v = schemas.decode(crc, mid, body)
                ok += 1
                print(f"  OK  {crc:08X}/{mid} {len(body):6d}B  {str(to_jsonable(v))[:110]}")
            except (DecodeError, KeyError, struct.error) as e:
                bad += 1
                print(f"  BAD {crc:08X}/{mid} {len(body):6d}B  {e}")
        return ok, bad

    print("login:")
    ok, bad = drain(4)
    print(f"login frames: {ok} ok, {bad} bad\n")

    print("open box / name query / store / equip / purchase / profile:")
    c.send(bytes([wire_of[0x7F4F46CB], 1]) + schemas.encode(0x7F4F46CB, 24201, {"+0x78": {"+0x0": [1, 0]}}))
    c.send(
        bytes([wire_of[0x46DC9706], 2])
        + schemas.encode(0x46DC9706, 58202, {"+0x78": [{"+0x0": 0x425AE13F, "+0x8": 1 << 56}]})
    )
    c.send(
        bytes([wire_of[0x5217E4CD], 0])
        + schemas.encode(0x5217E4CD, 26500, {"+0x7C": 5, "+0x80": 1, "+0x88": "RU"})
    )
    genji, illidan, top500 = 0x02E0000000000029, 0x02500000000028EC, 0x0250000000000A90
    c.send(
        bytes([wire_of[0xB68870B8], 0])
        + schemas.encode(0xB68870B8, 24500, {"+0x78": genji, "+0x80": illidan, "+0x88": 0})
    )
    c.send(
        bytes([wire_of[0xB68870B8], 0])
        + schemas.encode(0xB68870B8, 24500, {"+0x78": 0, "+0x80": top500, "+0x88": 0})
    )
    c.send(bytes([wire_of[0x7F4F46CB], 3]) + schemas.encode(0x7F4F46CB, 24203, {"+0x78": illidan}))
    c.send(
        bytes([wire_of[0x75D32AE2], 6])
        + schemas.encode(
            0x75D32AE2,
            22206,
            {
                "+0x78": {"+0x0": 0x425AE13F, "+0x8": 1 << 56},
                "+0x88": {"+0x0": 0x425AE13F, "+0x8": 1 << 56},
                "+0x98": 1,
            },
        )
    )
    ok2, bad2 = drain(3)
    print(f"reply frames: {ok2} ok, {bad2} bad")
    sys.exit(1 if bad or bad2 else 0)


if __name__ == "__main__":
    main()
