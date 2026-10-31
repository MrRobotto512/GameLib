"""The Game Shop agent: HTTP API, the storefront's static files, and Range-capable file serving."""
from __future__ import annotations

import ftplib
import mimetypes
import os
import posixpath
import socket
import string
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .art import ArtCache
from .config import ConfigStore, Source
from .db import Database
from .indexer import Indexer, scan_all
from .ps5 import PS5Client, Health, scan_subnet
from .systems import load_systems
from .transfer import TransferWorker

WEB = Path(__file__).parent / "web"
CHUNK = 1 << 20


class Agent:
    """Everything the API needs, wired together once."""

    def __init__(self, data_dir: Path | None = None):
        self.store = ConfigStore(data_dir)
        self.data_dir = self.store.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.systems = load_systems()
        self.db = Database(self.data_dir / "gameshop.db")
        self.indexer = Indexer(self.systems)
        self.art = ArtCache(self.db, self.systems, self.data_dir / "art", enabled=lambda: self.store.config.art_enabled)
        self.worker = TransferWorker(self.db, lambda: self.client(), self.file_url,
                                     skip_existing=lambda: self.store.config.skip_existing, after_ftp=self._after_ftp)
        self._health: Health | None = None
        self._health_at = 0.0
        self._remote_cache: dict[str, tuple[float, dict[str, int]]] = {}
        self._scan_lock = threading.Lock()
        self.scanning = False

    # ---- wiring -------------------------------------------------------------------
    @property
    def config(self):
        return self.store.config

    def client(self) -> PS5Client:
        return PS5Client(self.config.ps5_host)

    def local_ip_for(self, remote: str) -> str:
        """The address of this PC as seen from the PS5 (the interface that routes to it)."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((remote or "8.8.8.8", 9))
                return s.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())

    def file_url(self, item_id: str, index: int) -> str:
        return f"http://{self.local_ip_for(self.config.ps5_host)}:{self.config.port}/files/{item_id}/{index}"

    def health(self, refresh: bool = False) -> Health:
        if refresh or self._health is None or time.time() - self._health_at > 20:
            self._health = self.client().health()
            self._health_at = time.time()
        return self._health

    def dest_for(self, system_id: str) -> str:
        s = self.systems.get(system_id)
        return s.resolve_dest(self.config.ra_root, self.config.destinations) if s else ""

    # ---- library --------------------------------------------------------------------
    def rescan(self) -> dict[str, int]:
        with self._scan_lock:
            self.scanning = True
            try:
                counts = scan_all(self.indexer, self.db, self.config.sources)
            finally:
                self.scanning = False
        if self.config.art_enabled:
            self.art.prefetch(self.db.items())
        return counts

    def item_view(self, it: dict[str, Any], with_status: bool = True) -> dict[str, Any]:
        s = self.systems.get(it["system"])
        dest = self.dest_for(it["system"])
        route = s.route if s else "none"
        view = {
            **it,
            "system_name": s.name if s else it["system"],
            "case": s.case if s else "generic",
            "color": s.color if s else "#888",
            "route": route,
            "dest": dest,
            "art": self.art.path_for(it["id"]).exists(),
            "primary": posixpath.basename(it["rel"]),
        }
        if with_status:
            view["status"] = self.status_for(it, route, dest)
        return view

    def status_for(self, it: dict[str, Any], route: str, dest: str) -> dict[str, Any]:
        job = self.db.active_job_for(it["id"])
        if job:
            return {"state": job["state"], "job": job["id"], "step": job["step"], "done": job["done"], "total": job["total"]}
        if route == "none":
            return {"state": "unsupported", "reason": (self.systems.get(it["system"]).note if self.systems.get(it["system"]) else "")}
        if route == "ftp" and not dest:
            return {"state": "nodest", "reason": "Choose this console's PS5 folder in Settings → Destinations."}
        if route == "ftp" and self._on_ps5(it, dest):
            return {"state": "onps5"}
        last = self.db.last_job_for(it["id"])
        if last and last["state"] == "done" and route != "ftp":
            return {"state": "sent", "step": last["step"]}
        if last and last["state"] == "failed":
            return {"state": "failed", "job": last["id"], "error": last["error"]}
        return {"state": "ready"}

    def remote_listing(self, dest: str, refresh: bool = False) -> dict[str, int] | None:
        """{name: size} of a PS5 folder, cached a minute. None when the PS5 is unreachable."""
        now = time.time()
        hit = self._remote_cache.get(dest)
        if hit and not refresh and now - hit[0] < 60:
            return hit[1]
        if not self.config.ps5_host or not self.health().ftp:
            return None
        try:
            listing = {e.name: e.size for e in self.client().listdir(dest) if not e.is_dir}
        except ftplib.Error:
            listing = {}                      # the folder is not there yet: nothing is on the PS5
        except OSError:
            self._remote_cache[dest] = (now, {})
            return None
        self._remote_cache[dest] = (now, listing)
        return listing

    def _on_ps5(self, it: dict[str, Any], dest: str) -> bool:
        listing = self._remote_cache.get(dest)
        if not listing:
            return False
        names = listing[1]
        return all(names.get(posixpath.basename(rel)) == size for rel, size in it["files"]) if it["files"] else False

    def _after_ftp(self, job: dict[str, Any]) -> None:
        self._remote_cache.pop(job["dest"], None)
        self.remote_listing(job["dest"], refresh=True)
        if self.config.ra_root and job["dest"].startswith(self.config.ra_root):
            self.client().retroarch_refresh()

    # ---- jobs -----------------------------------------------------------------------
    def enqueue_item(self, item_id: str, dest: str | None = None) -> dict[str, Any]:
        it = self.db.item(item_id)
        if not it:
            raise HTTPException(404, "No such game")
        s = self.systems.get(it["system"])
        route = s.route if s else "none"
        if route == "none":
            raise HTTPException(400, s.note or "No PS5 target for this system yet")
        if self.db.active_job_for(item_id):
            raise HTTPException(409, "Already in the download queue")
        src_root = Path(it["source"])
        files = [[str(src_root / rel), rel if it["kind"] == "folder" else posixpath.basename(rel)] for rel, _ in it["files"]]
        if it["kind"] == "folder":
            # keep the folder: dest/<folder name>/<inner path>
            files = [[str(src_root / rel), rel] for rel, _ in it["files"]]
        target = dest or self.dest_for(it["system"])
        if route == "ftp" and not target:
            raise HTTPException(400, "Choose this console's PS5 folder in Settings → Destinations first")
        jid = self.db.add_job(item_id=item_id, title=it["title"], system=it["system"], route=route, src=files,
                              dest=target if route == "ftp" else "", total=it["size"])
        self.worker.wake()
        return self.db.job(jid)


def health_dict(h: Health) -> dict[str, Any]:
    return {**h.__dict__, "online": h.online}


def create_app(agent: Agent | None = None) -> FastAPI:
    agent = agent or Agent()
    app = FastAPI(title="Game Shop", version=__version__)
    app.state.agent = agent

    @app.on_event("startup")
    def _start() -> None:
        agent.worker.start()
        if agent.config.sources:
            threading.Thread(target=agent.rescan, daemon=True).start()

    @app.on_event("shutdown")
    def _stop() -> None:
        agent.worker.stop()

    # ---- state ---------------------------------------------------------------------
    @app.get("/api/state")
    def state(refresh: int = 0):
        counts = agent.db.counts()
        systems = []
        for s in agent.systems.all():
            systems.append({
                "id": s.id, "name": s.name, "maker": s.maker, "route": s.route, "case": s.case, "color": s.color,
                "note": s.note, "dest": agent.dest_for(s.id), "default_dest": s.resolve_dest(agent.config.ra_root),
                "count": counts.get(s.id, 0), "exts": list(s.exts),
            })
        h = agent.health(refresh=bool(refresh))
        return {
            "name": agent.config.name, "version": __version__, "scanning": agent.scanning,
            "ps5": health_dict(h), "systems": systems, "config": agent.config.to_dict(),
            "total": sum(counts.values()),
        }

    # ---- library -------------------------------------------------------------------
    @app.get("/api/discover")
    def discover():
        counts = agent.db.counts()
        shelves = []
        for s in agent.systems.all():
            if counts.get(s.id):
                shelves.append({"system": s.id, "name": s.name, "count": counts[s.id],
                                "items": [agent.item_view(i, with_status=False) for i in agent.db.items(s.id, limit=12)]})
        recent = [agent.item_view(i, with_status=False) for i in agent.db.recent(10)]
        return {"recent": recent, "shelves": shelves}

    @app.get("/api/items")
    def items(system: str = "", q: str = "", limit: int = 0, status: int = 0):
        if status and system:
            agent.remote_listing(agent.dest_for(system))
        return [agent.item_view(i, with_status=bool(status)) for i in agent.db.items(system or None, q, limit)]

    @app.get("/api/items/{item_id}")
    def item(item_id: str):
        it = agent.db.item(item_id)
        if not it:
            raise HTTPException(404)
        agent.remote_listing(agent.dest_for(it["system"]))
        v = agent.item_view(it)
        s = agent.systems.get(it["system"])
        v["system_note"] = s.note if s else ""
        v["destinations"] = [{"id": x.id, "name": x.name, "dest": agent.dest_for(x.id)} for x in agent.systems.all() if x.route == "ftp" and agent.dest_for(x.id)]
        return v

    @app.post("/api/items/{item_id}/download")
    def download(item_id: str, body: dict[str, Any] | None = None):
        return agent.enqueue_item(item_id, (body or {}).get("dest") or None)

    @app.get("/art/{item_id}")
    def art(item_id: str):
        it = agent.db.item(item_id)
        if not it:
            raise HTTPException(404)
        p = agent.art.get(it)
        if not p:
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    # ---- jobs ------------------------------------------------------------------------
    @app.get("/api/jobs")
    def jobs():
        return agent.db.jobs()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: int):
        agent.worker.cancel(job_id)
        return agent.db.job(job_id)

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: int):
        agent.worker.retry(job_id)
        return agent.db.job(job_id)

    @app.post("/api/jobs/clear")
    def clear_jobs():
        agent.db.execute("DELETE FROM jobs WHERE state IN ('done','failed','cancelled')")
        return {"ok": True}

    @app.post("/api/jobs/manual")
    def manual(body: dict[str, Any]):
        paths = [Path(p) for p in body.get("paths", [])]
        dest = body.get("dest", "")
        if not paths or not dest:
            raise HTTPException(400, "paths and dest are required")
        files: list[list[str]] = []
        for p in paths:
            if p.is_file():
                files.append([str(p), p.name])
            elif p.is_dir():
                for q in p.rglob("*"):
                    if q.is_file():
                        files.append([str(q), (Path(p.name) / q.relative_to(p)).as_posix()])
        if not files:
            raise HTTPException(400, "nothing to copy")
        total = sum(Path(f).stat().st_size for f, _ in files)
        title = paths[0].name + (f" (+{len(paths) - 1})" if len(paths) > 1 else "")
        jid = agent.db.add_job(item_id=None, title=title, system="", route="ftp", src=files, dest=dest, total=total)
        agent.worker.wake()
        return agent.db.job(jid)

    # ---- PS5 ---------------------------------------------------------------------------
    @app.get("/api/ps5/health")
    def ps5_health(refresh: int = 1):
        return health_dict(agent.health(refresh=bool(refresh)))

    @app.get("/api/ps5/ls")
    def ps5_ls(path: str = "/"):
        try:
            return [e.__dict__ for e in agent.client().listdir(path)]
        except (OSError, ftplib.Error) as e:
            raise HTTPException(502, f"FTP: {e}")

    @app.post("/api/ps5/delete")
    def ps5_delete(body: dict[str, Any]):
        try:
            agent.client().delete(body["path"], bool(body.get("is_dir")))
            agent._remote_cache.clear()
            return {"ok": True}
        except (OSError, ftplib.Error) as e:
            raise HTTPException(502, f"FTP: {e}")

    @app.post("/api/ps5/scan")
    def ps5_scan(body: dict[str, Any] | None = None):
        prefix = (body or {}).get("prefix") or ".".join(agent.local_ip_for("").split(".")[:3])
        return {"prefix": prefix, "found": scan_subnet(prefix)}

    @app.post("/api/ps5/payload")
    def ps5_payload(body: dict[str, Any]):
        p = Path(body.get("path", ""))
        if not p.is_file():
            raise HTTPException(400, "no such file")
        jid = agent.db.add_job(item_id=None, title=p.name, system="homebrew", route="elf", src=[[str(p), p.name]], dest="", total=p.stat().st_size)
        agent.worker.wake()
        return agent.db.job(jid)

    # ---- local files (for picking folders and manual copies) ---------------------------
    @app.get("/api/local/ls")
    def local_ls(path: str = ""):
        if not path:
            if os.name == "nt":
                drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
                return {"path": "", "entries": [{"name": d, "path": d, "is_dir": True, "size": 0} for d in drives]}
            path = "/"
        p = Path(path)
        if not p.is_dir():
            raise HTTPException(404, "no such folder")
        entries = []
        try:
            for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
                if child.name.startswith(".") or child.name.startswith("$"):
                    continue
                try:
                    entries.append({"name": child.name, "path": str(child), "is_dir": child.is_dir(),
                                    "size": 0 if child.is_dir() else child.stat().st_size})
                except OSError:
                    continue
        except PermissionError:
            raise HTTPException(403, "no access")
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "", "entries": entries}

    # ---- settings ------------------------------------------------------------------------
    @app.get("/api/config")
    def get_config():
        return agent.config.to_dict()

    @app.put("/api/config")
    def put_config(body: dict[str, Any]):
        body.pop("sources", None)          # sources have their own endpoints
        cfg = agent.store.update(body)
        agent._health = None
        agent._remote_cache.clear()
        return cfg.to_dict()

    @app.post("/api/sources")
    def add_source(body: dict[str, Any]):
        path, system = body.get("path", ""), body.get("system", "mixed")
        if not Path(path).is_dir():
            raise HTTPException(400, "That folder does not exist")
        if system != "mixed" and not agent.systems.get(system):
            raise HTTPException(400, "Unknown system")
        srcs = [s for s in agent.config.sources if s.path != path]
        srcs.append(Source(path=path, system=system, label=body.get("label", "")))
        agent.store.config.sources = srcs
        agent.store.save()
        threading.Thread(target=agent.rescan, daemon=True).start()
        return agent.config.to_dict()

    @app.delete("/api/sources")
    def del_source(path: str):
        agent.store.config.sources = [s for s in agent.config.sources if s.path != path]
        agent.store.save()
        agent.db.delete_source(path)
        return agent.config.to_dict()

    @app.post("/api/rescan")
    def rescan():
        threading.Thread(target=agent.rescan, daemon=True).start()
        return {"ok": True}

    # ---- files the PS5 pulls (DPI stream install) ------------------------------------------
    @app.get("/files/{item_id}/{index}")
    def serve_file(item_id: str, index: int, request: Request):
        it = agent.db.item(item_id)
        if not it or index >= len(it["files"]):
            raise HTTPException(404)
        path = Path(it["source"]) / it["files"][index][0]
        if not path.is_file():
            raise HTTPException(404)
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = request.headers.get("range")
        status = 200
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                if a:
                    start = int(a)
                    end = int(b) if b else size - 1
                else:
                    start = max(0, size - int(b))
            except ValueError:
                raise HTTPException(416)
            if start >= size or end < start:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            status = 206
        length = end - start + 1
        job = agent.db.active_job_for(item_id)

        def body() -> Iterator[bytes]:
            sent = 0
            last = 0.0
            with path.open("rb") as fp:
                fp.seek(start)
                left = length
                while left > 0:
                    chunk = fp.read(min(CHUNK, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                    sent += len(chunk)
                    if job and time.time() - last > 0.5:
                        last = time.time()
                        agent.db.update_job(job["id"], done=start + sent, step="PS5 is downloading")
                    yield chunk

        headers = {"Accept-Ranges": "bytes", "Content-Length": str(length),
                   "Content-Disposition": f'attachment; filename="{path.name}"'}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return StreamingResponse(body(), status_code=status, media_type=media, headers=headers)

    # ---- the storefront ----------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    return app
