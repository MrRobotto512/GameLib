"""Switch title IDs, titledb lookups, art URLs and facts — offline, with a tiny prepared index."""
from pathlib import Path

from gameshop.indexer import Indexer
from gameshop.naming import parse_name
from gameshop.switchdb import SwitchTitleDB, classify, compact, parse_title_id
from gameshop.systems import load_systems

RAW = {
    "70010000000001": {"id": "0100CBB027BA4000", "name": "Avatar Legends™ The Fighting Game", "publisher": "Maximum Games",
                        "releaseDate": 20240918, "numberOfPlayers": 2, "category": ["Fighting", "Action"],
                        "description": "Bend the elements.\n", "iconUrl": "https://cdn/icon-avatar.jpg",
                        "bannerUrl": "https://cdn/banner-avatar.jpg", "screenshots": ["https://cdn/s1.jpg", "https://cdn/s2.jpg"]},
    "70010000000002": {"id": "01006F8002326000", "name": "Animal Crossing™: New Horizons", "publisher": "Nintendo",
                        "releaseDate": 20200320, "numberOfPlayers": 8, "category": ["Simulation"], "iconUrl": "https://cdn/acnh.jpg"},
    "70010000000003": {"id": "0100ABCD00000000", "name": "ACA NEOGEO KOF 2000", "publisher": "HAMSTER", "releaseDate": 20180315,
                        "iconUrl": "https://cdn/kof.jpg"},
    "70010000000004": {"id": "BADID", "name": "junk"},
}


def test_title_id_math():
    assert parse_title_id("#DRIVE[01000F20122FA000][US][v0]") == "01000F20122FA000"
    assert parse_title_id("ACA NEOGEO KOF 2000") is None
    assert classify("0100CBB027BA4000") == ("base", "0100CBB027BA4000")
    assert classify("0100CBB027BA4800") == ("update", "0100CBB027BA4000")
    assert classify("01006F800232712D") == ("dlc", "01006F8002326000")


def test_naming_strips_version():
    p = parse_name("Animal Crossing New Horizons v2.0.8[01006F8002326800][1966080][UPD]")
    assert p.title == "Animal Crossing New Horizons"
    assert "v2.0.8" in p.tags and "01006F8002326800" in p.tags


def test_indexer_marks_switch_dumps(tmp_path: Path):
    for n in ("Avatar Legends™ The Fighting Game [0100CBB027BA4000][v0][Base].nsp",
              "Avatar Legends™ The Fighting Game [0100CBB027BA4800][v65536][1.0.1][UPD].nsp",
              "ACA NEOGEO KOF 2000.xci"):
        (tmp_path / n).write_bytes(b"x" * 10)
    items = {it["title"] + "|" + it["meta"].get("content", "-"): it for it in Indexer(load_systems()).scan_source(str(tmp_path), "switch")}
    assert items["Avatar Legends™ The Fighting Game|base"]["meta"]["title_id"] == "0100CBB027BA4000"
    upd = items["Avatar Legends™ The Fighting Game|update"]["meta"]
    assert upd["base_id"] == "0100CBB027BA4000"
    assert "content" not in items["ACA NEOGEO KOF 2000|-"]["meta"]


def test_lookup_art_and_facts(tmp_path: Path):
    db = SwitchTitleDB(tmp_path, enabled=lambda: False)
    by_id = compact(RAW)
    assert set(by_id) == {"0100CBB027BA4000", "01006F8002326000", "0100ABCD00000000"}
    db.load_from(by_id)
    assert db.name_for("0100abcd00000000") == "ACA NEOGEO KOF 2000" and db.name_for("0000000000000000") is None

    base = {"system": "switch", "title": "Avatar Legends™ The Fighting Game", "rel": "a.nsp",
            "meta": {"stem": "Avatar Legends™ The Fighting Game [0100CBB027BA4000][v0][Base]", "title_id": "0100CBB027BA4000"}}
    assert db.art_url(base, "box") == "https://cdn/icon-avatar.jpg"
    assert db.art_url(base, "title") == "https://cdn/banner-avatar.jpg"
    assert db.art_url(base, "snap") == "https://cdn/s1.jpg"
    f = db.facts(base)
    assert f["year"] == 2024 and f["month"] == 9 and f["publisher"] == "Maximum Games" and f["players"] == 2
    assert f["genre"] == "Fighting, Action" and f["description"] == "Bend the elements."

    # an update shares the base game's art but not its description
    upd = {"system": "switch", "title": "Avatar Legends™ The Fighting Game", "rel": "u.nsp",
           "meta": {"stem": "x [0100CBB027BA4800][v65536][UPD]"}}
    assert db.art_url(upd) == "https://cdn/icon-avatar.jpg"
    assert db.facts(upd)["part"] == "Update for Avatar Legends™ The Fighting Game"
    assert "description" not in db.facts(upd)

    # DLC maps to the base id; a dump without an id matches by name
    dlc = {"system": "switch", "title": "Animal Crossing", "rel": "d.nsp", "meta": {"stem": "ACNH DLC [01006F800232712D][v0]"}}
    assert db.facts(dlc)["part"].startswith("DLC for Animal Crossing")
    noid = {"system": "switch", "title": "ACA NEOGEO KOF 2000", "rel": "k.xci", "meta": {"stem": "ACA NEOGEO KOF 2000"}}
    assert db.art_url(noid) == "https://cdn/kof.jpg"
    assert db.record({"system": "switch", "title": "Nothing Here", "rel": "n.nsp", "meta": {"stem": "Nothing Here"}}) is None


def test_not_ready_without_cache(tmp_path: Path):
    import pytest
    from gameshop.switchdb import NotReady
    db = SwitchTitleDB(tmp_path, enabled=lambda: False)       # disabled: no download, but also no answer yet
    with pytest.raises(NotReady):
        db.record({"system": "switch", "title": "x", "rel": "x.nsp", "meta": {"stem": "x [0100CBB027BA4000]"}})
