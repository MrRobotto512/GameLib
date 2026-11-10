"""Tell a game file from something that merely has a game's extension.

The scanner used to trust extensions alone, which made an unpacked disc's IOP module a
PlayStation game (.img), every README.md in an emulator's source tree a Mega Drive game, and
left the real .iso next to them out because nine consoles use .iso. Here we look inside:

- a file that is plain text is never a game (except .cue/.m3u/.gdi, which are text by design);
- a disc image (.iso/.img) has to carry a disc signature: ISO 9660, GameCube, Wii or Xbox;
- a Mixed source resolves the shared extensions it can: an ISO 9660 image is read far enough to
  find SYSTEM.CNF (PS2 vs PSX), PSP_GAME / PS3_GAME, the Sega CD header or the Xbox magic;
  a .pkg is PS3 or PS4 by its magic; a .bin is Genesis by its SEGA header, else Atari.

Folders that are clearly not shelves of games are not walked at all: an unpacked disc
(SYSTEM.CNF inside) or a software project (.git, CMakeLists.txt, package.json ...)."""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path

HEAD = 0x10100                                   # enough for the Xbox magic at 0x10000
MIN_DISC = 0x10000                               # no disc image is smaller than this
_SKIP_MARKERS = {".git", ".github", ".gitignore", ".hg", ".svn", "cmakelists.txt", "package.json", "cargo.toml",
                 "pyproject.toml", "setup.py", "system.cnf"}
_SKIP_MARKER_EXTS = (".sln", ".slnx", ".xcodeproj", ".csproj")
_BOOT = re.compile(rb"BOOT(2?)\s*=\s*cdrom", re.I)
_CTRL = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")             # control bytes no text file has


def is_project_dir(path: Path) -> bool:
    """A folder that is an unpacked disc or a source tree, not a shelf of games."""
    try:
        names = [n.lower() for n in os.listdir(path)]
    except OSError:
        return False
    return any(n in _SKIP_MARKERS or n.endswith(_SKIP_MARKER_EXTS) for n in names)


def head(path: Path, n: int = HEAD) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def is_text(data: bytes) -> bool:
    """Plain text: decodes as UTF-8, no control bytes, and has line breaks or spaces (a lone
    run of letters, such as the stub files in tests, is not called text)."""
    if not data:
        return False
    sample = data[:4096]
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:]
    if _CTRL.search(sample):
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\n" in sample or b" " in sample


def disc_signature(data: bytes) -> str:
    """'' or which kind of disc image this is: iso9660 | gc | wii | xbox."""
    if len(data) >= 0x8010 and b"CD001" in data[0x8000:0x8010]:
        return "iso9660"
    if len(data) >= 0x9340 and b"CD001" in data[0x9300:0x9340]:      # raw 2352-byte sectors
        return "iso9660"
    if len(data) >= 0x20:
        if struct.unpack_from(">I", data, 0x1C)[0] == 0xC2339F3D:
            return "gc"
        if struct.unpack_from(">I", data, 0x18)[0] == 0x5D1C9EA3:
            return "wii"
    if len(data) >= 0x10014 and data[0x10000:0x10014] == b"MICROSOFT*XBOX*MEDIA":
        return "xbox"
    return ""


def looks_like_game(path: Path, ext: str, size: int) -> bool:
    """False for files that cannot be what their extension says."""
    if ext in ("cue", "m3u", "gdi", "ccd"):
        return True
    data = head(path, HEAD if ext in ("iso", "img") else 4096)
    if is_text(data):
        return False
    if ext in ("iso", "img"):
        if size < MIN_DISC:
            return False
        return bool(disc_signature(data))
    return True


# ---- Mixed sources: pick the console from the contents ------------------------------------
def _iso_root(f) -> dict[bytes, tuple[int, int]]:
    """Name -> (extent LBA, length) for an ISO 9660 image's root directory (2048-byte sectors)."""
    f.seek(0x8000)
    pvd = f.read(2048)
    if pvd[1:6] != b"CD001":
        return {}
    lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
    length = min(struct.unpack_from("<I", pvd, 156 + 10)[0], 64 * 2048)
    f.seek(lba * 2048)
    d = f.read(length)
    out: dict[bytes, tuple[int, int]] = {}
    i = 0
    while i + 33 < len(d):
        rec = d[i]
        if rec == 0:
            i = (i // 2048 + 1) * 2048
            continue
        nl = d[i + 32]
        name = d[i + 33:i + 33 + nl].split(b";")[0].upper()
        out.setdefault(name, (struct.unpack_from("<I", d, i + 2)[0], struct.unpack_from("<I", d, i + 10)[0]))
        i += rec
    return out


def iso_system(path: Path) -> str:
    """Which console an .iso is for, by its contents; '' when it cannot be told."""
    try:
        with open(path, "rb") as f:
            data = f.read(HEAD)
            sig = disc_signature(data)
            if sig in ("gc", "wii", "xbox"):
                return sig
            f.seek(0xFD90000 + 0x10000)
            if f.read(20) == b"MICROSOFT*XBOX*MEDIA":
                return "xbox360"
            if sig != "iso9660":
                return ""
            if data[:14] == b"SEGADISCSYSTEM":
                return "segacd"
            root = _iso_root(f)
            if b"PSP_GAME" in root:
                return "psp"
            if b"PS3_GAME" in root:
                return "ps3"
            if b"SYSTEM.CNF" in root:
                lba, ln = root[b"SYSTEM.CNF"]
                f.seek(lba * 2048)
                m = _BOOT.search(f.read(min(ln, 4096)))
                if m:
                    return "ps2" if m.group(1) else "psx"
    except (OSError, struct.error, IndexError):
        pass
    return ""


def pkg_system(path: Path) -> str:
    return {b"\x7fCNT": "ps4", b"\x7fPKG": "ps3"}.get(head(path, 4), "")


def bin_system(path: Path, size: int) -> str:
    data = head(path, 0x110)
    if b"SEGA" in data[0x100:0x110]:
        return "genesis"
    if 0 < size <= 0x10000 and size & (size - 1) == 0:
        return "atari2600"
    return ""


def resolve_mixed(path: Path, ext: str, size: int) -> str:
    """The system id for a file whose extension several consoles share; '' to leave it out."""
    if ext == "iso":
        return iso_system(path)
    if ext == "pkg":
        return pkg_system(path)
    if ext == "bin":
        return bin_system(path, size)
    return ""
