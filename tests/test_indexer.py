import struct
from pathlib import Path

from gameshop.indexer import Indexer
from gameshop.naming import libretro_thumb_name, parse_name
from gameshop.pkg import read_pkg
from gameshop.systems import load_systems


def test_parse_name_regions_and_tags():
    p = parse_name("Crash Bandicoot (USA) (Rev 1) [!]")
    assert p.title == "Crash Bandicoot" and p.region == "USA" and p.tags == ["Rev 1", "!"]
    p = parse_name("Final Fantasy VII (USA) (Disc 2)")
    assert p.title == "Final Fantasy VII" and p.disc == "2"
    p = parse_name("Legend of Zelda, The - Ocarina of Time (Europe) (En,Fr,De)")
    assert p.title.startswith("The Legend of Zelda") and p.region == "Europe"
    assert parse_name("The Thing").sort_title == "thing the"
    assert libretro_thumb_name("Ren & Stimpy: Ha!") == "Ren _ Stimpy_ Ha!"


def _touch(p: Path, size: int = 10):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def test_cue_grouping(tmp_path: Path):
    g = tmp_path / "psx"
    _touch(g / "Tekken 3 (USA).bin", 100)
    (g / "Tekken 3 (USA).cue").write_text('FILE "Tekken 3 (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n')
    _touch(g / "Ridge Racer (USA) (Track 1).bin", 50)
    _touch(g / "Ridge Racer (USA) (Track 2).bin", 50)
    (g / "Ridge Racer (USA).cue").write_text('FILE "Ridge Racer (USA) (Track 1).bin" BINARY\nFILE "Ridge Racer (USA) (Track 2).bin" BINARY\n')
    _touch(g / "Wipeout (USA).chd", 30)
    _touch(g / "stray.bin", 5)
    (g / "readme.txt").write_text("hi")
    items = Indexer(load_systems()).scan_source(str(g), "psx")
    titles = {i["title"]: i for i in items}
    assert set(titles) == {"Tekken 3", "Ridge Racer", "Wipeout"}
    assert titles["Ridge Racer"]["size"] == 100 + (g / "Ridge Racer (USA).cue").stat().st_size
    assert len(titles["Ridge Racer"]["files"]) == 3
    assert titles["Tekken 3"]["files"][0][0].endswith(".cue")


def test_m3u_multidisc(tmp_path: Path):
    g = tmp_path / "psx"
    for d in (1, 2):
        _touch(g / f"FF7 (Disc {d}).bin", 10)
        (g / f"FF7 (Disc {d}).cue").write_text(f'FILE "FF7 (Disc {d}).bin" BINARY\n')
    (g / "FF7.m3u").write_text("FF7 (Disc 1).cue\nFF7 (Disc 2).cue\n")
    items = Indexer(load_systems()).scan_source(str(g), "psx")
    assert len(items) == 1 and items[0]["title"] == "FF7" and len(items[0]["files"]) == 5


def test_folder_systems_and_mixed(tmp_path: Path):
    dos = tmp_path / "dos"
    _touch(dos / "Doom" / "DOOM.EXE")
    _touch(dos / "Doom" / "DOOM.WAD")
    items = Indexer(load_systems()).scan_source(str(dos), "dos")
    assert len(items) == 1 and items[0]["kind"] == "folder" and len(items[0]["files"]) == 2
    mixed = tmp_path / "mixed"
    _touch(mixed / "Mario 64 (USA).z64")
    _touch(mixed / "something.iso")       # ambiguous: ps2/psp/gc/... -> skipped
    items = Indexer(load_systems()).scan_source(str(mixed), "mixed")
    assert [i["system"] for i in items] == ["n64"]


def _fake_ps4_pkg(path: Path, content_id: str, title: str):
    # header (0x1000) + entry table at 0x1000 + param.sfo at 0x2000
    sfo_keys = b"CATEGORY\0CONTENT_ID\0TITLE\0TITLE_ID\0"
    vals = {"CATEGORY": b"gd\0", "CONTENT_ID": content_id.encode() + b"\0", "TITLE": title.encode() + b"\0", "TITLE_ID": content_id[7:16].encode() + b"\0"}
    idx = b""
    data = b""
    koff = 0
    for k in ("CATEGORY", "CONTENT_ID", "TITLE", "TITLE_ID"):
        v = vals[k]
        idx += struct.pack("<HHIII", koff, 0x0204, len(v), len(v), len(data))
        data += v
        koff += len(k) + 1
    key_off = 20 + len(idx)
    data_off = key_off + len(sfo_keys)
    sfo = b"\x00PSF" + struct.pack("<I", 0x101) + struct.pack("<III", key_off, data_off, 4) + idx + sfo_keys + data
    # the real header is magic, then at 0x08 something, 0x10 entry_count, 0x18 table_off; 0x40 content id
    head = bytearray(0x1000)
    struct.pack_into(">I", head, 0, 0x7F434E54)
    struct.pack_into(">I", head, 0x10, 1)
    struct.pack_into(">I", head, 0x18, 0x1000)
    head[0x40:0x40 + len(content_id)] = content_id.encode()
    table = struct.pack(">IIIIII", 0x1000, 0, 0, 0, 0x2000, len(sfo)) + b"\0" * 8
    blob = bytes(head) + table.ljust(0x1000, b"\0") + sfo
    path.write_bytes(blob)


def test_read_pkg(tmp_path: Path):
    p = tmp_path / "game.pkg"
    _fake_ps4_pkg(p, "UP0001-CUSA12345_00-TESTGAME00000000", "Test Game")
    info = read_pkg(p)
    assert info.platform == "ps4" and info.title == "Test Game" and info.title_id == "CUSA12345" and info.category == "game"
    q = tmp_path / "UP0001-PPSA01234_00-SOMETHING0000000-A0100-V0100.pkg"
    q.write_bytes(b"\x7fFIH" + b"\0" * 200)
    info = read_pkg(q)
    assert info.platform == "ps5" and info.title_id == "PPSA01234" and info.category == "update"
