"""Title-level matching: the file's name differs from libretro's in tags, region or style."""
from pathlib import Path

from gameshop.db import Database
from gameshop.metadata import MetadataStore
from gameshop.naming import parse_name, split_camel
from gameshop.systems import load_systems


def test_whdload_names():
    p = parse_name("1000ccTurbo_v1.0")
    assert p.title == "1000cc Turbo" and p.tags == ["v1.0"]
    p = parse_name("ChaosEngine2_v1.1_AGA")
    assert p.title == "Chaos Engine 2" and p.tags == ["v1.1", "AGA"]
    assert parse_name("1869_v1.2").title == "1869"
    assert split_camel("RoboCop3") == "Robo Cop 3" or split_camel("RoboCop3") == "RoboCop 3"
    # ordinary names are untouched
    assert parse_name("Super Mario 64 (USA)").title == "Super Mario 64"
    assert parse_name("Game_Title (USA)").title == "Game_Title"


def _store(tmp_path: Path, system: str, names: list[dict]) -> MetadataStore:
    db = Database(tmp_path / "t.db")
    ms = MetadataStore(db, load_systems(), tmp_path / "rdb")
    index = {}
    titles = {}
    from gameshop.metadata import norm
    for rec in names:
        index[norm(rec["name"])] = rec
        titles.setdefault(norm(parse_name(rec["name"]).title), []).append(rec)
    ms._index[system] = index
    ms._titles[system] = titles
    return ms


def test_title_match_prefers_clean_and_regional(tmp_path: Path):
    ms = _store(tmp_path, "genesis", [
        {"name": "Ghouls 'n Ghosts Rev 2 (USA, Europe)[o]", "releaseyear": 1989},
        {"name": "Ghouls'n Ghosts (USA, Europe)", "releaseyear": 1989, "publisher": "Sega"},
        {"name": "Ghouls'n Ghosts (Japan)", "releaseyear": 1989, "publisher": "Sega"},
        {"name": "RoboCop 3 (World)", "publisher": "Flying Edge"},
        {"name": "10th Frame (USA) (Tape Port Dongle)"},
        {"name": "10th Frame (USA)", "releaseyear": 1986},
        {"name": "10th Frame (Europe)", "releaseyear": 1986},
        {"name": "Super Mario 64 - Chaos Edition (USA)[h]", "releaseyear": 2000},
    ])
    # punctuation differs: the norm of the whole name still matches exactly
    it = {"system": "genesis", "title": "Ghouls 'n Ghosts", "region": "USA, Europe", "rel": "x.md",
          "meta": {"stem": "Ghouls 'n Ghosts (USA, Europe)"}}
    assert ms.thumb_names(it)[0] == "Ghouls'n Ghosts (USA, Europe)"
    # region differs: title match, World wins over nothing
    it = {"system": "genesis", "title": "RoboCop 3", "region": "USA, Europe", "rel": "r.md", "meta": {"stem": "RoboCop 3 (USA, Europe)"}}
    assert ms.thumb_names(it) == ["RoboCop 3 (World)"]
    # no region in the file: USA first, and the plain one over the dongle variant
    it = {"system": "genesis", "title": "10th Frame", "region": "", "rel": "t.md", "meta": {"stem": "10th Frame"}}
    assert ms.thumb_names(it)[0] == "10th Frame (USA)"
    # a hack's title is not the game's title
    it = {"system": "genesis", "title": "Super Mario 64", "region": "", "rel": "m.md", "meta": {"stem": "Super Mario 64"}}
    assert ms.thumb_names(it) == []
    # facts come through the same path
    f = ms.get({"id": "i1", "system": "genesis", "title": "RoboCop 3", "region": "", "rel": "r.md", "meta": {"stem": "RoboCop 3"}})
    assert f["publisher"] == "Flying Edge"
