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
