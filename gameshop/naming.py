"""Turn a backup's file name into a title, a region and tags, the way No-Intro / Redump name things.

    "Crash Bandicoot (USA) (Rev 1) [!].chd"  ->  title "Crash Bandicoot", region "USA", tags ["Rev 1", "!"]
    "Final Fantasy VII (USA) (Disc 1).cue"    ->  title "Final Fantasy VII", disc 1
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PAREN = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")
_DISC = re.compile(r"^(?:disc|disk|cd|side)\s*([0-9A-Z]+)(?:\s+of\s+\d+)?$", re.I)
_REGIONS = {
    "usa": "USA", "us": "USA", "u": "USA", "europe": "Europe", "eu": "Europe", "e": "Europe",
    "japan": "Japan", "jp": "Japan", "j": "Japan", "world": "World", "w": "World",
    "australia": "Australia", "korea": "Korea", "asia": "Asia", "brazil": "Brazil", "canada": "Canada",
    "france": "France", "germany": "Germany", "spain": "Spain", "italy": "Italy", "netherlands": "Netherlands",
    "sweden": "Sweden", "china": "China", "taiwan": "Taiwan", "uk": "UK", "ntsc": "USA", "pal": "Europe",
    "ntsc-u": "USA", "ntsc-j": "Japan", "ntsc-u/c": "USA",
}
_ARTICLES = ("the ", "a ", "an ")


@dataclass
class ParsedName:
    title: str
    region: str = ""
    disc: str = ""
    tags: list[str] = field(default_factory=list)
    stem: str = ""          # the name without extension, untouched

    @property
    def sort_title(self) -> str:
        t = self.title.lower()
        for a in _ARTICLES:
            if t.startswith(a):
                t = t[len(a):] + ", " + a.strip()
                break
        return re.sub(r"[^a-z0-9]+", " ", t).strip()


_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[a-z])(?=\d)|(?<=\d)(?=[A-Z][a-z])")


def split_camel(word: str) -> str:
    """'1000ccTurbo' -> '1000cc Turbo', 'ChaosEngine2' -> 'Chaos Engine 2'."""
    return _CAMEL.sub(" ", word).strip()


def parse_name(stem: str) -> ParsedName:
    """Parse a file stem (no extension)."""
    out = ParsedName(title=stem.strip(), stem=stem)
    tags: list[str] = []
    if " " not in stem and "_" in stem and "(" not in stem:
        # WHDLoad-style archive name: Title_v1.2_AGA_CD32 — camel-cased title, underscore tags
        head, *rest = stem.split("_")
        title = split_camel(head)
        if len(title) >= 2:
            out.title, out.tags = title, [r for r in rest if r]
            return out
    region = ""
    disc = ""
    first = _PAREN.search(stem)
    base = stem[: first.start()] if first else stem
    for m in _PAREN.finditer(stem):
        raw = m.group(1).strip()
        if not raw:
            continue
        dm = _DISC.match(raw)
        if dm:
            disc = dm.group(1)
            continue
        parts = [p.strip() for p in raw.split(",")]
        if not region and all(p.lower() in _REGIONS for p in parts):
            region = _REGIONS[parts[0].lower()]
            if len(parts) > 1 and region != "World":
                region = "World" if len(parts) > 2 else f"{_REGIONS[parts[0].lower()]}, {_REGIONS[parts[1].lower()]}"
            continue
        tags.append(raw)
    title = base.strip().rstrip("-–_ ").strip()
    vm = re.search(r"\s+(v\d+(?:\.\d+)+)$", title, re.I)
    if vm:
        tags.insert(0, vm.group(1))
        title = title[: vm.start()].strip()
    if not title:
        title = stem
    # "Title, The" / "Title, The - Subtitle" -> "The Title - Subtitle"
    m = re.match(r"^(.*?), (The|A|An)( - .*)?$", title, re.I)
    if m:
        title = f"{m.group(2)} {m.group(1)}{m.group(3) or ''}"
    out.title, out.region, out.disc, out.tags = title, region, disc, tags
    return out


_THUMB_BAD = str.maketrans({c: "_" for c in '&*/:`<>?\\|"'})


def libretro_thumb_name(stem: str) -> str:
    """libretro-thumbnails file names: the No-Intro name with a few characters replaced by '_'."""
    return stem.translate(_THUMB_BAD)
