"""The Game Shop agent: HTTP API, the storefront's static files, Range-capable file serving,
and the gateway that streams other agents' files to the PS5."""
from __future__ import annotations

import ftplib
import json
import mimetypes
import os
import posixpath
import socket
import string
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .art import ArtCache, ArtSource
from .config import ConfigStore, Source
from .db import Database
from .grouping import fold, group_key, part_of, strip_game
from .indexer import Indexer, scan_all
from .metadata import MetadataStore
from .switchdb import SwitchTitleDB
from .gametdb import GameTDB, SYSTEMS as GAMETDB_SYSTEMS, accept as gametdb_accept
from .covers import patch_site_icon_urls, serial_cover_urls
from .photos import ConsolePhotos
from .paths import resource_dir
from .peers import PeerDirectory
from .ps5 import PS5Client, Health, RA_PORT, scan_subnet
from .systems import load_systems
from .transfer import TransferWorker

WEB = resource_dir() / "web"
CHUNK = 1 << 20
REMOTE_SEP = "~"          # remote item ids look like "<peer key>~<item id>"


class Agent:
    """Everything the API needs, wired together once."""

    def __init__(self, data_dir: Path | None = None):
        self.store = ConfigStore(data_dir)
        self.store.ensure_identity()
        self.data_dir = self.store.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.systems = load_systems()
        self.db = Database(self.data_dir / "gameshop.db")
        self.indexer = Indexer(self.systems)
        self.quit_hooks: list = []                 # called (in order) when another copy asks us to quit
        self.art = ArtCache(self.db, self.systems, self.data_dir / "art", enabled=lambda: self.store.config.art_enabled)
        self.metadata = MetadataStore(self.db, self.systems, self.data_dir / "rdb", enabled=lambda: self.store.config.art_enabled)
        self.switchdb = SwitchTitleDB(self.data_dir / "titledb", enabled=lambda: self.store.config.art_enabled,
                                      on_ready=self._switch_ready)
        self.photos = ConsolePhotos(self.data_dir / "art", enabled=lambda: self.store.config.art_enabled)
        self.gametdb = GameTDB(self.data_dir / "gametdb", enabled=lambda: self.store.config.art_enabled,
                               hint=self._gametdb_names)
        # Switch: the retail front from GameTDB first, the eShop icon only when it has none
        self.art.providers.append(ArtSource(self.gametdb.urls, ["switch"], accept=gametdb_accept))
        self.art.providers.append(lambda it, kind: self.switchdb.art_url(it, kind) if it["system"] == "switch" else None)
        # after libretro misses: GameTDB for its other consoles, serial-keyed PS1 / PS2 covers, PS4 / PS5 icons
        self.art.fallbacks.append(ArtSource(self.gametdb.urls, GAMETDB_SYSTEMS - {"switch"}, accept=gametdb_accept))
        self.art.fallbacks.append(serial_cover_urls)
        self.art.fallbacks.append(patch_site_icon_urls)
        self.metadata.providers.append(lambda it: self.switchdb.facts(it) if it["system"] == "switch" else None)
        self.art.name_hint = self.metadata.thumb_names
        self._forget_misses_once()                     # matching improved: retry everything once per version
        self._refetch_once("switch", "gametdb-1")      # Switch boxes used to be eShop icons
        self.worker = TransferWorker(self.db, lambda: self.client(), self.file_url,
                                     skip_existing=lambda: self.store.config.skip_existing, after_ftp=self._after_ftp)
        self.peers = PeerDirectory(self.config.agent_id, self.config.name, lambda: self.config.port,
                                   static=lambda: self.store.config.peers, discovery=lambda: self.store.config.discovery,
                                   my_ip=lambda: self.local_ip_for(self.config.ps5_host))
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
        port = self.config.ps5_ftp_port or (self._health.ftp_port if self._health else 0)   # the port health found, so copies skip the probe
        return PS5Client(self.config.ps5_host, port)

    def local_ip_for(self, remote: str) -> str:
        """The address of this PC as seen from the PS5 (the interface that routes to it)."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((remote or "8.8.8.8", 9))
                return s.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())

    def file_url(self, item_id: str, index: int) -> str:
        """A URL the PS5 can fetch. Remote items are proxied through this agent (the gateway)."""
        return f"http://{self.local_ip_for(self.config.ps5_host)}:{self.config.port}/files/{item_id}/{index}"

    def health(self, refresh: bool = False) -> Health:
        if refresh or self._health is None or time.time() - self._health_at > 20:
            self._health = PS5Client(self.config.ps5_host, self.config.ps5_ftp_port).health()
            self._health_at = time.time()
        return self._health

    def store_url(self) -> str:
        """The store's address as the PS5 reaches it."""
        return f"http://{self.local_ip_for(self.config.ps5_host)}:{self.config.port}/"

    def dest_for(self, system_id: str) -> str:
        s = self.systems.get(system_id)
        return s.resolve_dest(self.config.ra_root, self.config.destinations) if s else ""

    # ---- library: local + peers ----------------------------------------------------
    def _forget_misses_once(self) -> None:
        from . import __version__
        marker = self.data_dir / f".misses-cleared-{__version__}"
        if marker.exists():
            return
        self.db.execute("DELETE FROM art WHERE status='missing'")
        self.db.execute("DELETE FROM metadata WHERE status='missing'")
        marker.write_text("")

    def _gametdb_names(self, item: dict[str, Any]) -> list[str]:
        """The eShop name of a Switch game (from titledb) is closer to GameTDB's than the file's."""
        if item.get("system") != "switch":
            return []
        try:
            found = self.switchdb.record(item)
        except Exception:   # noqa: BLE001
            return []
        return [found[0]["name"]] if found and found[0].get("name") else []

    def _refetch_once(self, system_id: str, tag: str) -> None:
        """Drop one system's cached box art once, so a better source gets its turn."""
        marker = self.data_dir / f".art-refetched-{system_id}-{tag}"
        if marker.exists():
            return
        for r in self.db.items(system=system_id):
            self.art.path_for(r["id"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM art WHERE item_id=?", (r["id"],))
        marker.write_text("")

    def _forget_misses(self, system_id: str) -> None:
        ids = [r["id"] for r in self.db.items(system=system_id)]
        for i in ids:
            self.db.execute("DELETE FROM art WHERE status='missing' AND (item_id=? OR item_id LIKE ?)", (i, i + ":%"))
            self.db.execute("DELETE FROM metadata WHERE status='missing' AND item_id=?", (i,))

    def _switch_ready(self) -> None:
        """The title database finished downloading: fetch art and facts for the Switch items now."""
        self._forget_misses("switch")
        items = self.db.items(system="switch")
        self.art.prefetch(items)
        self.metadata.prefetch(items)

    def rescan(self) -> dict[str, int]:
        with self._scan_lock:
            self.scanning = True
            try:
                counts = scan_all(self.indexer, self.db, self.config.sources, [h["id"] for h in self.config.hidden])
            finally:
                self.scanning = False
        if self.config.art_enabled:
            self.art.prefetch(self.db.items())
            self.metadata.prefetch(self.db.items())
        return counts

    @staticmethod
    def split_id(item_id: str) -> tuple[str, str]:
        if REMOTE_SEP in item_id:
            peer, _, local = item_id.partition(REMOTE_SEP)
            return peer, local
        return "", item_id

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        """A local or remote item by its (possibly peer-prefixed) id."""
        peer_key, local = self.split_id(item_id)
        if not peer_key:
            return self.db.item(local)
        peer = self.peers.get(peer_key)
        if not peer or not peer.online:
            return None
        for it in peer.items:
            if it["id"] == local:
                return self._remote(peer, it)
        return None

    def _remote(self, peer, it: dict[str, Any]) -> dict[str, Any]:
        r = dict(it)
        r["id"] = f"{peer.key}{REMOTE_SEP}{it['id']}"
        r["peer"] = peer.key
        r["peer_name"] = peer.name or peer.key
        r["peer_url"] = peer.url
        r["remote_id"] = it["id"]
        return r

    def merged_items(self, system: str | None = None, search: str = "", limit: int = 0) -> list[dict[str, Any]]:
        """Local items plus every online peer's, de-duplicated (same system, title and size), local first."""
        items = self.db.items(system, search, 0)
        seen = {(i["system"], i["sort_title"], i["size"]) for i in items}
        q = search.lower()
        for peer, it in self.peers.remote_items():
            if system and it["system"] != system:
                continue
            if q and q not in it["title"].lower():
                continue
            key = (it["system"], it["sort_title"], it["size"])
            if key in seen:
                for local in items:
                    if (local["system"], local["sort_title"], local["size"]) == key:
                        local.setdefault("also_on", []).append(peer.name or peer.key)
                        break
                continue
            seen.add(key)
            items.append(self._remote(peer, it))
        items.sort(key=lambda i: i["sort_title"])
        items = fold(items)                              # updates / DLC ride on their game
        return items[:limit] if limit else items

    def related(self, it: dict[str, Any]) -> dict[str, Any]:
        """For a product page: the game's add-ons with their status, or the game an add-on belongs to."""
        out: dict[str, Any] = {"part": part_of(it)}
        key = group_key(it)
        grouped = [g for g in self.merged_items(it["system"]) if group_key(g) == key]
        mine = next((g for g in grouped if g["id"] == it["id"]), None)
        if out["part"] == "base" or (mine and mine.get("extras")):     # a game, or an update standing in for one
            extras = list((mine or {}).get("extras") or [])
            for x in extras:
                full = self.get_item(x["id"])
                if full:
                    x["status"] = self.status_for(full, self.route_for(it["system"]), self.dest_for(it["system"]))
                    if not x["title"] and x["part"] == "dlc" and it["system"] == "switch" and (full.get("meta") or {}).get("title_id"):
                        name = self.switchdb.name_for(full["meta"]["title_id"]) or ""
                        x["title"] = strip_game(name, str(it.get("title", ""))) or name
            out["extras"] = extras
        else:
            base = next((g for g in grouped if g.get("part") == "base"), None)
            if base:
                out["base"] = {"id": base["id"], "title": base["title"]}
        return out

    def route_for(self, system_id: str) -> str:
        s = self.systems.get(system_id)
        return s.route if s else "none"

    def merged_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in self.merged_items():
            counts[it["system"]] = counts.get(it["system"], 0) + 1
        return counts

    def item_view(self, it: dict[str, Any], with_status: bool = True) -> dict[str, Any]:
        s = self.systems.get(it["system"])
        dest = self.dest_for(it["system"])
        route = s.route if s else "none"
        remote = bool(it.get("peer"))
        view = {
            **it,
            "system_name": s.name if s else it["system"],
            "case": s.case if s else "generic",
            "color": s.color if s else "#888",
            "route": route,
            "dest": dest,
            "art": bool(it.get("art")) if remote else self.art.path_for(it["id"]).exists(),
            "primary": posixpath.basename(it["rel"]),
            "core": s.core if s else "",
            "facts": (it.get("facts") or {}) if remote else (self.metadata.get(it, fetch=with_status) or {}),
        }
        if with_status:
            view["status"] = self.status_for(it, route, dest)
        return view

    def status_for(self, it: dict[str, Any], route: str, dest: str) -> dict[str, Any]:
        job = self.db.active_job_for(it["id"])
        if job:
            return {"state": job["state"], "job": job["id"], "step": job["step"], "done": job["done"], "total": job["total"]}
        if route == "none":
            s = self.systems.get(it["system"])
            return {"state": "unsupported", "reason": s.note if s else ""}
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
        if not dest:
            return None
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

    # ---- jobs -----------------------------------------------------------------------
    def enqueue_item(self, item_id: str, dest: str | None = None) -> dict[str, Any]:
        it = self.get_item(item_id)
        if not it:
            raise HTTPException(404, "No such game (or its PC is offline)")
        s = self.systems.get(it["system"])
        route = s.route if s else "none"
        if route == "none":
            raise HTTPException(400, s.note or "No PS5 target for this system yet")
        if self.db.active_job_for(item_id):
            raise HTTPException(409, "Already in the download queue")
        if it.get("peer"):
            base = f"{it['peer_url']}/files/{it['remote_id']}"
            files = [[f"{base}/{i}", rel if it["kind"] == "folder" else posixpath.basename(rel)] for i, (rel, _) in enumerate(it["files"])]
        else:
            src_root = Path(it["source"])
            files = [[str(src_root / rel), rel if it["kind"] == "folder" else posixpath.basename(rel)] for rel, _ in it["files"]]
        target = dest or self.dest_for(it["system"])
        if route == "ftp" and not target:
            raise HTTPException(400, "Choose this console's PS5 folder in Settings → Destinations first")
        jid = self.db.add_job(item_id=item_id, title=it["title"], system=it["system"], route=route, src=files,
                              dest=target if route == "ftp" else "", total=it["size"],
                              opts={"peer": it.get("peer_name", "")} if it.get("peer") else {})
        self.worker.wake()
        return self.db.job(jid)

    def enqueue_chain(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        jobs = []
        for i, p in enumerate(payloads):
            path = Path(p.get("path", ""))
            if not path.is_file():
                raise HTTPException(400, f"{p.get('name') or path.name}: file not found")
            delay = float(p.get("delay") or 0) if i else 0.0
            jid = self.db.add_job(item_id=None, title=p.get("name") or path.name, system="payload", route="elf",
                                  src=[[str(path), path.name]], dest="", total=path.stat().st_size, opts={"delay": delay})
            jobs.append(self.db.job(jid))
        self.worker.wake()
        return jobs

    # ---- RetroArch WebUI ---------------------------------------------------------------
    def retroarch_forwarder(self, it: dict[str, Any]) -> dict[str, Any]:
        s = self.systems.get(it["system"])
        dest = self.dest_for(it["system"])
        ra = self.config.ra_root.rstrip("/")
        if not s or not s.core or not dest.startswith(ra + "/content/"):
            raise HTTPException(400, "Home-screen tiles only work for games inside RetroArch's content folder")
        rom = dest[len(ra) + len("/content/"):].strip("/") + "/" + posixpath.basename(it["rel"])
        body = json.dumps({"name": it["title"][:60], "rom": rom, "core": s.core, "exit_after_game": True}).encode()
        req = urllib.request.Request(f"http://{self.config.ps5_host}:{RA_PORT}/api/forwarders", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:     # noqa: S310
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raise HTTPException(502, f"RetroArch said: {e.read().decode('utf-8', 'replace')[:300]}")
        except (urllib.error.URLError, OSError) as e:
            raise HTTPException(502, f"RetroArch's WebUI did not answer: {e}")


# Art is fetched from the internet on demand, one request per cover. Those requests get a small
# pool of their own, so a shelf full of first-time covers never queues the API behind them.
ART_LIMITER = anyio.CapacityLimiter(8)


def health_dict(h: Health) -> dict[str, Any]:
    return {**h.__dict__, "online": h.online}


def _proxy_stream(url: str, request_range: str | None, method: str = "GET") -> Response:
    """Pass a Range-capable GET/HEAD through to a peer, streaming the body."""
    headers = {"Range": request_range} if request_range else {}
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)                 # noqa: S310
    except urllib.error.HTTPError as e:
        return Response(status_code=e.code, content=e.read()[:1000])
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(502, f"peer did not answer: {e}")
    passthrough = {k: v for k, v in r.headers.items() if k.lower() in ("content-length", "content-range", "accept-ranges", "content-type", "content-disposition")}
    if method == "HEAD":
        r.close()
        return Response(status_code=r.status, headers=passthrough)

    def body() -> Iterator[bytes]:
        try:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            r.close()
    return StreamingResponse(body(), status_code=r.status, headers=passthrough,
                             media_type=passthrough.get("content-type", "application/octet-stream"))


def create_app(agent: Agent | None = None) -> FastAPI:
    agent = agent or Agent()
    app = FastAPI(title="Game Shop", version=__version__)
    app.state.agent = agent

    @app.on_event("startup")
    def _start() -> None:
        agent.worker.start()
        agent.peers.start()
        if agent.config.sources:
            threading.Thread(target=agent.rescan, daemon=True).start()

    @app.on_event("shutdown")
    def _stop() -> None:
        agent.worker.stop()
        agent.peers.stop()

    # ---- state ---------------------------------------------------------------------
    @app.get("/api/state")
    def state(refresh: int = 0):
        counts = agent.merged_counts()
        systems = []
        for s in agent.systems.all():
            systems.append({
                "id": s.id, "name": s.name, "maker": s.maker, "route": s.route, "case": s.case, "color": s.color,
                "note": s.note, "dest": agent.dest_for(s.id), "default_dest": s.resolve_dest(agent.config.ra_root),
                "count": counts.get(s.id, 0), "exts": list(s.exts), "core": s.core,
                "photo": bool(s.photo), "photo_by": s.photo_by,
            })
        h = agent.health(refresh=bool(refresh))
        return {
            "name": agent.config.name, "version": __version__, "scanning": agent.scanning, "agent_id": agent.config.agent_id,
            "ps5": health_dict(h), "systems": systems, "config": agent.config.to_dict(),
            "total": sum(counts.values()), "local_total": sum(agent.db.counts().values()),
            "peers": agent.peers.all(), "lan_ip": agent.local_ip_for(agent.config.ps5_host),
        }

    # ---- what other agents ask us ------------------------------------------------------
    @app.get("/api/peer/info")
    def peer_info():
        return {"agent_id": agent.config.agent_id, "name": agent.config.name, "version": __version__,
                "port": agent.config.port, "count": sum(agent.db.counts().values())}

    @app.get("/api/peer/items")
    def peer_items():
        return [{**it, "art": agent.art.path_for(it["id"]).exists(), "facts": agent.metadata.get(it, fetch=False) or {}} for it in agent.db.items()]

    @app.post("/api/peers/refresh")
    def peers_refresh():
        agent.peers.refresh()
        return {"ok": True}

    # ---- library -------------------------------------------------------------------
    @app.get("/api/discover")
    def discover():
        allitems = agent.merged_items()
        counts: dict[str, int] = {}
        for it in allitems:
            counts[it["system"]] = counts.get(it["system"], 0) + 1
        shelves = []
        for s in agent.systems.all():
            if counts.get(s.id):
                shelves.append({"system": s.id, "name": s.name, "count": counts[s.id],
                                "items": [agent.item_view(i, with_status=False) for i in allitems if i["system"] == s.id][:12]})
        recent = sorted(allitems, key=lambda i: i.get("added", 0), reverse=True)[:10]
        return {"recent": [agent.item_view(i, with_status=False) for i in recent], "shelves": shelves}

    @app.get("/api/items")
    def items(system: str = "", q: str = "", limit: int = 0, status: int = 0):
        if status and system:
            agent.remote_listing(agent.dest_for(system))
        return [agent.item_view(i, with_status=bool(status)) for i in agent.merged_items(system or None, q, limit)]

    @app.get("/api/items/{item_id}")
    def item(item_id: str):
        it = agent.get_item(item_id)
        if not it:
            raise HTTPException(404)
        agent.remote_listing(agent.dest_for(it["system"]))
        v = agent.item_view(it)
        s = agent.systems.get(it["system"])
        v["system_note"] = s.note if s else ""
        v["destinations"] = [{"id": x.id, "name": x.name, "dest": agent.dest_for(x.id)} for x in agent.systems.all() if x.route == "ftp" and agent.dest_for(x.id)]
        v["tile_possible"] = bool(s and s.core and agent.dest_for(it["system"]).startswith(agent.config.ra_root.rstrip("/") + "/content/"))
        v.update(agent.related(it))
        return v

    @app.post("/api/items/{item_id}/download")
    def download(item_id: str, body: dict[str, Any] | None = None):
        return agent.enqueue_item(item_id, (body or {}).get("dest") or None)

    @app.post("/api/items/{item_id}/hide")
    def hide(item_id: str):
        """Take a wrongly indexed file out of the store; it stays out across rescans until unhidden."""
        it = agent.db.item(item_id)
        if not it:
            raise HTTPException(404)
        entry = {"id": item_id, "source": it["source"], "rel": it["rel"], "title": it["title"]}
        agent.store.config.hidden = [h for h in agent.config.hidden if h["id"] != item_id] + [entry]
        agent.store.save()
        agent.db.delete_item(item_id)
        return {"ok": True, "hidden": agent.config.hidden}

    @app.post("/api/hidden/unhide")
    def unhide(body: dict[str, Any]):
        ids = set(body.get("ids") or [])
        agent.store.config.hidden = [h for h in agent.config.hidden if h["id"] not in ids]
        agent.store.save()
        threading.Thread(target=agent.rescan, daemon=True).start()
        return {"ok": True, "hidden": agent.config.hidden}

    @app.post("/api/items/{item_id}/tile")
    def tile(item_id: str):
        it = agent.get_item(item_id)
        if not it:
            raise HTTPException(404)
        return agent.retroarch_forwarder(it)

    @app.get("/console/{system_id}")
    async def console_photo(system_id: str):
        sys_ = agent.systems.get(system_id)
        if not sys_ or not sys_.photo:
            raise HTTPException(404)
        p = await anyio.to_thread.run_sync(agent.photos.get, sys_, limiter=ART_LIMITER)
        if not p:
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/art/{item_id}")
    async def art(item_id: str, kind: str = "box"):
        peer_key, local = agent.split_id(item_id)
        if peer_key:
            peer = agent.peers.get(peer_key)
            if not peer:
                raise HTTPException(404)
            return await anyio.to_thread.run_sync(_proxy_stream, f"{peer.url}/art/{local}?kind={kind}", None, limiter=ART_LIMITER)
        it = agent.db.item(local)
        if not it:
            raise HTTPException(404)
        kind = kind if kind in ArtCache.KINDS else "box"
        p = agent.art.get(it, fetch=False, kind=kind)                      # cached: answer at once
        if not p:
            p = await anyio.to_thread.run_sync(lambda: agent.art.get(it, kind=kind), limiter=ART_LIMITER)
        if not p:
            raise HTTPException(404)
        with open(p, "rb") as f:
            magic = f.read(3)
        media = "image/jpeg" if magic[:2] == b"\xff\xd8" else "image/webp" if magic[:3] == b"RIF" else "image/png"
        return FileResponse(p, media_type=media, headers={"Cache-Control": "public, max-age=86400"})

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

    @app.post("/api/ps5/tile")
    def ps5_tile():
        """A Game Shop tile on the PS5 home screen (needs FTP; Shadow Mount+ adds the tile)."""
        h = agent.health(refresh=True)
        if not h.ftp:
            raise HTTPException(400, "The PS5's FTP is not answering. Check its health above.")
        url = agent.store_url()
        try:
            agent.client().install_tile(url)
        except (OSError, ftplib.Error) as e:
            raise HTTPException(502, f"Could not add the tile: {e}") from e
        print(f"[ps5] tile installed, opens {url}", flush=True)
        return {"ok": True, "url": url}

    @app.post("/api/ps5/open")
    def ps5_open():
        """Open the store in the PS5's browser now (needs the ELF loader)."""
        h = agent.health(refresh=True)
        if not h.elfldr:
            raise HTTPException(400, "The PS5's ELF loader (9021) is not answering.")
        url = agent.store_url()
        try:
            agent.client().open_browser(url)
        except OSError as e:
            raise HTTPException(502, f"Could not reach the ELF loader: {e}") from e
        return {"ok": True, "url": url}

    @app.post("/api/ps5/scan")
    def ps5_scan(body: dict[str, Any] | None = None):
        prefix = (body or {}).get("prefix") or ".".join(agent.local_ip_for("").split(".")[:3])
        return {"prefix": prefix, "found": scan_subnet(prefix)}

    @app.post("/api/ps5/payload")
    def ps5_payload(body: dict[str, Any]):
        return agent.enqueue_chain([{"name": body.get("name", ""), "path": body.get("path", ""), "delay": 0}])[0]

    @app.post("/api/ps5/chain")
    def ps5_chain(body: dict[str, Any] | None = None):
        payloads = (body or {}).get("payloads") or agent.config.payloads
        if not payloads:
            raise HTTPException(400, "No payloads configured. Add them under Settings → Payloads.")
        return agent.enqueue_chain(payloads)

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

    @app.get("/api/smb/info")
    def smb_info(path: str):
        """What to type into RetroArch's SMB page to play this folder in place, and the share command."""
        p = Path(path)
        if not p.is_dir():
            raise HTTPException(404, "no such folder")
        name = "".join(ch for ch in p.name if ch.isalnum() or ch in "-_") or "games"
        ip = agent.local_ip_for(agent.config.ps5_host)
        return {
            "server": ip, "share": name, "folder": "", "user": "", "workgroup": "WORKGROUP",
            "command": f'New-SmbShare -Name "{name}" -Path "{p}" -ReadAccess Everyone',
            "remove": f'Remove-SmbShare -Name "{name}" -Force',
            "note": "Run the command in an administrator PowerShell once. Guest access must be allowed on this PC "
                    "(Windows Home allows it by default; Pro may need 'AllowInsecureGuestAuth'). Then in RetroArch's WebUI → SMB, "
                    f"enter server {ip}, share {name}, no user.",
        }

    # ---- settings ------------------------------------------------------------------------
    @app.get("/api/config")
    def get_config():
        return agent.config.to_dict()

    @app.put("/api/config")
    def put_config(body: dict[str, Any]):
        body.pop("sources", None)          # sources have their own endpoints
        body.pop("agent_id", None)
        cfg = agent.store.update(body)
        agent._health = None
        agent._remote_cache.clear()
        agent.peers.refresh()
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

    @app.post("/api/quit")
    def quit_(request: Request):
        """Exit this copy so a newer one can take the port. Loopback callers only: a copy starting
        on this PC (see single.py); never the LAN."""
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1"):
            raise HTTPException(403)
        print(f"asked to quit by another copy on this PC (v{__version__} leaving)", flush=True)

        def go():
            for hook in agent.quit_hooks:
                try:
                    hook()
                except Exception:   # noqa: BLE001
                    pass
            time.sleep(0.5)
            os._exit(0)
        threading.Timer(0.3, go).start()
        return {"ok": True, "version": __version__}

    @app.post("/api/rescan")
    def rescan():
        threading.Thread(target=agent.rescan, daemon=True).start()
        return {"ok": True}

    # ---- files the PS5 pulls (DPI stream install) and peers fetch (gateway) ----------------
    @app.get("/files/{item_id}/{index}")
    @app.head("/files/{item_id}/{index}")
    def serve_file(item_id: str, index: int, request: Request):
        peer_key, local = agent.split_id(item_id)
        if peer_key:
            peer = agent.peers.get(peer_key)
            if not peer:
                raise HTTPException(404)
            job = agent.db.active_job_for(item_id)
            resp = _proxy_stream(f"{peer.url}/files/{local}/{index}", request.headers.get("range"), request.method)
            if job and request.method == "GET" and resp.status_code in (200, 206):
                agent.db.update_job(job["id"], step="PS5 is downloading through this PC")
            return resp
        it = agent.db.item(local)
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
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(length),
                   "Content-Disposition": f'attachment; filename="{path.name}"'}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers, media_type=media)
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

        return StreamingResponse(body(), status_code=status, media_type=media, headers=headers)

    # ---- the storefront ----------------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    return app
