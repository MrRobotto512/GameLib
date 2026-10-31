import struct
from pathlib import Path

from gameshop.db import Database
from gameshop.metadata import MetadataStore
from gameshop.rdb import MAGIC, iter_records, load_games
from gameshop.systems import load_systems


def _pack_str(s: str) -> bytes:
    b = s.encode()
    return (bytes([0xA0 | len(b)]) if len(b) < 32 else bytes([0xD9, len(b)])) + b


def _pack_map(d: dict) -> bytes:
    out = bytes([0x80 | len(d)])
    for k, v in d.items():
        out += _pack_str(k)
        if isinstance(v, str):
            out += _pack_str(v)
        elif isinstance(v, bytes):
            out += bytes([0xC4, len(v)]) + v
        elif isinstance(v, int):
            out += bytes([0xCD]) + struct.pack(">H", v) if v > 0x7F else bytes([v])
    return out


def _fake_rdb(records: list[dict]) -> bytes:
    body = b"".join(_pack_map(r) for r in records)
    header = MAGIC + struct.pack(">Q", 16 + len(body))
    return header + body + _pack_map({"count": len(records)})


def test_rdb_roundtrip_and_lookup(tmp_path: Path):
    data = _fake_rdb([
        {"name": "Super Mario 64 (USA)", "rom_name": "Super Mario 64 (USA).z64", "releaseyear": 1996, "developer": "Nintendo",
         "publisher": "Nintendo", "genre": "Platform", "users": 1, "crc": b"\x12\x34\x56\x78"},
        {"name": "GoldenEye 007 (USA)", "rom_name": "GoldenEye 007 (USA).z64", "releaseyear": 1997, "publisher": "Nintendo", "users": 4},
    ])
    recs = list(iter_records(data))
    assert len(recs) == 2 and recs[0]["releaseyear"] == 1996 and recs[0]["crc"] == b"\x12\x34\x56\x78"
    games = load_games(data)
    assert len(games) == 2 and "crc" not in games[0]
    db = Database(tmp_path / "m.db")
    store = MetadataStore(db, load_systems(), tmp_path / "rdb")
    (tmp_path / "rdb" / "Nintendo - Nintendo 64.rdb").write_bytes(data)        # pre-seeded: no download
    item = {"id": "x1", "system": "n64", "title": "GoldenEye 007", "region": "USA", "rel": "GoldenEye 007 (USA).z64",
            "meta": {"stem": "GoldenEye 007 (USA)"}}
    facts = store.get(item)
    assert facts == {"year": 1997, "publisher": "Nintendo", "players": 4}
    # cached in sqlite afterwards, even with the index gone
    store._index.clear()
    assert store.get(item, fetch=False) == facts
    miss = {"id": "x2", "system": "n64", "title": "Nothing Here", "region": "", "rel": "Nothing Here.z64", "meta": {"stem": "Nothing Here"}}
    assert store.get(miss) is None
