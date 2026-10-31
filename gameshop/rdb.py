"""Read libretro-database .rdb files: a "RARCHDB" header, then msgpack maps, one per game.

Fields seen: name, description, rom_name, size, users, releasemonth, releaseyear, developer,
publisher, genre, franchise, serial, region, esrb_rating, crc/md5/sha1 (binary).
"""
from __future__ import annotations

import struct
from typing import Any, Iterator

MAGIC = b"RARCHDB\x00"


class _Reader:
    __slots__ = ("d", "i")

    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.i = data, pos

    def u(self, fmt: str, n: int) -> Any:
        v = struct.unpack_from(fmt, self.d, self.i)[0]
        self.i += n
        return v

    def take(self, n: int) -> bytes:
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def value(self) -> Any:
        b = self.d[self.i]
        self.i += 1
        if b <= 0x7F:
            return b
        if b >= 0xE0:
            return b - 0x100
        if 0x80 <= b <= 0x8F:
            return self._map(b & 0x0F)
        if 0x90 <= b <= 0x9F:
            return [self.value() for _ in range(b & 0x0F)]
        if 0xA0 <= b <= 0xBF:
            return self._str(b & 0x1F)
        if b == 0xC0:
            return None
        if b == 0xC2:
            return False
        if b == 0xC3:
            return True
        if b == 0xC4:
            return self.take(self.u(">B", 1))
        if b == 0xC5:
            return self.take(self.u(">H", 2))
        if b == 0xC6:
            return self.take(self.u(">I", 4))
        if b == 0xCA:
            return self.u(">f", 4)
        if b == 0xCB:
            return self.u(">d", 8)
        if b == 0xCC:
            return self.u(">B", 1)
        if b == 0xCD:
            return self.u(">H", 2)
        if b == 0xCE:
            return self.u(">I", 4)
        if b == 0xCF:
            return self.u(">Q", 8)
        if b == 0xD0:
            return self.u(">b", 1)
        if b == 0xD1:
            return self.u(">h", 2)
        if b == 0xD2:
            return self.u(">i", 4)
        if b == 0xD3:
            return self.u(">q", 8)
        if b == 0xD9:
            return self._str(self.u(">B", 1))
        if b == 0xDA:
            return self._str(self.u(">H", 2))
        if b == 0xDB:
            return self._str(self.u(">I", 4))
        if b == 0xDC:
            return [self.value() for _ in range(self.u(">H", 2))]
        if b == 0xDD:
            return [self.value() for _ in range(self.u(">I", 4))]
        if b == 0xDE:
            return self._map(self.u(">H", 2))
        if b == 0xDF:
            return self._map(self.u(">I", 4))
        raise ValueError(f"unsupported msgpack byte 0x{b:02x} at {self.i - 1}")

    def _str(self, n: int) -> str:
        return self.take(n).decode("utf-8", "replace")

    def _map(self, n: int) -> dict[str, Any]:
        out = {}
        for _ in range(n):
            k = self.value()
            out[k if isinstance(k, str) else str(k)] = self.value()
        return out


def iter_records(data: bytes) -> Iterator[dict[str, Any]]:
    if not data.startswith(MAGIC):
        raise ValueError("not a libretro rdb file")
    end = struct.unpack_from(">Q", data, 8)[0]
    r = _Reader(data, 16)
    while r.i < end:
        v = r.value()
        if isinstance(v, dict) and v:
            yield v


_KEEP = ("name", "rom_name", "description", "releaseyear", "releasemonth", "developer", "publisher",
         "genre", "franchise", "users", "serial", "region", "esrb_rating", "size")


def load_games(data: bytes) -> list[dict[str, Any]]:
    """Records reduced to the text fields we show."""
    out = []
    for rec in iter_records(data):
        g = {k: rec[k] for k in _KEEP if k in rec and rec[k] not in (None, "", b"")}
        if g.get("name") or g.get("rom_name"):
            out.append(g)
    return out
