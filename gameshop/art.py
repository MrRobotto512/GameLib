"""Cover art from libretro-thumbnails, fetched by this PC and cached on disk.

https://raw.githubusercontent.com/libretro-thumbnails/<System_Repo>/master/Named_Boxarts/<Name>.png
where <Name> is the No-Intro / Redump name with  &*/:`<>?\\|"  replaced by '_'.

Other sources plug in as providers (asked before libretro) and fallbacks (asked after a real
miss): GameTDB for the consoles libretro lacks, serial-keyed PS1 / PS2 covers, package icons.
"""
from __future__ import annotations

import os
import re
import ssl
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


class ArtSource:
    """An art lookup for some systems, optionally with a check on what it returns (a database's
    generic placeholder cover is no cover)."""

    def __init__(self, fn: Callable[[dict[str, Any], str], str | list[str] | None], systems: Iterable[str] | None = None,
                 accept: Callable[[bytes], bool] | None = None):
        self.fn, self.systems, self.accept = fn, (set(systems) if systems else None), accept

    def __call__(self, item: dict[str, Any], kind: str) -> str | list[str] | None:
        if self.systems is not None and item.get("system") not in self.systems:
            return None
        return self.fn(item, kind)


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
        # back covers: libretro has none, and the front sources must not be asked for them
        self.backs: list[Callable[[dict[str, Any], str], str | list[str] | None]] = []

    # back and front: the two sides of one box scan, from self.backs (LaunchBox). A front, when
    # there is one, is what kind "box" shows: it is the same edition as the back, and sharper.
    KINDS = {"box": "Named_Boxarts", "snap": "Named_Snaps", "title": "Named_Titles", "back": None, "front": None}
    SCAN_WIDTH = 1200             # scans are kept this wide at most (they come up to ~2500 px, 3 MB)

    def path_for(self, item_id: str, kind: str = "box") -> Path:
        return self.dir / (f"{item_id}.png" if kind == "box" else f"{item_id}.{kind}.png")

    def has(self, item_id: str) -> bool:
        """There is a cover to show for the item."""
        return self.path_for(item_id).exists() or self.path_for(item_id, "front").exists()

    def shrink_scans(self) -> int:
        """Scans kept at full size by an earlier version, made SCAN_WIDTH wide. Returns how many."""
        n = 0
        for kind in ("back", "front"):
            for p in self.dir.glob(f"*.{kind}.png"):
                try:
                    if p.stat().st_size <= SCAN_BYTES:
                        continue
                    data = p.read_bytes()
                    small = _shrunk(data, self.SCAN_WIDTH)
                    if len(small) < len(data):
                        tmp = p.with_suffix(".part")
                        tmp.write_bytes(small)
                        os.replace(tmp, p)
                        n += 1
                except OSError:
                    continue
        return n

    def version(self, item_id: str) -> int:
        """0 when the item has no cover, else a number that changes when its cover does."""
        for kind in ("front", "box"):
            try:
                return int(self.path_for(item_id, kind).stat().st_mtime) or 1
            except OSError:
                pass
        return 0

    def shape(self, item_id: str) -> float:
        """Width / height of the item's cover as shown, 0 when it has none."""
        for kind in ("front", "box"):
            p = self.path_for(item_id, kind)
            if p.exists():
                try:
                    from PIL import Image
                    with Image.open(p) as im:
                        return im.width / im.height
                except (OSError, ValueError, ZeroDivisionError):
                    pass
        return 0

    def thumb(self, src: Path, width: int, shape: float = 0) -> Path:
        """src scaled down to width pixels as a JPEG (the PS5 app's grid: a full cover is ~0.5 MB of
        PNG), made once and kept beside the art. src itself when it is already that narrow.

        shape (width / height) makes the picture exactly that shape: a back cover is given its
        front's, so the two sides of a box are drawn the same size. A scan a little off (a few
        percent, as two scans of one box are) is stretched to it; one far off is another edition's
        and is cropped around its middle instead."""
        tag = f".s{round(shape * 1000)}" if shape else ""
        out = self.dir / "thumbs" / f"{src.stem}.w{width}{tag}.jpg"
        if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
            return out
        from PIL import Image
        with Image.open(src) as im:
            if im.width <= width and not shape:
                return src
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, (20, 20, 20))         # transparent corners on the card colour
            flat.paste(im, mask=im.getchannel("A"))
            if shape:
                mine = flat.width / flat.height
                if mine > shape * 1.15:                             # much wider: keep the middle
                    w = round(flat.height * shape)
                    flat = flat.crop(((flat.width - w) // 2, 0, (flat.width - w) // 2 + w, flat.height))
                elif mine < shape / 1.15:                           # much taller
                    h = round(flat.width / shape)
                    flat = flat.crop((0, (flat.height - h) // 2, flat.width, (flat.height - h) // 2 + h))
                width = min(width, flat.width)
                flat = flat.resize((width, max(1, round(width / shape))), Image.LANCZOS)
            else:
                flat = flat.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
        out.parent.mkdir(exist_ok=True)
        tmp = out.with_suffix(f".{threading.get_ident()}.part")
        flat.save(tmp, "JPEG", quality=85)
        os.replace(tmp, out)
        return out

    def get(self, item: dict[str, Any], fetch: bool = True, kind: str = "box") -> Path | None:
        """Cached image path, fetching it now when allowed. None when there is none."""
        if kind == "box":
            front = self.path_for(item["id"], "front")
            if front.exists():
                return front
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
        if kind in ("back", "front"):
            return self._fetch_scan(item, key, kind) if system else None
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

    def _fetch_scan(self, item: dict[str, Any], key: str, kind: str) -> Path | None:
        with self._lock:
            if key in self._inflight:
                return None
            self._inflight.add(key)
        try:
            found = self._from(self.backs, item, kind)
            if found is None:
                return None                          # the index is not there yet: nothing to record
            if found:
                return self._store(item, key, kind, _shrunk(found, self.SCAN_WIDTH))
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
            accept = getattr(source, "accept", None)
            for url in urls or []:
                data = _get(url, timeout=20)
                if data and (accept is None or accept(data)):
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
        todo = [it for it in items if not self.has(it["id"])]

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


SCAN_BYTES = 400_000          # a scan under this, and no wider than asked, is kept as it came


def _shrunk(data: bytes, width: int) -> bytes:
    """A scan as a JPEG no wider than width: LaunchBox's come up to ~2500 px and 3 MB, many as
    PNG, which for a few thousand games is gigabytes. One already small is kept as it came."""
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            if im.width <= width and len(data) <= SCAN_BYTES:
                return data
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            return buf.getvalue() if buf.tell() < len(data) else data
    except Exception:   # noqa: BLE001
        return data


# One TLS setup for every download. urllib's default builds a new one per request, and on Windows
# that reads the whole certificate store each time: with four threads fetching covers it kept a
# CPU core busy and slowed every page the store served.
_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def open_url(req, timeout: float):
    return _OPENER.open(req, timeout=timeout)


def _get_status(url: str, timeout: float = 8.0) -> tuple[int, bytes | None]:
    """(HTTP status or 0, body) — 404 is the only answer that means 'this art does not exist'."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GameShop/0.1"})
        with open_url(req, timeout) as r:
            return r.status, (r.read() if r.status == 200 else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _get(url: str, timeout: float = 8.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GameShop/0.1"})
        with open_url(req, timeout) as r:
            if r.status == 200:
                return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    return None
