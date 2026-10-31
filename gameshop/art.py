"""Cover art from libretro-thumbnails, fetched by this PC and cached on disk.

https://raw.githubusercontent.com/libretro-thumbnails/<System_Repo>/master/Named_Boxarts/<Name>.png
where <Name> is the No-Intro / Redump name with  &*/:`<>?\\|"  replaced by '_'.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .db import Database
from .naming import libretro_thumb_name
from .systems import SystemsTable

BASE = "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/{kind}/{name}.png"
RECHECK_MISSING_AFTER = 7 * 86400
_DISC_TAG = re.compile(r"\s*\((?:Disc|Disk|CD)\s*[0-9A-Z]+(?:\s+of\s+\d+)?\)", re.I)


def candidates(item: dict[str, Any]) -> list[str]:
    """Names to try, most specific first."""
    stem = item.get("meta", {}).get("stem") or Path(item["rel"]).stem
    out = [stem]
    no_disc = _DISC_TAG.sub("", stem)
    if no_disc != stem:
        out.append(no_disc)
        out.append(f"{no_disc} (Disc 1)")
    # title + region only, dropping the other tags
    region = item.get("region") or ""
    title = item.get("title") or ""
    if title:
        # libretro names keep ", The" at the end
        t = title
        m = re.match(r"^(The|A|An) (.+?)( - .*)?$", t)
        if m:
            t = f"{m.group(2)}, {m.group(1)}{m.group(3) or ''}"
        if region:
            out.append(f"{t} ({region})")
        out.append(t)
    seen, uniq = set(), []
    for n in out:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


class ArtCache:
    def __init__(self, db: Database, systems: SystemsTable, cache_dir: Path, enabled: lambda: bool = lambda: True):
        self.db = db
        self.systems = systems
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    def path_for(self, item_id: str) -> Path:
        return self.dir / f"{item_id}.png"

    def get(self, item: dict[str, Any], fetch: bool = True) -> Path | None:
        """Cached cover path, fetching it now when allowed. None when there is none."""
        p = self.path_for(item["id"])
        if p.exists():
            return p
        row = self.db.art(item["id"])
        if row and row["status"] == "missing" and time.time() - row["checked"] < RECHECK_MISSING_AFTER:
            return None
        if not fetch or not self.enabled():
            return None
        return self.fetch(item)

    def fetch(self, item: dict[str, Any]) -> Path | None:
        system = self.systems.get(item["system"])
        if not system or not system.thumbs:
            self.db.set_art(item["id"], "missing")
            return None
        with self._lock:
            if item["id"] in self._inflight:
                return None
            self._inflight.add(item["id"])
        try:
            for name in candidates(item):
                url = BASE.format(repo=system.thumbs, kind="Named_Boxarts",
                                  name=urllib.parse.quote(libretro_thumb_name(name)))
                data = _get(url)
                if data:
                    p = self.path_for(item["id"])
                    p.write_bytes(data)
                    self.db.set_art(item["id"], "ok", str(p))
                    return p
            self.db.set_art(item["id"], "missing")
            return None
        finally:
            with self._lock:
                self._inflight.discard(item["id"])

    def prefetch(self, items: Iterable[dict[str, Any]], workers: int = 4) -> None:
        """Background: warm the cache for a list of items."""
        import concurrent.futures as cf
        todo = [it for it in items if not self.path_for(it["id"]).exists()]

        def run():
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(lambda it: self.get(it), todo))
        threading.Thread(target=run, name="gameshop-art", daemon=True).start()


def _get(url: str, timeout: float = 8.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GameShop/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
            if r.status == 200:
                return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    return None
