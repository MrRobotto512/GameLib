"""End to end against the fake PS5: the real HTTP server, a library on disk, FTP downloads and a DPI install."""
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from gameshop.server import Agent, create_app
from tests.fakeps5 import FakeDPI, FakeFTP, free_port
from tests.test_indexer import _fake_ps4_pkg


class Server:
    def __init__(self, agent: Agent):
        self.port = free_port()
        agent.store.config.port = self.port
        config = uvicorn.Config(create_app(agent), host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _wait_job(c, jid, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/jobs").json()
        j = next(x for x in j if x["id"] == jid)
        if j["state"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.1)
    raise AssertionError("job stuck")


def test_end_to_end(tmp_path: Path, monkeypatch):
    import gameshop.ps5 as ps5mod
    lib = tmp_path / "lib"
    (lib / "psx").mkdir(parents=True)
    (lib / "psx" / "Tekken 3 (USA).chd").write_bytes(b"T" * 200000)
    (lib / "ps4").mkdir()
    _fake_ps4_pkg(lib / "ps4" / "game.pkg", "UP0001-CUSA12345_00-TESTGAME00000000", "Test Game")
    agent = Agent(tmp_path / "data")
    agent.store.config.art_enabled = False
    with FakeFTP(tmp_path / "ps5") as ftp, FakeDPI() as dpi, Server(agent) as srv:
        monkeypatch.setattr(ps5mod, "FTP_PORT", ftp.port)
        monkeypatch.setattr(ps5mod, "DPI2_PORT", dpi.port)
        agent.client = lambda: ps5mod.PS5Client("127.0.0.1", ftp_port=ftp.port)
        c = httpx.Client(base_url=f"http://127.0.0.1:{srv.port}", timeout=10)
        assert c.put("/api/config", json={"ps5_host": "127.0.0.1"}).status_code == 200
        c.post("/api/sources", json={"path": str(lib / "psx"), "system": "psx"})
        c.post("/api/sources", json={"path": str(lib / "ps4"), "system": "ps4"})
        for _ in range(50):
            if c.get("/api/state").json()["total"] == 2:
                break
            time.sleep(0.1)
        st = c.get("/api/state").json()
        assert st["total"] == 2 and st["ps5"]["ftp"] and st["ps5"]["dpi2"]
        items = c.get("/api/items?system=psx&status=1").json()
        assert items[0]["title"] == "Tekken 3" and items[0]["status"]["state"] == "ready"
        # FTP download
        job = c.post(f"/api/items/{items[0]['id']}/download", json={}).json()
        j = _wait_job(c, job["id"])
        assert j["state"] == "done", j
        assert (tmp_path / "ps5/data/homebrew/PPSA99169/content/psx/Tekken 3 (USA).chd").stat().st_size == 200000
        assert c.get(f"/api/items/{items[0]['id']}").json()["status"]["state"] == "onps5"
        # Range serving
        r = c.get(f"/files/{items[0]['id']}/0", headers={"Range": "bytes=10-19"})
        assert r.status_code == 206 and r.content == b"T" * 10 and r.headers["content-range"] == "bytes 10-19/200000"
        # DPI install: etaHEN gets a URL and pulls the file from us
        pkg = c.get("/api/items?system=ps4").json()[0]
        assert pkg["meta"]["title_id"] == "CUSA12345" and pkg["title"] == "Test Game"
        job = c.post(f"/api/items/{pkg['id']}/download", json={}).json()
        j = _wait_job(c, job["id"])
        assert j["state"] == "done" and "DPI v2" in j["step"], j
        assert dpi.urls and dpi.urls[0].endswith(f"/files/{pkg['id']}/0") and dpi.fetched == [(lib / 'ps4' / 'game.pkg').stat().st_size]
        # manual copy + PS5 browsing
        (lib / "bios.bin").write_bytes(b"B" * 1000)
        job = c.post("/api/jobs/manual", json={"paths": [str(lib / "bios.bin")], "dest": "/data/homebrew/PPSA99169/system"}).json()
        assert _wait_job(c, job["id"])["state"] == "done"
        names = [e["name"] for e in c.get("/api/ps5/ls", params={"path": "/data/homebrew/PPSA99169/system"}).json()]
        assert names == ["bios.bin"]
        assert c.get("/").status_code == 200 and c.get("/static/app.js").status_code == 200
        agent.worker.stop()
