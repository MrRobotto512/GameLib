"""Two agents: B holds the games, A is the gateway next to the (fake) PS5. A merges B's library,
proxies B's files for the PS5 and streams a remote game to the PS5 over FTP."""
import time
from pathlib import Path

import httpx

from gameshop.server import Agent
from tests.fakeps5 import FakeFTP
from tests.test_server import Server, _wait_job


def test_peer_merge_and_gateway(tmp_path: Path, monkeypatch):
    import gameshop.ps5 as ps5mod
    import gameshop.peers as peersmod
    monkeypatch.setattr(peersmod, "POLL_SECONDS", 1)
    lib_b = tmp_path / "lib_b" / "n64"
    lib_b.mkdir(parents=True)
    (lib_b / "Super Mario 64 (USA).z64").write_bytes(bytes(range(256)) * 2000)      # 512 KB
    lib_a = tmp_path / "lib_a" / "n64"
    lib_a.mkdir(parents=True)
    (lib_a / "Super Mario 64 (USA).z64").write_bytes(bytes(range(256)) * 2000)      # the same game: de-duplicated
    (lib_a / "GoldenEye 007 (USA).z64").write_bytes(b"G" * 1000)

    b = Agent(tmp_path / "data_b")
    b.store.config.art_enabled = False
    b.store.config.discovery = False
    b.store.config.name = "Basement"
    a = Agent(tmp_path / "data_a")
    a.store.config.art_enabled = False
    a.store.config.discovery = False
    a.store.config.name = "Office"

    with FakeFTP(tmp_path / "ps5") as ftp, Server(b) as sb, Server(a) as sa:
        a.store.config.peers = [f"127.0.0.1:{sb.port}"]
        a.client = lambda: ps5mod.PS5Client("127.0.0.1", ftp_port=ftp.port)
        monkeypatch.setattr(ps5mod, "FTP_PORTS", (ftp.port,))   # the health check probes these, not a.client
        cb = httpx.Client(base_url=f"http://127.0.0.1:{sb.port}", timeout=10)
        ca = httpx.Client(base_url=f"http://127.0.0.1:{sa.port}", timeout=10)
        cb.post("/api/sources", json={"path": str(lib_b), "system": "n64"})
        ca.post("/api/sources", json={"path": str(lib_a), "system": "n64"})
        ca.put("/api/config", json={"ps5_host": "127.0.0.1"})
        (lib_b / "Mario Kart 64 (USA).z64").write_bytes(b"K" * 300000)
        cb.post("/api/rescan")
        for _ in range(100):
            st = ca.get("/api/state").json()
            if st["total"] == 3 and any(p["online"] for p in st["peers"]):
                break
            time.sleep(0.1)
        st = ca.get("/api/state").json()
        assert st["total"] == 3 and st["local_total"] == 2, st["peers"]
        items = ca.get("/api/items?system=n64").json()
        by_title = {i["title"]: i for i in items}
        assert set(by_title) == {"Super Mario 64", "GoldenEye 007", "Mario Kart 64"}
        assert by_title["Super Mario 64"].get("also_on") == ["Basement"] and "peer" not in by_title["Super Mario 64"]
        kart = by_title["Mario Kart 64"]
        assert kart["peer_name"] == "Basement" and "~" in kart["id"]
        # the gateway proxies the remote file with Range support (what the PS5 and the worker use)
        r = ca.get(f"/files/{kart['id']}/0", headers={"Range": "bytes=5-9"})
        assert r.status_code == 206 and r.content == b"KKKKK"
        h = ca.head(f"/files/{kart['id']}/0")
        assert h.status_code == 200 and h.headers["content-length"] == "300000"
        # download the remote game: A streams it from B to the PS5
        job = ca.post(f"/api/items/{kart['id']}/download", json={}).json()
        j = _wait_job(ca, job["id"])
        assert j["state"] == "done", j
        assert (tmp_path / "ps5/data/homebrew/PPSA99169/content/n64/Mario Kart 64 (USA).z64").stat().st_size == 300000
        assert ca.get(f"/api/items/{kart['id']}").json()["status"]["state"] == "onps5"
        # the payload chain queues with delays
        elf = tmp_path / "kstuff.elf"
        elf.write_bytes(b"\x7fELF" + b"\0" * 100)
        jobs = ca.post("/api/ps5/chain", json={"payloads": [{"name": "kstuff", "path": str(elf), "delay": 5}, {"name": "etaHEN", "path": str(elf), "delay": 1}]}).json()
        assert [x["opts"].get("delay") for x in jobs] == [0.0, 1.0]
        a.worker.stop()
        b.worker.stop()
