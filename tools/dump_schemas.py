#!/usr/bin/env python3
"""
Extract every lobby message schema from a running 1.74 client.

The client keeps one JamMessageInfo per message: {name*, protocol*, items*,
u32 crc, u32 message_id}. Items are 0x30-byte records {name*, u32 type,
u32 offset, u32 size, i32 count, inner*, resize*, bits}; type 17 ends a list,
type 14 nests a struct through inner*, and a non-null resize* marks an array.
The process is only read (ReadProcessMemory), never written.

    py tools/dump_schemas.py            (with Overwatch.exe running)

Writes data/schemas_174.json and data/schemas_174.txt.
"""

import collections
import ctypes
import ctypes.wintypes as wt
import json
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TYPE_NAMES = {
    0: "bool",
    1: "u8",
    2: "u8",
    3: "i8",
    4: "u16",
    5: "i16",
    6: "u32",
    7: "i32",
    8: "u64",
    9: "i64",
    10: "f32",
    11: "f64",
    12: "str",
    13: "str",
    14: "struct",
    15: "blob",
    16: "dyn",
    17: "end",
}

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class MODINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p), ("SizeOfImage", wt.DWORD), ("EntryPoint", ctypes.c_void_p)]


k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


def read_image(process="Overwatch.exe"):
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {process}", "/FO", "CSV", "/NH"], capture_output=True, text=True
    ).stdout
    pid = next(
        (
            int(line.split('","')[1])
            for line in out.splitlines()
            if line.lower().startswith(f'"{process.lower()}"')
        ),
        None,
    )
    if pid is None:
        raise SystemExit(f"{process} is not running")
    h = k32.OpenProcess(0x0410, False, pid)
    mods = (wt.HMODULE * 1)()
    psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(wt.DWORD()), 3)
    mi = MODINFO()
    psapi.GetModuleInformation(h, ctypes.c_void_p(mods[0]), ctypes.byref(mi), ctypes.sizeof(mi))
    base, size = mi.lpBaseOfDll, mi.SizeOfImage
    img = bytearray(size)
    addr, mbi = base, MBI()
    while addr < base + size and k32.VirtualQueryEx(
        h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
    ):
        region, rsize = mbi.BaseAddress or 0, mbi.RegionSize
        if mbi.State == 0x1000 and not (mbi.Protect & 0x101):
            for s in range(0, rsize, 1 << 20):
                n = min(1 << 20, rsize - s)
                buf, got = ctypes.create_string_buffer(n), ctypes.c_size_t()
                if k32.ReadProcessMemory(h, ctypes.c_void_p(region + s), buf, n, ctypes.byref(got)):
                    off = region - base + s
                    img[off : off + got.value] = buf.raw[: got.value]
        addr = region + rsize
    return base, bytes(img)


class SchemaReader:
    def __init__(self, base, img):
        self.base, self.img = base, img

    def q(self, rva):
        return struct.unpack_from("<Q", self.img, rva)[0]

    def rva(self, ptr):
        return ptr - self.base if self.base <= ptr < self.base + len(self.img) else None

    def valid(self, items_rva, depth=0):
        r = items_rva
        for _ in range(300):
            if r + 0x30 > len(self.img):
                return False
            t, off, size, cnt = struct.unpack_from("<IIIi", self.img, r + 8)
            if t == 17:
                return True
            if t > 17 or off > 0x10000 or size > 0x10000 or cnt < -1 or cnt > 0x1000:
                return False
            if t == 14:
                inner = self.rva(self.q(r + 0x18))
                if inner is None or depth > 16 or not self.valid(inner, depth + 1):
                    return False
            r += 0x30
        return False

    def fields(self, items_rva, depth=0):
        out, r = [], items_rva
        for _ in range(300):
            t, off, size, cnt = struct.unpack_from("<IIIi", self.img, r + 8)
            if t == 17:
                break
            f = {"type": t, "off": off, "size": size, "count": cnt, "array": bool(self.q(r + 0x20))}
            inner = self.rva(self.q(r + 0x18))
            if t == 14 and inner is not None and depth < 16:
                f["fields"] = self.fields(inner, depth + 1)
            out.append(f)
            r += 0x30
        return out

    def messages(self, crcs):
        infos = {}
        for off in range(0x18, len(self.img) - 8, 8):
            crc, msg_id = struct.unpack_from("<II", self.img, off)
            if crc in crcs and 0 < msg_id < 70000:
                items = self.rva(self.q(off - 8))
                if items is not None and self.valid(items):
                    infos.setdefault((crc, msg_id), items)
        groups = collections.defaultdict(dict)
        for (crc, msg_id), items in infos.items():
            groups[crc][msg_id] = self.fields(items)
        return groups


def describe(fields, indent="  "):
    lines = []
    for f in fields:
        arr = "[]" if f["array"] else (f"[{f['count']}]" if f["count"] > 1 else "")
        lines.append(
            f"{indent}+0x{f['off']:X} {TYPE_NAMES.get(f['type'], f['type'])}{arr} "
            f"size={f['size']:#x} cnt={f['count']}"
        )
        lines += describe(f.get("fields", []), indent + "    ")
    return lines


def main():
    announced = json.loads((DATA_DIR / "announced_crcs_174.json").read_text())
    index = {int(c, 16): i for i, c in announced}
    index[0x11D82194] = 0
    base, img = read_image()
    groups = SchemaReader(base, img).messages(set(index))
    (DATA_DIR / "schemas_174.json").write_text(
        json.dumps({f"{c:08X}": {str(m): f for m, f in sorted(g.items())} for c, g in sorted(groups.items())})
    )
    with open(DATA_DIR / "schemas_174.txt", "w") as out:
        for crc in sorted(groups, key=lambda c: index[c]):
            g = groups[crc]
            out.write(f"=== wire {index[crc]} crc {crc:08X} ids {min(g)}..{max(g)}\n")
            for mid in sorted(g):
                out.write(f" msg {mid} (off {mid - min(g)})\n" + "\n".join(describe(g[mid])) + "\n")
    print(f"{sum(len(g) for g in groups.values())} messages in {len(groups)} protocols")


if __name__ == "__main__":
    main()
