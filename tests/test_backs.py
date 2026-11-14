"""Back covers from LaunchBox's database (offline: a tiny Metadata.xml stands in for the real one)."""
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
  <GameImage><DatabaseID>12</DatabaseID><FileName>oot-back.jpg</FileName><Type>Box - Back</Type></GameImage>
  <GameImage><DatabaseID>14</DatabaseID><FileName>cdi-back.jpg</FileName><Type>Box - Back</Type></GameImage>
</LaunchBox>"""


def test_index_keeps_backs_of_our_consoles_only():
    idx = launchbox.build_index(io.BytesIO(XML), set(launchbox.PLATFORMS.values()))
    psx = idx["Sony Playstation"]
    assert sorted(f[1] for f in psx["jet moto 2"]) == ["jm2-back-eu.jpg", "jm2-back-us.jpg"]    # no fronts
    n64 = idx["Nintendo 64"]
    assert n64["legend of zelda ocarina of time"] == n64["zelda no densetsu toki no ocarina"] == [["", "oot-back.jpg"]]
    assert "halo 3" not in idx["Microsoft Xbox 360"]                                            # it has no back
    assert "Philips CD-i" not in idx


def test_back_urls_prefer_the_items_region(tmp_path):
    lb = launchbox.LaunchBox(tmp_path)
    item = {"system": "psx", "title": "Jet Moto 2", "rel": "Jet Moto 2 (USA).chd", "region": "USA"}
    with pytest.raises(NotReady):                       # before "Download back covers": nothing recorded
        lb.back_urls(item, "back")
    lb.index = launchbox.build_index(io.BytesIO(XML), set(launchbox.PLATFORMS.values()))
    assert lb.back_urls(item, "back")[0] == "https://images.launchbox-app.com/jm2-back-us.jpg"
    assert lb.back_urls({**item, "region": "Europe"}, "back")[0].endswith("jm2-back-eu.jpg")
    assert lb.back_urls(item, "box") is None                  # never asked for fronts
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
