import json
import time
from pathlib import Path

from gameshop.db import Database
from gameshop.ps5 import PS5Client
from gameshop.transfer import TransferWorker
from tests.fakeps5 import FakeFTP


def _wait(db, jid, states=("done", "failed", "cancelled"), timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        j = db.job(jid)
        if j["state"] in states:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job stuck: {db.job(jid)}")


def test_ftp_upload_resume_and_verify(tmp_path: Path):
    remote_root = tmp_path / "ps5"
    local = tmp_path / "Game (USA).chd"
    local.write_bytes(bytes(range(256)) * 4000)       # ~1 MB
    with FakeFTP(remote_root) as ftp:
        client = PS5Client("127.0.0.1", ftp_port=ftp.port)
        # a half-finished partial from "last time"
        dest = remote_root / "data/homebrew/PPSA99169/content/psx"
        dest.mkdir(parents=True)
        (dest / "Game (USA).chd.gameshop-partial").write_bytes(local.read_bytes()[:300000])
        seen = []
        res = client.upload(str(local), "/data/homebrew/PPSA99169/content/psx/Game (USA).chd", progress=seen.append)
        assert res == "copied"
        assert seen[0] >= 300000, "resume should start where the partial ended"
        assert (dest / "Game (USA).chd").read_bytes() == local.read_bytes()
        assert not (dest / "Game (USA).chd.gameshop-partial").exists()
        # second time: skipped
        assert client.upload(str(local), "/data/homebrew/PPSA99169/content/psx/Game (USA).chd") == "skipped"
        names = [e.name for e in client.listdir("/data/homebrew/PPSA99169/content/psx")]
        assert names == ["Game (USA).chd"]


def test_worker_runs_ftp_job(tmp_path: Path):
    remote_root = tmp_path / "ps5"
    cue = tmp_path / "Game (USA).cue"
    binf = tmp_path / "Game (USA).bin"
    cue.write_text('FILE "Game (USA).bin" BINARY\n')
    binf.write_bytes(b"\x01" * 500000)
    db = Database(tmp_path / "t.db")
    with FakeFTP(remote_root) as ftp:
        worker = TransferWorker(db, lambda: PS5Client("127.0.0.1", ftp_port=ftp.port), lambda i, n: "")
        worker.start()
        jid = db.add_job(item_id="abc", title="Game", system="psx", route="ftp",
                         src=[[str(cue), "Game (USA).cue"], [str(binf), "Game (USA).bin"]], dest="/data/homebrew/PPSA99169/content/psx")
        worker.wake()
        j = _wait(db, jid)
        worker.stop()
    assert j["state"] == "done", j
    assert j["done"] == j["total"] == cue.stat().st_size + binf.stat().st_size
    assert (remote_root / "data/homebrew/PPSA99169/content/psx/Game (USA).bin").stat().st_size == 500000


def test_client_finds_the_ftp_port(tmp_path, monkeypatch):
    """ps5_ftp_port 0: the first of FTP_PORTS that answers is used (etaHEN 1337, or ftpsrv.elf 2121)."""
    from gameshop import ps5 as ps5mod
    from tests.fakeps5 import free_port
    (tmp_path / "ps5").mkdir()
    with FakeFTP(tmp_path / "ps5") as ftp:
        monkeypatch.setattr(ps5mod, "FTP_PORTS", (free_port(), ftp.port))
        client = PS5Client("127.0.0.1")
        assert client.find_ftp_port(timeout=0.5) == ftp.port
        h = client.health()
        assert h.ftp and h.ftp_port == ftp.port
        assert client.listdir("/") is not None
        assert not PS5Client("127.0.0.1", ftp_port=free_port()).health().ftp


def test_tile_and_open_payloads(tmp_path):
    """install_tile puts the web-shortcut files on the PS5 and sends the tile payload; open_browser
    sends the open payload with the url written into its buffer."""
    import socket, threading
    from gameshop import ps5 as ps5mod
    from tests.fakeps5 import free_port
    got: list[bytes] = []
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(2)
    elf_port = srv.getsockname()[1]

    def accept():
        for _ in range(2):
            c, _ = srv.accept()
            buf = b""
            while True:
                d = c.recv(65536)
                if not d:
                    break
                buf += d
            got.append(buf); c.close()
    threading.Thread(target=accept, daemon=True).start()
    (tmp_path / "ps5").mkdir()
    icon = tmp_path / "icon0.png"; icon.write_bytes(b"\x89PNG fake")
    with FakeFTP(tmp_path / "ps5") as ftp:
        client = PS5Client("127.0.0.1", ftp_port=ftp.port)
        client.send_bytes = lambda data, port=elf_port: PS5Client.send_bytes(client, data, elf_port)
        client.install_tile("http://10.0.0.2:8750/", icon)
        client.open_browser("http://10.0.0.2:8750/")
    import time
    for _ in range(50):
        if len(got) == 2:
            break
        time.sleep(0.1)
    base = tmp_path / "ps5" / "user" / "app" / ps5mod.TILE_ID / "sce_sys"
    param = json.loads((base / "param.json").read_text())
    assert param["deeplinkUri"] == "http://10.0.0.2:8750/" and param["titleId"] == ps5mod.TILE_ID
    assert (base / "icon0.png").read_bytes() == b"\x89PNG fake"
    assert got[0] == (ps5mod.PAYLOAD_DIR / "gameshop-tile.elf").read_bytes()
    assert ps5mod.URL_MARK not in got[1] and b"http://10.0.0.2:8750/\0" in got[1]
    assert len(got[1]) == (ps5mod.PAYLOAD_DIR / "gameshop-open.elf").stat().st_size
