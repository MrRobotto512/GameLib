"""Updates and DLC fold under their game instead of showing as duplicates."""
from gameshop.grouping import fold, part_of, strip_game, version_label


def sw(title, tid, content, base, size, tags=()):
    return {"id": tid, "system": "switch", "title": title, "sort_title": title.lower(), "size": size, "tags": list(tags),
            "meta": {"title_id": tid, "base_id": base, "content": content}}


def test_switch_fold():
    items = [
        sw("Mario Kart 8 Deluxe", "0100152000022000", "base", "0100152000022000", 7_000),
        sw("Mario Kart 8 Deluxe", "0100152000022800", "update", "0100152000022000", 4_800, ["v131072", "UPD"]),
        sw("Mario Kart 8 Deluxe - Booster Course Pass", "0100152000023001", "dlc", "0100152000022000", 121),
        sw("Luigis Mansion 3", "0100DCA0064A6800", "update", "0100DCA0064A6000", 1_000),   # no base in the library
        sw("Luigis Mansion 3", "0100DCA0064A7001", "dlc", "0100DCA0064A6000", 130),
        {"id": "x", "system": "switch", "title": "hbl", "sort_title": "hbl", "size": 5, "tags": [], "meta": {}},
    ]
    out = fold(items)
    assert [i["title"] for i in out] == ["Mario Kart 8 Deluxe", "Luigis Mansion 3", "hbl"]
    mk = out[0]
    assert mk["part"] == "base" and len(mk["extras"]) == 2
    upd, dlc = mk["extras"]
    assert upd["part"] == "update" and upd["version"] == "v2" and upd["title"] == ""
    assert dlc["part"] == "dlc" and dlc["title"] == "Booster Course Pass"
    assert out[1]["part"] == "update"          # orphan add-ons stay visible as one tile, the update in front
    assert [x["part"] for x in out[1]["extras"]] == ["dlc"]
    assert out[2]["part"] == "base"


def test_other_systems_by_category_or_tag():
    game = {"id": "g", "system": "ps4", "title": "Bloodborne", "sort_title": "bloodborne", "size": 30, "tags": [],
            "meta": {"content_id": "UP9000-CUSA00900_00-BLOODBORNE0000US", "title_id": "CUSA00900", "category": "game"}}
    patch = {"id": "p", "system": "ps4", "title": "Bloodborne", "sort_title": "bloodborne", "size": 3, "tags": [],
             "meta": {"content_id": "UP9000-CUSA00900_00-BLOODBORNE0000US", "title_id": "CUSA00900", "category": "update", "app_version": "01.09"}}
    dlc = {"id": "d", "system": "ps4", "title": "Bloodborne - The Old Hunters", "sort_title": "bloodborne the old hunters", "size": 4, "tags": [],
           "meta": {"content_id": "UP9000-CUSA00900_00-OLDHUNTERS000000", "title_id": "CUSA00900", "category": "dlc"}}
    # no package metadata at all: the name's tags decide, the title ties them
    rom = {"id": "r", "system": "ps3", "title": "Demon's Souls", "sort_title": "demons souls", "size": 9, "tags": ["USA"], "meta": {}}
    rom_upd = {"id": "ru", "system": "ps3", "title": "Demon’s Souls", "sort_title": "demons souls", "size": 1, "tags": ["Update", "v1.04"], "meta": {}}
    out = fold([game, patch, dlc, rom, rom_upd])
    assert [i["id"] for i in out] == ["g", "r"]
    assert [(e["part"], e["version"], e["title"]) for e in out[0]["extras"]] == [("update", "v01.09", ""), ("dlc", "", "The Old Hunters")]
    assert out[1]["extras"][0]["version"] == "v1.04" and out[1]["extras"][0]["title"] == ""   # ’ vs ' is the same name
    assert part_of({"tags": ["DLC"], "meta": {}}) == "dlc"
    assert version_label({"tags": ["v65537"], "meta": {}}) == "v65537"
    assert strip_game("Luigi’s Mansion™ 3 Multiplayer Pack 2", "Luigis Mansion 3") == "Multiplayer Pack 2"
    assert strip_game("Animal Crossing™꞉ New Horizons Nintendo Switch Online - Member-Exclusive Items", "Animal Crossing New Horizons") == "Nintendo Switch Online - Member-Exclusive Items"
    assert strip_game("LEGO® Brawls", "LEGO Brawls") == ""
    assert strip_game("Season Pass", "Other Game") == "Season Pass"
    assert strip_game("Animal Crossing™꞉ New Horizons - Animal Crossing꞉ New Horizons Special Order Ticket", "Animal Crossing New Horizons") == "Special Order Ticket"
