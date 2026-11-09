"""Nintendo Switch art and facts from the community titledb (blawar/titledb), matched by the
16-digit title ID that most dumps carry in their file name, e.g. "Broforce [010060A00B53C000][v0]".

Update and DLC IDs are derived from the base game's ID, so they share its art:
  base    ....000      (low 12 bits are zero)
  update  ....800      (base | 0x800)
  dlc     base + 0x1000 + n   (n in the low 12 bits)

The 90 MB US.en.json is downloaded once by this PC, compacted to the fields we use and cached;
the big file is deleted afterwards. Images come from Nintendo's eShop CDN URLs in the records.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

TITLEDB_URL = "https://raw.githubusercontent.com/blawar/titledb/master/US.en.json"
REFRESH_AFTER = 14 * 86400
RETRY_AFTER = 3600
_TID = re.compile(r"\[([0-9A-Fa-f]{16})\]")
_NORM = re.compile(r"[^a-z0-9]+")
_KEEP = ("name", "iconUrl", "bannerUrl", "publisher", "developer", "releaseDate", "numberOfPlayers",
         "category", "description", "intro", "screenshots", "isDemo", "nsuId")


class NotReady(Exception):
    """The database is still downloading; try again later (do not record a miss)."""


def norm(name: str) -> str:
    return _NORM.sub(" ", name.lower()).strip()


def parse_title_id(stem: str) -> str | None:
    m = _TID.search(stem)
    return m.group(1).upper() if m else None


def classify(tid: str) -> tuple[str, str]:
    """-> (kind, base id) where kind is base | update | dlc."""
    n = int(tid, 16)
    low = n & 0xFFF
    if low == 0:
        return "base", tid.upper()
    if low == 0x800:
        return "update", f"{n - 0x800:016X}"
    return "dlc", f"{(n - 0x1000) & ~0xFFF:016X}"


def compact(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """titledb's {nsuId: record} -> {TITLE_ID: slim record}, keeping the richest record per id."""
    out: dict[str, dict[str, Any]] = {}
    for rec in raw.values():
        tid = (rec.get("id") or "").upper()
        if len(tid) != 16:
            continue
        slim = {k: rec[k] for k in _KEEP if rec.get(k) not in (None, "", [], 0)}
        if "screenshots" in slim:
            slim["screenshots"] = slim["screenshots"][:4]
        prev = out.get(tid)
        if prev is None or len(slim) > len(prev):
            out[tid] = slim
    return out


class SwitchTitleDB:
    def __init__(self, cache_dir: Path, enabled: Callable[[], bool] = lambda: True,
                 on_ready: Callable[[], None] | None = None, url: str = TITLEDB_URL):
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self.on_ready = on_ready
        self.url = url
        self.path = self.dir / "switch_titles.json"
        self._by_id: dict[str, dict[str, Any]] | None = None
        self._by_name: dict[str, str] = {}
        self._lock = threading.Lock()
        self._downloading = False
        self._failed_at = 0.0

    # ---- loading -------------------------------------------------------------------
    def ready(self) -> bool:
        return self._by_id is not None

    def status(self) -> str:
        if self._by_id is not None:
            return "ready"
        if self._downloading:
            return "downloading"
        return "failed" if self._failed_at else "pending"

    def _ensure(self) -> dict[str, dict[str, Any]]:
        """The id index, loading the cache or starting a background download. Raises NotReady."""
        with self._lock:
            if self._by_id is not None:
                if self.path.exists() and time.time() - self.path.stat().st_mtime > REFRESH_AFTER:
                    self._start_download()
                return self._by_id
            if self.path.exists():
                try:
                    self._load(json.loads(self.path.read_text(encoding="utf-8")))
                    return self._by_id or {}
                except (OSError, ValueError):
                    pass
            if not self.enabled():
                raise NotReady()
            self._start_download()
            raise NotReady()

    def _load(self, by_id: dict[str, dict[str, Any]]) -> None:
        names: dict[str, str] = {}
        for tid, rec in by_id.items():
            if classify(tid)[0] != "base" or not rec.get("name"):
                continue
            k = norm(rec["name"])
            if k and (k not in names or (by_id[names[k]].get("isDemo") and not rec.get("isDemo"))):
                names[k] = tid
        self._by_id, self._by_name = by_id, names

    def _start_download(self) -> None:
        # caller holds the lock
        if self._downloading or time.time() - self._failed_at < RETRY_AFTER:
            return
        self._downloading = True
        threading.Thread(target=self._download, name="gameshop-titledb", daemon=True).start()

    def _download(self) -> None:
        big = self.dir / "US.en.json.part"
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "GameShop/0.1"})
            with urllib.request.urlopen(req, timeout=120) as r, open(big, "wb") as f:   # noqa: S310
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            with open(big, "rb") as f:
                raw = json.load(f)
            by_id = compact(raw)
            del raw
            tmp = self.path.with_suffix(".part")
            tmp.write_text(json.dumps(by_id, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
            with self._lock:
                self._load(by_id)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            with self._lock:
                self._failed_at = time.time()
            return
        finally:
            big.unlink(missing_ok=True)
            with self._lock:
                self._downloading = False
        if self.on_ready:
            self.on_ready()

    def load_from(self, by_id: dict[str, dict[str, Any]]) -> None:
        """Tests and tools: use a prepared compact index without downloading."""
        with self._lock:
            self._load(by_id)

    # ---- lookups -------------------------------------------------------------------
    def record(self, item: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
        """(base game record, kind) for a Switch item, by title id or else by name. Raises NotReady."""
        by_id = self._ensure()
        meta = item.get("meta") or {}
        tid = meta.get("title_id") or parse_title_id(meta.get("stem") or item.get("rel", ""))
        if tid:
            kind, base = classify(tid)
            rec = by_id.get(base) or by_id.get(tid)
            if rec:
                return rec, kind
        title = item.get("title") or ""
        for cand in (title, re.sub(r"\s+v\d+(\.\d+)*$", "", title, flags=re.I)):
            k = norm(cand)
            if len(k) >= 3 and k in self._by_name:
                return by_id[self._by_name[k]], "base"
        return None

    def art_url(self, item: dict[str, Any], kind: str = "box") -> str | None:
        found = self.record(item)
        if not found:
            return None
        rec = found[0]
        if kind == "box":
            return rec.get("iconUrl")
        if kind == "title":
            return rec.get("bannerUrl")
        shots = rec.get("screenshots") or []
        return shots[0] if shots else None

    def facts(self, item: dict[str, Any]) -> dict[str, Any] | None:
        found = self.record(item)
        if not found:
            return None
        rec, kind = found
        out: dict[str, Any] = {}
        date = str(rec.get("releaseDate") or "")
        if len(date) == 8 and date.isdigit():
            out["year"], out["month"] = int(date[:4]), int(date[4:6])
        for src, dst in (("publisher", "publisher"), ("developer", "developer"), ("numberOfPlayers", "players")):
            if rec.get(src):
                out[dst] = rec[src]
        if rec.get("category"):
            out["genre"] = ", ".join(rec["category"][:3])
        if kind == "base":
            if rec.get("description"):
                out["description"] = rec["description"].strip()
        else:
            out["part"] = ("Update for " if kind == "update" else "DLC for ") + (rec.get("name") or "the base game")
        if rec.get("name"):
            out["game"] = rec["name"]
        return out or None
