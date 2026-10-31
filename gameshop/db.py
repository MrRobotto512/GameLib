"""SQLite storage for the library index and the download jobs."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    system TEXT NOT NULL,
    title TEXT NOT NULL,
    sort_title TEXT NOT NULL,
    region TEXT DEFAULT '',
    tags TEXT DEFAULT '',          -- JSON list of the bracketed tags from the name
    kind TEXT DEFAULT 'rom',       -- rom | pkg | image | elf | folder
    source TEXT NOT NULL,          -- the source folder this came from
    rel TEXT NOT NULL,             -- primary file (or folder) relative to the source
    files TEXT NOT NULL,           -- JSON list of [rel, size] for every file in the game
    size INTEGER NOT NULL,
    meta TEXT DEFAULT '{}',        -- JSON: content_id, title_id, category, version, ...
    added REAL NOT NULL,
    seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS items_system ON items(system, sort_title);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT,                  -- NULL for manual copies
    title TEXT NOT NULL,
    system TEXT DEFAULT '',
    route TEXT NOT NULL,           -- ftp | dpi | elf
    src TEXT NOT NULL,             -- JSON list of [local_abs_path, remote_rel_name]
    dest TEXT NOT NULL,            -- PS5 folder (ftp) or '' (dpi/elf)
    state TEXT NOT NULL,           -- queued | running | verifying | done | failed | cancelled
    step TEXT DEFAULT '',          -- human line: "Copying track 2 of 3"
    total INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    created REAL NOT NULL,
    started REAL,
    finished REAL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, id);

CREATE TABLE IF NOT EXISTS art (
    item_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,          -- ok | missing
    path TEXT DEFAULT '',
    checked REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.query("PRAGMA table_info(jobs)")}
        if "opts" not in cols:
            self.execute("ALTER TABLE jobs ADD COLUMN opts TEXT DEFAULT '{}'")

    # ---- generic helpers -------------------------------------------------
    @staticmethod
    def _p(params: Any) -> Any:
        return params if isinstance(params, dict) else tuple(params)

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, self._p(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, self._p(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- items -------------------------------------------------------------
    def upsert_items(self, items: list[dict[str, Any]], source: str) -> None:
        now = time.time()
        with self._lock:
            for it in items:
                self._conn.execute(
                    """INSERT INTO items(id, system, title, sort_title, region, tags, kind, source, rel, files, size, meta, added, seen)
                       VALUES(:id,:system,:title,:sort_title,:region,:tags,:kind,:source,:rel,:files,:size,:meta,:now,:now)
                       ON CONFLICT(id) DO UPDATE SET system=excluded.system, title=excluded.title, sort_title=excluded.sort_title,
                         region=excluded.region, tags=excluded.tags, kind=excluded.kind, rel=excluded.rel, files=excluded.files,
                         size=excluded.size, meta=excluded.meta, seen=excluded.seen""",
                    {**it, "tags": json.dumps(it.get("tags", [])), "files": json.dumps(it["files"]),
                     "meta": json.dumps(it.get("meta", {})), "source": source, "now": now},
                )
            # anything from this source not seen in this pass is gone
            self._conn.execute("DELETE FROM items WHERE source=? AND seen<?", (source, now))
            self._conn.commit()

    def delete_source(self, source: str) -> None:
        self.execute("DELETE FROM items WHERE source=?", (source,))

    @staticmethod
    def _item(row: dict[str, Any]) -> dict[str, Any]:
        row["tags"] = json.loads(row["tags"] or "[]")
        row["files"] = json.loads(row["files"] or "[]")
        row["meta"] = json.loads(row["meta"] or "{}")
        return row

    def items(self, system: str | None = None, search: str = "", limit: int = 0) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM items", []
        where = []
        if system:
            where.append("system=?"); params.append(system)
        if search:
            where.append("lower(title) LIKE ?"); params.append(f"%{search.lower()}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY sort_title"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._item(r) for r in self.query(sql, params)]

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        return [self._item(r) for r in self.query("SELECT * FROM items ORDER BY added DESC, sort_title LIMIT ?", (limit,))]

    def item(self, item_id: str) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM items WHERE id=?", (item_id,))
        return self._item(r) if r else None

    def counts(self) -> dict[str, int]:
        return {r["system"]: r["n"] for r in self.query("SELECT system, COUNT(*) n FROM items GROUP BY system")}

    # ---- jobs ----------------------------------------------------------------
    def add_job(self, **job: Any) -> int:
        job.setdefault("state", "queued")
        job.setdefault("created", time.time())
        job["src"] = json.dumps(job["src"])
        job["opts"] = json.dumps(job.get("opts") or {})
        cols = ", ".join(job)
        marks = ", ".join(":" + k for k in job)
        cur = self.execute(f"INSERT INTO jobs({cols}) VALUES({marks})", job)
        return int(cur.lastrowid)

    @staticmethod
    def _job(row: dict[str, Any]) -> dict[str, Any]:
        row["src"] = json.loads(row["src"] or "[]")
        row["opts"] = json.loads(row.get("opts") or "{}")
        return row

    def jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        return [self._job(r) for r in self.query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))]

    def job(self, job_id: int) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        return self._job(r) if r else None

    def next_job(self) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1")
        return self._job(r) if r else None

    def update_job(self, job_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{k}=:{k}" for k in fields)
        self.execute(f"UPDATE jobs SET {sets} WHERE id=:id", {**fields, "id": job_id})

    def active_job_for(self, item_id: str) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM jobs WHERE item_id=? AND state IN ('queued','running','verifying') ORDER BY id DESC LIMIT 1", (item_id,))
        return self._job(r) if r else None

    def last_job_for(self, item_id: str) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM jobs WHERE item_id=? ORDER BY id DESC LIMIT 1", (item_id,))
        return self._job(r) if r else None

    def reset_running(self) -> None:
        """At start-up, jobs that were running when we died go back to the queue (they resume)."""
        self.execute("UPDATE jobs SET state='queued', step='Resuming' WHERE state IN ('running','verifying')")

    # ---- art ------------------------------------------------------------------
    def art(self, item_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM art WHERE item_id=?", (item_id,))

    def set_art(self, item_id: str, status: str, path: str = "") -> None:
        self.execute("INSERT OR REPLACE INTO art(item_id,status,path,checked) VALUES(?,?,?,?)", (item_id, status, path, time.time()))
