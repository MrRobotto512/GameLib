"""Box scans from LaunchBox's database (offline: a tiny Metadata.xml stands in for the real one)."""
import io
import shutil
import subprocess
from pathlib import Path

import pytest

from gameshop import launchbox
from gameshop.switchdb import NotReady

XML = b"""<?xml version="1.0" encoding="utf-8"?>
<LaunchBox>
  <Game><Name>Jet Moto 2</Name><DatabaseID>11</DatabaseID><Platform>Sony Playstation</Platform><Overview>Racing.</Overview></Game>
  <Game><Name>The Legend of Zelda: Ocarina of Time</Name><DatabaseID>12</DatabaseID><Platform>Nintendo 64</Platform></Game>
  <Game><Name>Halo 3</Name><DatabaseID>13</DatabaseID><Platform>Microsoft Xbox 360</Platform></Game>
  <Game><Name>Not Ours</Name><DatabaseID>14</DatabaseID><Platform>Philips CD-i</Platform></Game>
  <GameAlternateName><AlternateName>Zelda no Densetsu: Toki no Ocarina</AlternateName><DatabaseID>12</DatabaseID></GameAlternateName>
  <GameImage><DatabaseID>11</DatabaseID><FileName>jm2-back-eu.jpg</FileName><Type>Box - Back</Type><Region>Europe</Region></GameImage>
  <GameImage><DatabaseID>11</DatabaseID><FileName>jm2-back-us.jpg</FileName><Type>Box - Back</Type><Region>North America</Region></GameImage>
  <GameImage><DatabaseID>11</DatabaseID><FileName>jm2-front.jpg</FileName><Type>Box - Front</Type><Region>North America</Region></GameImage>
  <GameImage><DatabaseID>11</DatabaseID><FileName>jm2-3d.jpg</FileName><Type>Box - 3D</Type><Region>North America</Region></GameImage>
  <GameImage><DatabaseID>13</DatabaseID><FileName>halo-front-remake.jpg</FileName><Type>Box - Front - Reconstructed</Type><Region>Europe</Region></GameImage>
  <GameImage><DatabaseID>13</DatabaseID><FileName>halo-front-eu.jpg</FileName><Type>Box - Front</Type><Region>Europe</Region></GameImage>
  <GameImage><DatabaseID>13</DatabaseID><FileName>halo-front-us.jpg</FileName><Type>Box - Front</Type><Region>North America</Region></GameImage>
  <GameImage><DatabaseID>13</DatabaseID><FileName>halo-back-eu.jpg</FileName><Type>Box - Back</Type><Region>Europe</Region></GameImage>
  <GameImage><DatabaseID>12</DatabaseID><FileName>oot-back.jpg</FileName><Type>Box - Back</Type></GameImage>
  <GameImage><DatabaseID>14</DatabaseID><FileName>cdi-back.jpg</FileName><Type>Box - Back</Type></GameImage>
</LaunchBox>"""


def test_index_keeps_box_scans_of_our_consoles_only():
    idx = launchbox.build_index(io.BytesIO(XML), set(launchbox.PLATFORMS.values()))
    psx = idx["Sony Playstation"]
    assert sorted(f[1] for f in psx["jet moto 2"]["b"]) == ["jm2-back-eu.jpg", "jm2-back-us.jpg"]
    assert psx["jet moto 2"]["f"] == [["North America", "jm2-front.jpg"]]                      # no 3D renders
    n64 = idx["Nintendo 64"]
    assert n64["legend of zelda ocarina of time"] == n64["zelda no densetsu toki no ocarina"] == {"b": [["", "oot-back.jpg"]]}
    assert [f[1] for f in idx["Microsoft Xbox 360"]["halo 3"]["f"]][-1] == "halo-front-remake.jpg"   # real scans first
    assert "Philips CD-i" not in idx


def test_front_and_back_come_from_the_same_box(tmp_path):
    lb = launchbox.LaunchBox(tmp_path)
    lb.index = launchbox.build_index(io.BytesIO(XML), set(launchbox.PLATFORMS.values()))
    halo = {"system": "xbox360", "title": "Halo 3", "rel": "Halo 3 (USA).iso", "region": "USA"}
    # a USA game, but only the European box has a back: its front goes with it
    assert lb.urls(halo, "back")[0].endswith("halo-back-eu.jpg")
    assert lb.urls(halo, "front")[0].endswith("halo-front-eu.jpg")
    jm = {"system": "psx", "title": "Jet Moto 2", "rel": "Jet Moto 2 (Europe).chd", "region": "Europe"}
    # a European game whose European box has no front scan: both sides from the American one
    assert lb.urls(jm, "front")[0].endswith("jm2-front.jpg")
    assert lb.urls(jm, "back")[0].endswith("jm2-back-us.jpg")


def test_a_back_is_served_in_its_fronts_shape(tmp_path):
    from PIL import Image

    from gameshop.art import ArtCache
    art = ArtCache.__new__(ArtCache)
    art.dir = tmp_path
    Image.new("RGB", (400, 560), "red").save(art.path_for("g", "front"))        # 0.714
    Image.new("RGB", (900, 1300), "blue").save(art.path_for("g", "back"))       # 0.692: the same box, scanned a little off
    Image.new("RGB", (1500, 1000), "green").save(art.path_for("h", "back"))     # another edition's, far off
    shape = art.shape("g")
    with Image.open(art.thumb(art.path_for("g", "back"), 300, shape)) as im:
        assert im.size == (300, 420)
    with Image.open(art.thumb(art.path_for("h", "back"), 300, shape)) as im:
        assert im.size == (300, 420)
    assert art.version("g") and not art.version("nothing")


def test_back_urls_prefer_the_items_region(tmp_path):
    lb = launchbox.LaunchBox(tmp_path)
    item = {"system": "psx", "title": "Jet Moto 2", "rel": "Jet Moto 2 (USA).chd", "region": "USA"}
    with pytest.raises(NotReady):                       # before "Download back covers": nothing recorded
        lb.back_urls(item, "back")
    lb.index = launchbox.build_index(io.BytesIO(XML), set(launchbox.PLATFORMS.values()))
    assert lb.back_urls(item, "back")[0] == "https://images.launchbox-app.com/jm2-back-us.jpg"
    assert lb.back_urls({**item, "region": "Europe"}, "back")[1].endswith("jm2-back-eu.jpg")   # its own region next (no front there)
    assert lb.back_urls(item, "box") is None                  # the store's own covers are not LaunchBox's
    zelda = {"system": "n64", "title": "Legend of Zelda, The - Ocarina of Time", "rel": "x.z64", "region": "USA"}
    assert lb.back_urls(zelda, "back") == ["https://images.launchbox-app.com/oot-back.jpg"]
    assert lb.back_urls({"system": "scummvm", "title": "x", "rel": "x"}, "back") == []


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_store_scripts_parse():
    """A syntax error in one of the store's scripts leaves the page blank: catch it here."""
    web = Path(__file__).resolve().parent.parent / "gameshop" / "web"
    for js in ("app.js", "shelf.js"):
        r = subprocess.run(["node", "--check", str(web / js)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
