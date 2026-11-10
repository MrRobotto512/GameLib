"""Cover art from libretro-thumbnails, fetched by this PC and cached on disk.

https://raw.githubusercontent.com/libretro-thumbnails/<System_Repo>/master/Named_Boxarts/<Name>.png
where <Name> is the No-Intro / Redump name with  &*/:`<>?\\|"  replaced by '_'.

Other sources plug in as providers (asked before libretro) and fallbacks (asked after a real
miss): GameTDB for the consoles libretro lacks, serial-keyed PS1 / PS2 covers, package icons.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

from .db import Database
from .naming import libretro_thumb_name
from .switchdb import NotReady
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
        # (item, kind) -> image URL, a list of URLs to try in order, or None; may raise
        # switchdb.NotReady. Providers are asked before libretro, fallbacks after it missed.
        self.providers: list[Callable[[dict[str, Any], str], str | list[str] | None]] = []
        self.fallbacks: list[Callable[[dict[str, Any], str], str | list[str] | None]] = []
        # item -> extra names to try on libretro-thumbnails (the database's own name for the game)
        self.name_hint: Callable[[dict[str, Any]], list[str]] | None = None

    KINDS = {"box": "Named_Boxarts", "snap": "Named_Snaps", "title": "Named_Titles"}

    def path_for(self, item_id: str, kind: str = "box") -> Path:
        return self.dir / (f"{item_id}.png" if kind == "box" else f"{item_id}.{kind}.png")

    def get(self, item: dict[str, Any], fetch: bool = True, kind: str = "box") -> Path | None:
        """Cached image path, fetching it now when allowed. None when there is none."""
        p = self.path_for(item["id"], kind)
        if p.exists():
            return p
        key = item["id"] if kind == "box" else f"{item['id']}:{kind}"
        row = self.db.art(key)
        if row and row["status"] == "missing" and time.time() - row["checked"] < RECHECK_MISSING_AFTER:
            return None
        if not fetch or not self.enabled():
            return None
        return self.fetch(item, kind)

    def fetch(self, item: dict[str, Any], kind: str = "box") -> Path | None:
        key = item["id"] if kind == "box" else f"{item['id']}:{kind}"
        system = self.systems.get(item["system"])
        if not system or kind not in self.KINDS or (not system.thumbs and not self.providers and not self.fallbacks):
            self.db.set_art(key, "missing")
            return None
        with self._lock:
            if key in self._inflight:
                return None
            self._inflight.add(key)
        try:
            unsure = False
            found = self._from(self.providers, item, kind)
            if found is None:
                return None                          # a source is still loading: try again later
            if found:
                return self._store(item, key, kind, found)
            if system.thumbs:
                names = candidates(item)
                if self.name_hint:
                    names += [n for n in self.name_hint(item) if n not in names]
                for name in names:
                    url = BASE.format(repo=system.thumbs, kind=self.KINDS[kind],
                                      name=urllib.parse.quote(libretro_thumb_name(name)))
                    status, data = _get_status(url)
                    if data:
                        return self._store(item, key, kind, data)
                    if status != 404:
                        unsure = True                # throttled, offline, timeout: not a real miss
            found = self._from(self.fallbacks, item, kind)
            if found is None:
                return None
            if found:
                return self._store(item, key, kind, found)
            if not unsure:
                self.db.set_art(key, "missing")
            return None
        finally:
            with self._lock:
                self._inflight.discard(key)

    def _from(self, sources, item: dict[str, Any], kind: str) -> bytes | None | bool:
        """Image bytes from the first source that has one; False when none has, None when a
        source is still loading its index (nothing is recorded then)."""
        for source in sources:
            try:
                urls = source(item, kind)
            except NotReady:
                return None
            if isinstance(urls, str):
                urls = [urls]
            for url in urls or []:
                data = _get(url, timeout=20)
                if data:
                    return data
        return False

    def _store(self, item: dict[str, Any], key: str, kind: str, data: bytes) -> Path:
        p = self.path_for(item["id"], kind)
        p.write_bytes(_as_png(data))
        self.db.set_art(key, "ok", str(p))
        return p

    def prefetch(self, items: Iterable[dict[str, Any]], workers: int = 4) -> None:
        """Background: warm the cache for a list of items."""
        import concurrent.futures as cf
        todo = [it for it in items if not self.path_for(it["id"]).exists()]

        def run():
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(lambda it: self.get(it), todo))
        threading.Thread(target=run, name="gameshop-art", daemon=True).start()


def _as_png(data: bytes) -> bytes:
    """WebP (the patch sites' icons) -> PNG so every cached file is a browser-safe image."""
    if data[:4] != b"RIFF":
        return data
    try:
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.open(io.BytesIO(data)).convert("RGBA").save(buf, "PNG")
        return buf.getvalue()
    except Exception:   # noqa: BLE001
        return data


def _get_status(url: str, timeout: float = 8.0) -> tuple[int, bytes | None]:
    """(HTTP status or 0, body) — 404 is the only answer that means 'this art does not exist'."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GameShop/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
            return r.status, (r.read() if r.status == 200 else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _get(url: str, timeout: float = 8.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GameShop/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
            if r.status == 200:
                return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    return None
