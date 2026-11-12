"""Release parsing and version comparison for the tray self-update (offline)."""
from gameshop import __version__, update


def test_parse_version():
    assert update.parse_version("v0.1.1") == (0, 1, 1)
    assert update.parse_version("Game Shop 1.2.10") == (1, 2, 10)
    assert update.parse_version("") == (0, 0, 0)


def test_release_from_api_picks_exe():
    data = {"tag_name": "v9.0.0", "html_url": "https://example/r", "body": "notes",
            "assets": [{"name": "other.zip", "browser_download_url": "x", "size": 1},
                       {"name": "GameShop.exe", "browser_download_url": "https://example/GameShop.exe", "size": 26000000}]}
    rel = update.release_from_api(data)
    assert rel.version == "9.0.0" and rel.asset_url == "https://example/GameShop.exe" and rel.asset_size == 26000000
    assert rel.newer
    same = update.release_from_api({"tag_name": "v" + __version__, "assets": []})
    assert not same.newer and same.asset_url == ""
    assert update.release_from_api({"tag_name": "v9.9.9", "draft": True}) is None
    assert update.release_from_api({"tag_name": "v9.9.9", "prerelease": True}) is None


def test_swap_script_waits_moves_and_restarts():
    from pathlib import Path
    s = update.swap_script(Path(r"C:\x\GameShop.exe"), Path(r"C:\x\GameShop.exe.new"), 4242, ["--port", "8750"])
    assert "process 4242" in s and "ping -n 2 127.0.0.1" in s and "goto move" in s
    assert "tasklist" not in s and "timeout" not in s          # both opened console windows / refused to run
    assert 'move /y "C:\\x\\GameShop.exe.new" "C:\\x\\GameShop.exe"' in s
    assert 'start "" /d "C:\\x" "C:\\x\\GameShop.exe" "--port" "8750"' in s
    assert "goto giveup" in s and "gameshop-update.log" in s            # a failed swap leaves a trace
    assert s.endswith('del "%~f0"\r\n')


def test_release_with_a_setup_updates_through_it():
    data = {"tag_name": "v9.0.0", "assets": [
        {"name": "GameShop.exe", "browser_download_url": "https://example/GameShop.exe", "size": 29000000},
        {"name": "GameShopSetup.exe", "browser_download_url": "https://example/GameShopSetup.exe", "size": 30000000}]}
    rel = update.release_from_api(data)
    assert rel.setup_url == "https://example/GameShopSetup.exe" and update.uses_setup(rel)
    assert update.download_size(rel) == 30000000
    old = update.release_from_api({"tag_name": "v9.0.0", "assets": data["assets"][:1]})
    assert not update.uses_setup(old) and update.download_size(old) == 29000000


def test_children_do_not_inherit_pyinstallers_environment():
    env = update.child_env({"PATH": "C:\Windows", "_PYI_APPLICATION_HOME_DIR": "C:\Temp\_MEI123",
                            "_PYI_PARENT_PROCESS_LEVEL": "1", "_MEIPASS2": "C:\Temp\_MEI123"})
    assert env == {"PATH": "C:\Windows", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
