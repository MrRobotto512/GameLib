"""Game facts (year, publisher, developer, genre, players) from libretro-database's .rdb files,
downloaded once per system by this PC and matched by the backup's No-Intro / Redump name."""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .art import candidates
from .db import Database
from .rdb import load_games
from .systems import SystemsTable

RDB_URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/rdb/{name}.rdb"
RETRY_AFTER = 24 * 3600
_NORM = re.compile(r"[^a-z0-9]+")


def norm(name: str) -> str:
    return _NORM.sub(" ", name.lower()).strip()


def rdb_name(system) -> str:
    return getattr(system, "rdb", "") or (system.thumbs.replace("_", " ") if system.thumbs else "")


class MetadataStore:
    """Per-system in-memory indexes built from cached .rdb files, plus a per-item result cache in SQLite."""

    def __init__(self, db: Database, systems: SystemsTable, cache_dir: Path, enabled=lambda: True):
        self.db = db
        self.systems = systems
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self._index: dict[str, dict[str, dict[str, Any]]] = {}       # system id -> norm name -> record
        self._failed: dict[str, float] = {}
        self._lock = threading.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (item_id TEXT PRIMARY KEY, status TEXT, data TEXT, checked REAL)")

    # ---- per item --------------------------------------------------------------
    def get(self, item: dict[str, Any], fetch: bool = True) -> dict[str, Any] | None:
        import json
        row = self.db.one("SELECT * FROM metadata WHERE item_id=?", (item["id"],))
        if row:
            if row["status"] == "ok":
                return json.loads(row["data"])
            if time.time() - row["checked"] < RETRY_AFTER:
                return None
        if not fetch or not self.enabled():
            return None
        index = self._system_index(item["system"])
        if index is None:
            return None
        rec = self._match(index, item)
        if rec:
            facts = self._facts(rec)
            self.db.execute("INSERT OR REPLACE INTO metadata(item_id,status,data,checked) VALUES(?,?,?,?)",
                            (item["id"], "ok", json.dumps(facts), time.time()))
            return facts
        self.db.execute("INSERT OR REPLACE INTO metadata(item_id,status,data,checked) VALUES(?,?,?,?)",
                        (item["id"], "missing", "{}", time.time()))
        return None

    def prefetch(self, items: list[dict[str, Any]]) -> None:
        def run():
            for it in items:
                try:
                    self.get(it)
                except Exception:       # noqa: BLE001 — best effort, in the background
                    pass
        threading.Thread(target=run, name="gameshop-metadata", daemon=True).start()

    # ---- matching ------------------------------------------------------------------
    @staticmethod
    def _facts(rec: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, out_k in (("releaseyear", "year"), ("releasemonth", "month"), ("developer", "developer"), ("publisher", "publisher"),
                         ("genre", "genre"), ("franchise", "franchise"), ("users", "players"), ("serial", "serial"),
                         ("region", "region"), ("esrb_rating", "rating"), ("description", "description")):
            v = rec.get(k)
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace").strip("\x00")
            if v not in (None, "", 0) and isinstance(v, (str, int, float)):
                out[out_k] = v
        if out.get("description") == rec.get("name"):
            out.pop("description", None)
        return out

    def _match(self, index: dict[str, dict[str, Any]], item: dict[str, Any]) -> dict[str, Any] | None:
        fallback = None
        for name in candidates(item):
            rec = index.get(norm(name))
            if rec:
                if rec.get("releaseyear") or rec.get("publisher") or rec.get("developer"):
                    return rec                       # a record with facts, not just a bare name
                fallback = fallback or rec
        if fallback:
            return fallback
        # last resort: the title with a region tag, never a hack or a homebrew variant
        t = norm(item.get("title", ""))
        if len(t) >= 4:
            for region in ("usa", "europe", "japan", "world", "usa europe", "japan usa", "europe usa"):
                rec = index.get(f"{t} {region}")
                if rec and (rec.get("releaseyear") or rec.get("publisher")):
                    return rec
        return None

    # ---- per system ------------------------------------------------------------------
    def _system_index(self, system_id: str) -> dict[str, dict[str, Any]] | None:
        with self._lock:
            if system_id in self._index:
                return self._index[system_id]
            if time.time() - self._failed.get(system_id, 0) < RETRY_AFTER:
                return None
        system = self.systems.get(system_id)
        name = rdb_name(system) if system else ""
        if not name:
            return None
        path = self.dir / f"{name}.rdb"
        if not path.exists():
            url = RDB_URL.format(name=urllib.parse.quote(name))
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "GameShop/0.1"})
                with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(r.read())
                    tmp.replace(path)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                with self._lock:
                    self._failed[system_id] = time.time()
                return None
        try:
            games = load_games(path.read_bytes())
        except (ValueError, OSError):
            with self._lock:
                self._failed[system_id] = time.time()
            return None
        index: dict[str, dict[str, Any]] = {}

        def richer(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
            score = lambda r: sum(1 for k in ("releaseyear", "publisher", "developer", "genre") if r.get(k))   # noqa: E731
            return b if score(b) > score(a) else a

        for field_name in ("rom_name", "name"):                   # file names first, then display names
            for g in games:
                key = g.get(field_name, "")
                if not key:
                    continue
                stem = key.rsplit(".", 1)[0] if "." in key and len(key.rsplit(".", 1)[1]) <= 4 else key
                k = norm(stem)
                index[k] = richer(index[k], g) if k in index else g
        with self._lock:
            self._index[system_id] = index
        return index
