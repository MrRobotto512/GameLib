"""The scanner looks inside files: text is never a game, disc images need a disc signature,
Mixed sources tell shared extensions apart by content, and project folders are not walked."""
import struct
from pathlib import Path

from gameshop.indexer import Indexer
from gameshop.sniff import bin_system, is_text, iso_system, looks_like_game, pkg_system
from gameshop.systems import load_systems


def _iso9660(path: Path, root_files: dict[str, bytes]) -> None:
    """A tiny ISO 9660 image: PVD at sector 16, root directory at sector 18, files after it."""
    sectors = [bytearray(2048) for _ in range(40)]                 # 80 KB: past the smallest disc
    pvd = sectors[16]
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    struct.pack_into("<I", pvd, 156 + 2, 18)          # root dir extent
    struct.pack_into("<I", pvd, 156 + 10, 2048)       # root dir length
    rec = bytearray()
    for n, (name, data) in enumerate(root_files.items()):
        sectors[19 + n][:len(data)] = data
        ident = (name + ";1").encode() if "." in name else name.encode()
        r = bytearray(33 + len(ident))
        r[0] = len(r) + (len(r) & 1)
        struct.pack_into("<I", r, 2, 19 + n)
        struct.pack_into("<I", r, 10, len(data))
        r[32] = len(ident)
        r[33:33 + len(ident)] = ident
        r += b"\0" * (len(r) & 1)
        rec += r
    sectors[18][:len(rec)] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(sectors))


def test_text_and_disc_signatures(tmp_path: Path):
    md = tmp_path / "README.md"
    md.write_text("# PCSX2\n\nThe PlayStation 2 emulator.\n")
    assert is_text(md.read_bytes())
    assert not looks_like_game(md, "md", md.stat().st_size)
    stub = tmp_path / "stub.sfc"
    stub.write_bytes(b"x" * 10)                                   # the test stubs stay games
    assert looks_like_game(stub, "sfc", 10)
    module = tmp_path / "IOPRP280.IMG"
    module.write_bytes(b"RESET\0\0\0" + bytes(range(256)) * 1100)  # binary, no disc inside
    assert not looks_like_game(module, "img", module.stat().st_size)
    _iso9660(tmp_path / "game.iso", {"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_219.31;1\r\n"})
    assert looks_like_game(tmp_path / "game.iso", "iso", (tmp_path / "game.iso").stat().st_size)


def test_iso_system_by_contents(tmp_path: Path):
    _iso9660(tmp_path / "ps2.iso", {"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_219.31;1\r\nVER = 1.00\r\n"})
    _iso9660(tmp_path / "psx.iso", {"SYSTEM.CNF": b"BOOT = cdrom:\\SLUS_000.67;1\r\n"})
    _iso9660(tmp_path / "psp.iso", {"PSP_GAME": b"", "UMD_DATA.BIN": b"x"})
    _iso9660(tmp_path / "ps3.iso", {"PS3_GAME": b""})
    assert iso_system(tmp_path / "ps2.iso") == "ps2"
    assert iso_system(tmp_path / "psx.iso") == "psx"
    assert iso_system(tmp_path / "psp.iso") == "psp"
    assert iso_system(tmp_path / "ps3.iso") == "ps3"
    gc = tmp_path / "gc.iso"
    gc.write_bytes(b"GALE01\0" + b"\0" * 0x15 + struct.pack(">I", 0xC2339F3D) + b"\0" * 0x20000)
    assert iso_system(gc) == "gc"
    xb = tmp_path / "xbox.iso"
    xb.write_bytes(b"\0" * 0x10000 + b"MICROSOFT*XBOX*MEDIA" + b"\0" * 64)
    assert iso_system(xb) == "xbox"
    nothing = tmp_path / "blob.iso"
    nothing.write_bytes(bytes(range(256)) * 300)
    assert iso_system(nothing) == ""


def test_pkg_and_bin_by_contents(tmp_path: Path):
    (tmp_path / "a.pkg").write_bytes(b"\x7fCNT" + b"\0" * 64)
    (tmp_path / "b.pkg").write_bytes(b"\x7fPKG" + b"\0" * 64)
    assert pkg_system(tmp_path / "a.pkg") == "ps4" and pkg_system(tmp_path / "b.pkg") == "ps3"
    md = tmp_path / "sonic.bin"
    md.write_bytes(b"\0" * 0x100 + b"SEGA MEGA DRIVE " + b"\0" * 0x1000)
    assert bin_system(md, md.stat().st_size) == "genesis"
    vcs = tmp_path / "pitfall.bin"
    vcs.write_bytes(bytes(range(256)) * 16)                        # 4 KB
    assert bin_system(vcs, 4096) == "atari2600"


def test_mixed_source_resolves_and_skips_junk(tmp_path: Path):
    src = tmp_path / "PS2"
    game = src / "Toy Story 3"
    _iso9660(game / "Toy Story 3 (USA).iso", {"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_219.31;1\r\n"})
    # an unpacked copy of the disc next to it: SYSTEM.CNF marks it, nothing inside is indexed
    ex = game / "extracted"
    ex.mkdir()
    (ex / "SYSTEM.CNF").write_text("BOOT2 = cdrom0:\\SLUS_219.31;1\r\n")
    (ex / "MODULES").mkdir()
    (ex / "MODULES" / "IOPRP280.IMG").write_bytes(b"RESET\0\0\0" + bytes(range(256)) * 1100)
    # an emulator's source tree: CMakeLists.txt marks it, the READMEs are not Genesis games
    emu = src / "pcsx2" / "pcsx2"
    (emu / "3rdparty").mkdir(parents=True)
    (emu / "CMakeLists.txt").write_text("project(pcsx2)\n")
    (emu / "README.md").write_text("# PCSX2\n")
    (emu / "3rdparty" / "README.md").write_text("# deps\n")
    # a loose README and a real cartridge
    (src / "notes.md").write_text("some notes\nabout the games\n")
    (src / "Mario 64 (USA).z64").write_bytes(b"\x80\x37\x12\x40" + b"\0" * 100)
    items = Indexer(load_systems()).scan_source(str(src), "mixed")
    assert sorted((i["system"], i["title"]) for i in items) == [("n64", "Mario 64"), ("ps2", "Toy Story 3")]


def test_typed_source_drops_text_and_fake_discs(tmp_path: Path):
    src = tmp_path / "psx"
    (src / "MODULES").mkdir(parents=True)
    (src / "MODULES" / "IOPRP280.IMG").write_bytes(b"RESET\0\0\0" + bytes(range(256)) * 1100)
    (src / "Crash.pbp").write_bytes(b"\0PBP" + b"\0" * 100)
    (src / "readme.img").write_text("this is not a disc\n")
    items = Indexer(load_systems()).scan_source(str(src), "psx")
    assert [i["title"] for i in items] == ["Crash"]
