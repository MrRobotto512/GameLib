"""Box scans from the LaunchBox Games Database (gamesdb.launchbox-app.com), the source Boxshelf
uses: free, no account, nearly every console, and real, high-resolution scans of the back and
the front of the same box.

Its whole database is one download, Metadata.zip (about 110 MB, a ~500 MB XML inside). It is
fetched once, when Settings' "Download back covers" asks for it, and boiled down to a small
index: for each console Game Shop knows, a game's normalised name -> its "Box - Back" and
"Box - Front" image files by region. Images are plain URLs:
    https://images.launchbox-app.com/<FileName>

A back and a front are taken from the same region's box whenever one region has both, so the two
sides of a case are the same edition, the same shape and equally sharp.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

from .switchdb import NotReady

METADATA_URL = "https://gamesdb.launchbox-app.com/Metadata.zip"
IMAGE_URL = "https://images.launchbox-app.com/{file}"
INDEX_VERSION = 2          # 2: fronts beside the backs
REFRESH_AFTER = 60 * 86400
TYPES = {"Box - Back": ("b", 0), "Box - Back - Reconstructed": ("b", 1),
         "Box - Front": ("f", 0), "Box - Front - Reconstructed": ("f", 1)}     # side; 1 = a remake, used last
KINDS = {"back": "b", "front": "f"}

# Game Shop system id -> LaunchBox platform name
PLATFORMS = {
    "psx": "Sony Playstation", "ps2": "Sony Playstation 2", "psp": "Sony PSP", "ps3": "Sony Playstation 3",
    "ps4": "Sony Playstation 4", "ps5": "Sony Playstation 5", "nes": "Nintendo Entertainment System",
    "snes": "Super Nintendo Entertainment System", "n64": "Nintendo 64", "gb": "Nintendo Game Boy",
    "gbc": "Nintendo Game Boy Color", "gba": "Nintendo Game Boy Advance", "nds": "Nintendo DS", "n3ds": "Nintendo 3DS",
    "gc": "Nintendo GameCube", "wii": "Nintendo Wii", "switch": "Nintendo Switch", "genesis": "Sega Genesis",
    "mastersystem": "Sega Master System", "gamegear": "Sega Game Gear", "segacd": "Sega CD", "sega32x": "Sega 32X",
    "saturn": "Sega Saturn", "dreamcast": "Sega Dreamcast", "xbox": "Microsoft Xbox", "xbox360": "Microsoft Xbox 360",
    "neogeo": "SNK Neo Geo AES", "pcengine": "NEC TurboGrafx-16", "atari2600": "Atari 2600", "atari7800": "Atari 7800",
    "atarilynx": "Atari Lynx", "atarijaguar": "Atari Jaguar", "c64": "Commodore 64", "amiga": "Commodore Amiga",
    "dos": "MS-DOS",
}
# the item's region -> LaunchBox's names for it, preferred in that order
_REGIONS = {"USA": ("North America", "United States"), "Europe": ("Europe", "United Kingdom"), "Japan": ("Japan",)}
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "xi": "11", "xii": "12",
          "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16"}
_ROMAN_RE = re.compile(r"\b(" + "|".join(sorted(_ROMAN, key=len, reverse=True)) + r")\b")
_TAGS = re.compile(r"\s*[(\[][^)\]]*[)\]]")


def norm(name: str) -> str:
    """A name reduced to what two spellings of one game share (Boxshelf's lbindex norm())."""
    s = unicodedata.normalize("NFD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    s = _ROMAN_RE.sub(lambda m: _ROMAN[m.group(1)], s)
    return re.sub(r"\s+", " ", s).strip()


def build_index(xml_stream, platforms: set[str]) -> dict[str, dict[str, dict[str, list[list[str]]]]]:
    """{platform: {norm(name): {"b": [[region, file], ...], "f": [...]}}}: the back and front scans
    of every game on platforms (real scans before remakes), reading the XML as a stream (~500 MB)."""
    games: dict[str, tuple[str, list[str]]] = {}         # DatabaseID -> (platform, names)
    alts: dict[str, list[str]] = {}
    images: dict[str, list[tuple[int, str, str, str]]] = {}
    root = None
    for ev, el in ET.iterparse(xml_stream, events=("start", "end")):
        if ev == "start":
            if root is None:
                root = el
            continue
        tag = el.tag
        if tag == "Game":
            plat = el.findtext("Platform") or ""
            if plat in platforms:
                games[el.findtext("DatabaseID") or ""] = (plat, [el.findtext("Name") or ""])
        elif tag == "GameAlternateName":
            alts.setdefault(el.findtext("DatabaseID") or "", []).append(el.findtext("AlternateName") or "")
        elif tag == "GameImage":
            side = TYPES.get(el.findtext("Type") or "")
            if side:
                images.setdefault(el.findtext("DatabaseID") or "", []).append(
                    (side[1], side[0], el.findtext("Region") or "", el.findtext("FileName") or ""))
        else:
            continue
        root.clear()                                       # done with it: keep memory flat over ~500 MB
    out: dict[str, dict[str, dict[str, list[list[str]]]]] = {p: {} for p in platforms}
    for gid, files in images.items():
        g = games.get(gid)
        if not g:
            continue
        plat, names = g
        for name in names + alts.get(gid, []):
            key = norm(name)
            if not key:
                continue
            entry = out[plat].setdefault(key, {})
            for _, side, region, file in sorted(files, key=lambda f: f[0]):       # stable: scans first
                have = entry.setdefault(side, [])
                if [region, file] not in have:
                    have.append([region, file])
    return out


def _by_region(files: list[list[str]], want: tuple[str, ...], first: str | None = None) -> list[list[str]]:
    """files with region `first` leading, then the item's own regions in order, then World."""
    return sorted(files, key=lambda f: (f[0] != first if first is not None else False, f[0] not in want,
                                        want.index(f[0]) if f[0] in want else 0, f[0] != "World"))


class LaunchBox:
    """The back-cover index, downloaded and built in the background on request."""

    def __init__(self, cache_dir: Path, enabled: Callable[[], bool] = lambda: True,
                 hint: Callable[[dict[str, Any]], list[str]] | None = None):
        self.dir = cache_dir
        self.enabled = enabled
        self.hint = hint                    # item -> other names for it (the libretro database's)
        self.index: dict[str, dict[str, dict[str, list[list[str]]]]] | None = None
        self.state, self.pct, self.error = "idle", 0, ""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.on_ready: Callable[[], None] | None = None

    @property
    def index_path(self) -> Path:
        return self.dir / "backs.json"

    def load(self) -> bool:
        """The index from disk, when there is a current one."""
        if self.index is not None:
            return True
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if data.get("version") != INDEX_VERSION:
            return False
        self.index, self.state = data["platforms"], "ready"
        return True

    def outdated(self) -> bool:
        """An index from an older Game Shop is on disk (it knows backs only): it is rebuilt at start."""
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8")).get("version") != INDEX_VERSION
        except (OSError, ValueError):
            return False

    def status(self) -> dict[str, Any]:
        self.load()
        age = time.time() - self.index_path.stat().st_mtime if self.index_path.exists() else None
        return {"state": self.state, "pct": self.pct, "error": self.error,
                "games": sum(len(v) for v in (self.index or {}).values()), "age_days": round(age / 86400, 1) if age else None}

    def start(self, refresh: bool = False) -> None:
        """Download and index LaunchBox's database in the background (once; again when refresh or
        when the index is older than two months)."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if not refresh and self.load() and time.time() - self.index_path.stat().st_mtime < REFRESH_AFTER:
                if self.on_ready:
                    threading.Thread(target=self.on_ready, name="gameshop-backs", daemon=True).start()
                return
            self._thread = threading.Thread(target=self._build, name="gameshop-launchbox", daemon=True)
            self._thread.start()

    def _build(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        zpath = self.dir / "Metadata.zip"
        try:
            self.state, self.pct, self.error = "downloading", 0, ""
            req = urllib.request.Request(METADATA_URL, headers={"User-Agent": "GameShop"})
            with urllib.request.urlopen(req, timeout=60) as r, open(zpath.with_suffix(".part"), "wb") as f:   # noqa: S310
                total, done = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    self.pct = int(done * 100 / total) if total else 0
            zpath.with_suffix(".part").replace(zpath)
            self.state, self.pct = "indexing", 0
            with zipfile.ZipFile(zpath) as z, z.open("Metadata.xml") as xml:
                index = build_index(xml, set(PLATFORMS.values()))
            tmp = self.index_path.with_suffix(".part")
            tmp.write_text(json.dumps({"version": INDEX_VERSION, "platforms": index}), encoding="utf-8")
            tmp.replace(self.index_path)
            self.index, self.state = index, "ready"
        except Exception as e:   # noqa: BLE001
            self.state, self.error = "failed", str(e)
            return
        finally:
            zpath.unlink(missing_ok=True)                 # the index is all that is kept
            zpath.with_suffix(".part").unlink(missing_ok=True)
        if self.on_ready:
            self.on_ready()

    def urls(self, item: dict[str, Any], kind: str) -> list[str] | None:
        """URLs of the item's back cover (kind "back") or the front of the same box ("front"): the
        region that has both sides first, the item's own region before others. Raises NotReady
        until the index exists, so nothing is recorded as missing before "Download back covers"
        was pressed."""
        side = KINDS.get(kind)
        if not side:
            return None
        plat = PLATFORMS.get(item.get("system", ""))
        if not plat:
            return []
        if not self.load():
            raise NotReady()
        games = self.index.get(plat) or {}
        names = [item.get("title") or "", _TAGS.sub("", Path(item.get("rel") or "").stem)]
        if self.hint:
            names += [_TAGS.sub("", n) for n in self.hint(item)]
        entry: dict[str, list[list[str]]] = {}
        for n in names:
            entry = games.get(norm(n)) or {}
            if entry:
                break
        want = _REGIONS.get(item.get("region") or "", ("North America", "United States"))
        other = {f[0] for f in entry.get("f" if side == "b" else "b", [])}
        both = [f[0] for f in _by_region(entry.get(side, []), want) if f[0] in other]     # regions with both sides
        files = _by_region(entry.get(side, []), want, both[0] if both else None)
        return [IMAGE_URL.format(file=f[1]) for f in files[:4]]

    back_urls = urls
