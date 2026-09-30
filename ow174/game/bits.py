"""The bit stream of the game link: LSB first, as the client's reader 0x7FF78969BDB0 takes it.

Variable-width numbers start with selector bits that pick a width. The client uses several tables:
  var_a      1 -> 18 bits | 01 -> 24 | 00 -> 32      the packet frame, the ch3 ack (0x7FF789CC0E60)
  var_b      0 -> 8 | 10 -> 16 | 11 -> 32            entity ids (0x7FF78A76B800); statescript's w_var
                                                     (0x7FF789C631B0) is the same
  w_u16      0 -> 4 | 10 -> 8 | 11 -> 16             statescript indexes (0x7FF789C63320)
  w_u32      0 -> 16 | 10 -> 24 | 11 -> 32           statescript frame numbers (0x7FF789C63000)
  guid index 1 -> 12 | 01 -> 20 | 00 -> 32           a GUID's low 32 bits, the type comes from context
"""

import struct


class BitWriter:
    def __init__(self) -> None:
        self.value = 0
        self.count = 0

    def bit(self, flag) -> None:
        if flag:
            self.value |= 1 << self.count
        self.count += 1

    def bits(self, value: int, count: int) -> None:
        value = int(value)
        if value < 0 or value >> count:
            raise ValueError(f"{value} does not fit in {count} bits")
        self.value |= value << self.count
        self.count += count

    def signed(self, value: int, count: int) -> None:
        self.bits(int(value) & ((1 << count) - 1), count)

    def var_a(self, value: int) -> None:
        if value < 1 << 18:
            self.bit(1)
            self.bits(value, 18)
        elif value < 1 << 24:
            self.bits(0b10, 2)  # a 0 then a 1
            self.bits(value, 24)
        else:
            self.bits(0, 2)
            self.bits(value, 32)

    def var_b(self, value: int) -> None:
        if value < 1 << 8:
            self.bit(0)
            self.bits(value, 8)
        elif value < 1 << 16:
            self.bits(0b01, 2)  # a 1 then a 0
            self.bits(value, 16)
        else:
            self.bits(0b11, 2)
            self.bits(value, 32)

    def _widths(self, value: int, small: int, middle: int, large: int) -> None:
        """0 + small bits | 10 + middle | 11 + large (the first selector bit written first)."""
        if value < 1 << small:
            self.bit(0)
            self.bits(value, small)
        elif value < 1 << middle:
            self.bits(0b01, 2)
            self.bits(value, middle)
        else:
            self.bits(0b11, 2)
            self.bits(value, large)

    def w_u16(self, value: int) -> None:
        self._widths(value, 4, 8, 16)

    def w_var(self, value: int) -> None:
        self._widths(value, 8, 16, 32)

    def w_u32(self, value: int) -> None:
        self._widths(value, 16, 24, 32)

    def guid_index(self, guid: int) -> None:
        index = int(guid) & 0xFFFFFFFF
        if index < 1 << 12:
            self.bit(1)
            self.bits(index, 12)
        elif index < 1 << 20:
            self.bits(0b10, 2)
            self.bits(index, 20)
        else:
            self.bits(0, 2)
            self.bits(index, 32)

    def entity_id(self, entity: int | None) -> None:
        """2 type bits, then var_b of the rest. The client rebuilds 0x80000000 | type << 29 | value;
        a value of 0 means no entity (0x7FF789CC0F70)."""
        entity = entity or 0
        self.bits((entity >> 29) & 3, 2)
        self.var_b(entity & 0x1FFFFFFF)

    def f32(self, value: float) -> None:
        self.bits(struct.unpack("<I", struct.pack("<f", float(value)))[0], 32)

    def raw(self, data: bytes) -> None:
        for byte in data:
            self.bits(byte, 8)

    def align(self) -> None:
        self.count = (self.count + 7) & ~7

    def append(self, other: "BitWriter") -> None:
        self.value |= other.value << self.count
        self.count += other.count

    def getvalue(self) -> bytes:
        return self.value.to_bytes((self.count + 7) // 8, "little")


class BitReader:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = bytes(data)
        self.pos = pos
        self.size = len(self.data) * 8
        self._number = int.from_bytes(self.data, "little")

    def left(self) -> int:
        return self.size - self.pos

    def bit(self) -> int:
        return self.bits(1)

    def bits(self, count: int) -> int:
        if self.pos + count > self.size:
            raise EOFError("the bit stream ends here")
        value = (self._number >> self.pos) & ((1 << count) - 1)
        self.pos += count
        return value

    def signed(self, count: int) -> int:
        value = self.bits(count)
        return value - (1 << count) if value >> (count - 1) else value

    def var_a(self) -> int:
        if self.bit():
            return self.bits(18)
        if self.bit():
            return self.bits(24)
        return self.bits(32)

    def var_b(self) -> int:
        if not self.bit():
            return self.bits(8)
        if self.bit():
            return self.bits(32)
        return self.bits(16)

    def entity_id(self) -> int | None:
        kind = self.bits(2)
        value = self.var_b()
        return (0x80000000 | kind << 29 | value) if value & 0x1FFFFFFF else None

    def align(self) -> None:
        self.pos = (self.pos + 7) & ~7

    def rest(self) -> bytes:
        """Every bit left, moved down to bit 0."""
        left = self.left()
        if left <= 0:
            return b""
        value = (self._number >> self.pos) & ((1 << left) - 1)
        return value.to_bytes((left + 7) // 8, "little")
