"""Read what a PS4 / PS5 package is, from its header only (a few KB, never the whole file).

PS4 .pkg: big-endian header, magic 0x7F434E54 ("\\x7fCNT"), content id at 0x40 (36 bytes),
an entry table (count at 0x10, offset at 0x18, 32-byte entries) whose entry 0x1000 is param.sfo.
PS5 .pkg: magic 0x7F464948 ("\\x7fFIH"); its layout is different and encrypted earlier, so we
take the content id from the file name when it carries one.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

PS4_MAGIC = 0x7F434E54
PS5_MAGIC = 0x7F464948
_CONTENT_ID = re.compile(r"[A-Z]{2}\d{4}-(?:CUSA|PPSA|PCJS|PLJM|PCAS)\d{5}_00-[A-Z0-9_]{16}")
_TITLE_ID = re.compile(r"(?:CUSA|PPSA)\d{5}")
_CATEGORY = {"gd": "game", "gp": "update", "ac": "dlc", "gde": "game", "gdc": "game", "gpc": "update"}


@dataclass
class PkgInfo:
    platform: str = ""        # ps4 | ps5 | unknown
    content_id: str = ""
    title_id: str = ""
    title: str = ""
    category: str = ""        # game | update | dlc | app | ""
    version: str = ""
    app_version: str = ""
    sfo: dict[str, str] = field(default_factory=dict)

    def as_meta(self) -> dict[str, str]:
        return {k: v for k, v in {
            "platform": self.platform, "content_id": self.content_id, "title_id": self.title_id,
            "pkg_title": self.title, "category": self.category, "version": self.version,
            "app_version": self.app_version,
        }.items() if v}


def parse_sfo(data: bytes) -> dict[str, str]:
    """param.sfo -> {key: value} (strings and ints as str)."""
    if len(data) < 20 or data[:4] != b"\x00PSF":
        return {}
    key_off, data_off, count = struct.unpack_from("<III", data, 8)
    out: dict[str, str] = {}
    for i in range(count):
        base = 20 + i * 16
        if base + 16 > len(data):
            break
        k_rel, fmt, length, _max_len, d_rel = struct.unpack_from("<HHIII", data, base)
        k_end = data.find(b"\x00", key_off + k_rel)
        if k_end < 0:
            continue
        key = data[key_off + k_rel:k_end].decode("ascii", "replace")
        raw = data[data_off + d_rel: data_off + d_rel + length]
        if fmt == 0x0404:
            out[key] = str(struct.unpack_from("<I", raw.ljust(4, b"\0"))[0]) if raw else "0"
        else:
            out[key] = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return out


def _read_at(fp: BinaryIO, off: int, n: int) -> bytes:
    fp.seek(off)
    return fp.read(n)


def read_pkg(path: Path | str) -> PkgInfo:
    p = Path(path)
    info = PkgInfo()
    try:
        with p.open("rb") as fp:
            head = fp.read(0x100)
            if len(head) < 0x60:
                return _from_name(info, p)
            magic = struct.unpack_from(">I", head, 0)[0]
            if magic == PS4_MAGIC:
                info.platform = "ps4"
                info.content_id = head[0x40:0x40 + 36].split(b"\x00", 1)[0].decode("ascii", "replace")
                entry_count = struct.unpack_from(">I", head, 0x10)[0]
                table_off = struct.unpack_from(">I", head, 0x18)[0]
                if 0 < entry_count < 4096 and table_off:
                    table = _read_at(fp, table_off, entry_count * 32)
                    for i in range(entry_count):
                        eid, _name_off, _f1, _f2, off, size = struct.unpack_from(">IIIIII", table, i * 32)
                        if eid == 0x1000 and 0 < size < 1 << 20:      # param.sfo
                            info.sfo = parse_sfo(_read_at(fp, off, size))
                            break
            elif magic == PS5_MAGIC:
                info.platform = "ps5"
            else:
                info.platform = "unknown"
    except OSError:
        return _from_name(info, p)
    if info.sfo:
        info.title = info.sfo.get("TITLE", "")
        info.title_id = info.sfo.get("TITLE_ID", "")
        info.content_id = info.sfo.get("CONTENT_ID", info.content_id)
        info.category = _CATEGORY.get(info.sfo.get("CATEGORY", "").lower(), "")
        info.version = info.sfo.get("VERSION", "")
        info.app_version = info.sfo.get("APP_VER", "")
    return _from_name(info, p)


def _from_name(info: PkgInfo, p: Path) -> PkgInfo:
    """Fill what the header did not give from the file name."""
    if not info.content_id:
        m = _CONTENT_ID.search(p.name)
        if m:
            info.content_id = m.group(0)
    if not info.title_id:
        m = _TITLE_ID.search(info.content_id or p.name)
        if m:
            info.title_id = m.group(0)
    if not info.platform or info.platform == "unknown":
        if info.title_id.startswith("CUSA"):
            info.platform = "ps4"
        elif info.title_id.startswith("PPSA"):
            info.platform = "ps5"
    if not info.category:
        low = p.name.lower()
        if "update" in low or "patch" in low or "-a0" in low and "-v0" in low:
            info.category = "update"
        elif "dlc" in low:
            info.category = "dlc"
    return info
