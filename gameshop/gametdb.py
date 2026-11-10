"""Retail box fronts from GameTDB (gametdb.com), the cover database behind USB loaders and
homebrew front ends. It covers the consoles libretro-thumbnails does not: Switch, 3DS, DS, Wii,
GameCube and PS3.

GameTDB keys games by its own codes (AXN7A, RMCE01, BLUS30443). Its free title list
("<code> = <name>", one line each) is downloaded per console and cached, and a game is found
by a code in the dump's name when there is one, else by name. Covers are plain image URLs:
    https://art.gametdb.com/<console>/<kind>/<region>/<code>.<ext>
"""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .switchdb import NotReady, norm

LIST_URL = "https://www.gametdb.com/{name}.txt?LANG=EN"
ART_URL = "https://art.gametdb.com/{console}/{kind}/{region}/{code}.{ext}"
REFRESH_AFTER = 7 * 86400
RETRY_AFTER = 3600

# system id -> (title list, art console, cover files to try, best first)
SOURCES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "switch": ("switchtdb", "switch", ("coverM.jpg", "cover.jpg")),
    "n3ds": ("3dstdb", "3ds", ("coverM.jpg", "cover.jpg")),
    "nds": ("dstdb", "ds", ("coverM.jpg", "cover.jpg")),
    "wii": ("wiitdb", "wii", ("cover.png",)),
    "gc": ("wiitdb", "wii", ("cover.png",)),
    "ps3": ("ps3tdb", "ps3", ("coverM.jpg", "cover.jpg")),
}
SYSTEMS = frozenset(SOURCES)
REGIONS = ("US", "EN", "JA", "DE", "FR", "ES", "IT", "KO", "NL", "PT", "RU", "ZHCN", "ZHTW", "AU")
_ITEM_REGION = {"USA": "US", "Europe": "EN", "Japan": "JA", "Germany": "DE", "France": "FR", "Spain": "ES",
                "Italy": "IT", "Korea": "KO", "Netherlands": "NL", "Australia": "AU", "China": "ZHCN", "Taiwan": "ZHTW"}
_NINTENDO_LETTER = {"E": "US", "P": "EN", "J": "JA", "K": "KO", "D": "DE", "F": "FR", "S": "ES", "I": "IT",
                    "H": "NL", "U": "AU", "W": "ZHTW", "C": "ZHCN", "R": "RU"}
_PS3_LETTER = {"U": "US", "E": "EN", "J": "JA", "K": "KO", "A": "EN", "H": "EN"}
_SWITCH_LETTER = {"A": "US", "B": "EN", "C": "JA"}
_PS3_SERIAL = re.compile(r"\b([BN][CL][AEHJKU]S\d{5})\b")
_WII_CODE = re.compile(r"\[([A-Z0-9]{6})\]")
_TRAIL = re.compile(r"\s*\((?:Demo|Virtual Console|PlayStation 3 the Best|Greatest Hits|Platinum)[^)]*\)\s*$", re.I)
_VERSION = re.compile(r"\s+v\d+(\.\d+)*$", re.I)


def code_region(system: str, code: str) -> str:
    """The region a GameTDB code was issued for, as a cover region, or '' when unknown."""
    if system == "ps3":
        return _PS3_LETTER.get(code[2:3], "")
    if system == "switch":
        return _SWITCH_LETTER.get(code[4:5], "")
    return _NINTENDO_LETTER.get(code[3:4], "")


def parse_list(text: str) -> dict[str, str]:
    """'CODE = Name' lines -> {code: name}; the first line is a header."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        code, sep, name = line.partition(" = ")
        code = code.strip()
        if sep and code and code != "TITLES" and name.strip() and " " not in code:
            out[code.strip()] = name.strip()
    return out


class _List:
    def __init__(self, by_code: dict[str, str]):
        self.by_code = by_code
        self.by_name: dict[str, list[str]] = {}
        for code, name in by_code.items():
            k = norm(_TRAIL.sub("", name))
            if k:
                self.by_name.setdefault(k, []).append(code)


class GameTDB:
    """Title lists cached under `dir`, downloaded in the background on first use."""

    def __init__(self, dir: Path, enabled: Callable[[], bool] = lambda: True,
                 hint: Callable[[dict[str, Any]], list[str]] | None = None):
        self.dir = dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self.hint = hint                      # item -> other names for the game (the Switch eShop name)
        self._lists: dict[str, _List] = {}
        self._downloading: set[str] = set()
        self._failed_at: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- title lists ----------------------------------------------------------------
    def load_from(self, name: str, text: str) -> None:
        """Tests and tools: use a title list without downloading."""
        with self._lock:
            self._lists[name] = _List(parse_list(text))

    def _list(self, name: str) -> _List:
        path = self.dir / f"{name}.txt"
        with self._lock:
            if name in self._lists:
                if path.exists() and time.time() - path.stat().st_mtime > REFRESH_AFTER:
                    self._start_download(name)
                return self._lists[name]
            if path.exists():
                try:
                    self._lists[name] = _List(parse_list(path.read_text(encoding="utf-8")))
                    return self._lists[name]
                except OSError:
                    pass
            if not self.enabled():
                raise NotReady()
            self._start_download(name)
            raise NotReady()

    def _start_download(self, name: str) -> None:
        # caller holds the lock
        if name in self._downloading or time.time() - self._failed_at.get(name, 0) < RETRY_AFTER:
            return
        self._downloading.add(name)
        threading.Thread(target=self._download, args=(name,), name=f"gameshop-gametdb-{name}", daemon=True).start()

    def _download(self, name: str) -> None:
        path = self.dir / f"{name}.txt"
        try:
            req = urllib.request.Request(LIST_URL.format(name=name), headers={"User-Agent": "GameShop/0.1"})
            with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310
                text = r.read().decode("utf-8", "replace")
            by_code = parse_list(text)
            if len(by_code) < 100:
                raise ValueError("title list too short")
            tmp = path.with_suffix(".part")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            with self._lock:
                self._lists[name] = _List(by_code)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            with self._lock:
                self._failed_at[name] = time.time()
        finally:
            with self._lock:
                self._downloading.discard(name)

    # ---- lookups ----------------------------------------------------------------------
    def codes(self, item: dict[str, Any]) -> list[str]:
        """GameTDB codes for this game, best region first. Raises NotReady while the list downloads."""
        system = item.get("system", "")
        src = SOURCES.get(system)
        if not src:
            return []
        lst = self._list(src[0])
        meta = item.get("meta") or {}
        text = f"{meta.get('stem', '')} {item.get('rel', '')}"
        found: list[str] = []
        if system == "ps3":
            found = [m for m in _PS3_SERIAL.findall(text.upper()) if m in lst.by_code]
        elif system in ("wii", "gc"):
            found = [m for m in _WII_CODE.findall(text.upper()) if m in lst.by_code]
        if not found:
            names = [item.get("title") or ""]
            if self.hint:
                names += self.hint(item)
            names += [_VERSION.sub("", n) for n in list(names)]
            seen: set[str] = set()
            for n in names:
                k = norm(n)
                if len(k) >= 3 and k not in seen:
                    seen.add(k)
                    found += [c for c in lst.by_name.get(k, []) if c not in found]
        want = _ITEM_REGION.get(str(item.get("region", "")).split(",")[0].strip(), "")
        rank = {r: i for i, r in enumerate(REGIONS)}
        found.sort(key=lambda c: (code_region(system, c) != want if want else False, rank.get(code_region(system, c), 99)))
        return found

    def urls(self, item: dict[str, Any], kind: str = "box") -> list[str] | None:
        """Cover URLs to try in order, or None when this is not a GameTDB console / no match."""
        if kind != "box" or item.get("system") not in SOURCES:
            return None
        codes = self.codes(item)
        if not codes:
            return None
        _, console, files = SOURCES[item["system"]]
        want = _ITEM_REGION.get(str(item.get("region", "")).split(",")[0].strip(), "")
        out: list[str] = []
        for code in codes[:3]:
            regions: list[str] = []
            for r in (want, code_region(item["system"], code), "US", "EN", "JA"):
                if r and r not in regions:
                    regions.append(r)
            for f in files:
                kind_, ext = f.split(".")
                out += [ART_URL.format(console=console, kind=kind_, region=r, code=code, ext=ext) for r in regions]
        return out[:16]
