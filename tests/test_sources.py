"""GameTDB covers by code or name, serial-keyed PS1 / PS2 covers, PS4 / PS5 icons — offline."""
from pathlib import Path

import gameshop.art as art
from gameshop.art import ArtCache
from gameshop.covers import disc_serial, patch_site_icon_urls, serial_cover_urls, title_id
from gameshop.db import Database
from gameshop.gametdb import GameTDB, code_region, parse_list
from gameshop.systems import load_systems

SWITCH = """TITLES = https://www.gametdb.com (type: Switch language: EN version: 1)
AXN7A = The Legend of Zelda: Tears of the Kingdom
AXN7B = The Legend of Zelda: Tears of the Kingdom
A2BAA = Animal Crossing: New Horizons
A2BAB = Animal Crossing: New Horizons
A2BAC = Animal Crossing: New Horizons
AB38A = Big Buck Hunter: Ultimate Trophy
"""
PS3 = """TITLES = ps3
BLUS30443 = Demon's Souls
BLES00932 = Demon's Souls
BCJS30022 = Demon's Souls
"""
WII = """TITLES = wii
RMCE01 = Mario Kart Wii
RMCP01 = Mario Kart Wii
GALE01 = Super Smash Bros. Melee
"""


def sw(title, region="", rel=None):
    return {"id": "x", "system": "switch", "title": title, "region": region, "rel": rel or title + ".nsp", "meta": {"stem": title}}


def test_parse_and_regions():
    assert parse_list(SWITCH)["AXN7A"] == "The Legend of Zelda: Tears of the Kingdom"
    assert "TITLES" not in parse_list(SWITCH)
    assert code_region("switch", "AXN7B") == "EN" and code_region("wii", "RMCP01") == "EN"
    assert code_region("ps3", "BCJS30022") == "JA" and code_region("ps3", "BLUS30443") == "US"


def test_switch_by_name_with_hint(tmp_path: Path):
    db = GameTDB(tmp_path, enabled=lambda: False, hint=lambda it: ["Animal Crossing™: New Horizons"] if it["title"].startswith("Animal") else [])
    db.load_from("switchtdb", SWITCH)
    urls = db.urls(sw("Animal Crossing New Horizons"))
    assert urls[0] == "https://art.gametdb.com/switch/coverM/US/A2BAA.jpg"       # US code, US cover first
    assert "https://art.gametdb.com/switch/cover/EN/A2BAB.jpg" in urls
    # the file's own name is enough when it is the retail one; punctuation does not count
    assert db.urls(sw("The Legend of Zelda - Tears of the Kingdom v1.2.1", region="Europe"))[0].endswith("/EN/AXN7B.jpg")
    assert db.urls(sw("Some Homebrew")) is None
    assert db.urls(sw("Big Buck Hunter Ultimate Trophy"), kind="snap") is None


def test_codes_in_names_and_other_consoles(tmp_path: Path):
    db = GameTDB(tmp_path, enabled=lambda: False)
    db.load_from("ps3tdb", PS3)
    db.load_from("wiitdb", WII)
    ps3 = {"id": "p", "system": "ps3", "title": "Demon's Souls", "region": "", "rel": "BLES00932/PS3_GAME/PARAM.SFO", "meta": {"stem": "PARAM"}}
    assert db.urls(ps3)[0] == "https://art.gametdb.com/ps3/coverM/EN/BLES00932.jpg"
    wii = {"id": "w", "system": "wii", "title": "Mario Kart Wii", "region": "USA", "rel": "Mario Kart Wii [RMCE01].wbfs", "meta": {"stem": "Mario Kart Wii [RMCE01]"}}
    assert db.urls(wii)[0] == "https://art.gametdb.com/wii/cover/US/RMCE01.png"
    gc = {"id": "g", "system": "gc", "title": "Super Smash Bros. Melee", "region": "USA", "rel": "x.iso", "meta": {}}
    assert db.urls(gc)[0] == "https://art.gametdb.com/wii/cover/US/GALE01.png"
    assert db.urls({"id": "n", "system": "nes", "title": "Mario Kart Wii", "rel": "x", "meta": {}}) is None


def test_serials_and_title_ids():
    ps2 = {"system": "ps2", "title": "Kingdom Hearts", "rel": "Kingdom Hearts (USA) [SLUS-20370].chd", "meta": {}}
    assert disc_serial(ps2) == "SLUS-20370"
    assert serial_cover_urls(ps2) == ["https://raw.githubusercontent.com/xlenore/ps2-covers/main/covers/default/SLUS-20370.jpg"]
    assert disc_serial({"system": "psx", "rel": "SCUS_941.63/game.bin", "meta": {}}) == "SCUS-94163"
    assert serial_cover_urls({"system": "psx", "rel": "Crash Bandicoot.chd", "meta": {}}) is None
    ps4 = {"system": "ps4", "rel": "Bloodborne.pkg", "meta": {"title_id": "CUSA00900"}}
    assert title_id(ps4) == "CUSA00900"
    page = '<img src="https://cdn.orbispatches.com/titles/CUSA00900_c9f3/icon0.webp">'
    assert patch_site_icon_urls(ps4, fetch=lambda u: page if u == "https://orbispatches.com/CUSA00900" else None) == \
        ["https://cdn.orbispatches.com/titles/CUSA00900_c9f3/icon0.webp"]
    assert patch_site_icon_urls({"system": "ps5", "rel": "x.pkg", "meta": {}}) is None


def test_artcache_tries_lists_then_fallbacks(tmp_path: Path, monkeypatch):
    served = {"https://a/2.jpg": b"\xff\xd8A", "https://b/x.jpg": b"\xff\xd8B"}
    monkeypatch.setattr(art, "_get", lambda url, timeout=8.0: served.get(url))
    monkeypatch.setattr(art, "_get_status", lambda url, timeout=8.0: (404, None))
    cache = ArtCache(Database(tmp_path / "t.db"), load_systems(), tmp_path / "art")
    cache.providers.append(lambda it, kind: ["https://a/1.jpg", "https://a/2.jpg"] if it["id"] == "one" else None)
    cache.fallbacks.append(lambda it, kind: "https://b/x.jpg" if it["id"] == "two" else None)
    one = {"id": "one", "system": "switch", "title": "One", "rel": "one.nsp", "meta": {}}
    two = {"id": "two", "system": "ps2", "title": "Two", "rel": "two.chd", "meta": {}}
    three = {"id": "three", "system": "ps2", "title": "Three", "rel": "three.chd", "meta": {}}
    assert cache.get(one).read_bytes() == b"\xff\xd8A"
    assert cache.get(two).read_bytes() == b"\xff\xd8B"       # libretro missed (404), the fallback had it
    assert cache.get(three) is None
    assert cache.db.art("three")["status"] == "missing"


def test_placeholder_covers_are_rejected(tmp_path: Path, monkeypatch):
    import io
    from PIL import Image
    from gameshop.art import ArtSource
    from gameshop.gametdb import is_placeholder
    def jpeg(color):
        buf = io.BytesIO(); Image.new("RGB", (40, 60), color).save(buf, "JPEG"); return buf.getvalue()
    red, blue = jpeg((230, 0, 18)), jpeg((20, 40, 200))
    assert is_placeholder(red) and not is_placeholder(blue)
    served = {"https://g/placeholder.jpg": red, "https://icon/x.jpg": blue}
    monkeypatch.setattr(art, "_get", lambda url, timeout=8.0: served.get(url))
    cache = ArtCache(Database(tmp_path / "t.db"), load_systems(), tmp_path / "art")
    cache.providers.append(ArtSource(lambda it, kind: ["https://g/placeholder.jpg"], ["switch"], accept=lambda d: not is_placeholder(d)))
    cache.providers.append(ArtSource(lambda it, kind: "https://icon/x.jpg", ["switch"]))
    item = {"id": "s", "system": "switch", "title": "Mini Metro", "rel": "m.nsp", "meta": {}}
    assert cache.get(item).read_bytes() == blue              # the placeholder fell through to the icon
    assert cache.providers[0]({"id": "n", "system": "nes", "title": "x", "rel": "x", "meta": {}}, "box") is None
