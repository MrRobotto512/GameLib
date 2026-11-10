"""Photos of the machines themselves, for the Consoles page: one Wikimedia Commons file per
system (named in systems.toml), fetched once by the PC and cached next to the cover art.

The photos are studio shots on white. The white is knocked out here so the console sits on
the dark storefront by itself: a flood fill from the picture's edges marks everything that is
white and connected to the border, that becomes transparent, and the picture is cropped to
what is left. Commons renders SVGs to PNG and resizes on the way, via Special:FilePath."""
from __future__ import annotations

import io
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .systems import System

FILE_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width={width}"
WIDTH = 900
_KEY = (255, 0, 255)                     # fill colour no product shot uses


class ConsolePhotos:
    def __init__(self, cache_dir: Path, enabled: Callable[[], bool] = lambda: True):
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self._failed: dict[str, float] = {}
        self._lock = threading.Lock()

    def path_for(self, system: System) -> Path:
        return self.dir / f"console-{system.id}.png"

    def get(self, system: System) -> Path | None:
        """The cached cut-out, fetching and cutting it now when allowed; None when there is none."""
        if not system.photo:
            return None
        p = self.path_for(system)
        if p.exists():
            return p
        if not self.enabled():
            return None
        with self._lock:
            if time.time() - self._failed.get(system.id, 0) < 600:
                return None
        data = fetch(system.photo)
        png = cutout(data) if data else None
        if not png:
            with self._lock:
                self._failed[system.id] = time.time()
            return None
        tmp = p.with_suffix(".png.part")
        tmp.write_bytes(png)
        tmp.replace(p)
        return p


def fetch(name: str, width: int = WIDTH) -> bytes | None:
    url = FILE_URL.format(name=urllib.parse.quote(name), width=width)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GameShop/0.1 (https://github.com/MrRobotto512/GameLib)"})
        with urllib.request.urlopen(req, timeout=30) as r:   # noqa: S310
            data = r.read() if r.status == 200 else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    if not data or data[:2] not in (b"\xff\xd8", b"\x89P"):
        return None
    return data


def cutout(data: bytes, thresh: int = 48, margin: float = 0.03) -> bytes | None:
    """PNG with the white backdrop transparent and the picture cropped to the object.
    A picture that already has transparent corners is only cropped."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:   # noqa: BLE001
        return None
    w, h = im.size
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    if any(im.getpixel(c)[3] < 20 for c in corners):
        alpha = im.getchannel("A")                          # already cut out upstream
    else:
        rgb = im.convert("RGB")
        for c in corners:
            r, g, b = rgb.getpixel(c)
            if min(r, g, b) > 200:                           # a white-ish edge pixel: fill from it
                ImageDraw.floodfill(rgb, c, _KEY, thresh=thresh)
        key = Image.new("RGB", rgb.size, _KEY)
        alpha = ImageChops.difference(rgb, key).convert("L").point(lambda v: 255 if v > 8 else 0)
        alpha = alpha.filter(ImageFilter.MinFilter(3))        # eat the bright fringe left at the edge
        im.putalpha(alpha)
    box = alpha.getbbox()
    if not box:
        return None
    mx, my = int(w * margin), int(h * margin)
    im = im.crop((max(0, box[0] - mx), max(0, box[1] - my), min(w, box[2] + mx), min(h, box[3] + my)))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()
