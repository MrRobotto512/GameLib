"""More cover sources, tried after libretro-thumbnails misses.

- PS1 / PS2: xlenore's cover repositories on GitHub (what DuckStation and PCSX2 download from),
  keyed by the disc serial (SLUS-20312) that Redump names and most folders carry.
- PS4 / PS5: no public retail-cover source exists, so the package icon from orbispatches.com /
  prosperopatches.com by title id (CUSA…, PPSA…). It is a square icon; the store lays it out as
  an insert under the system strip.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any

SERIAL_REPOS = {"psx": "psx-covers", "ps2": "ps2-covers"}
SERIAL_URL = "https://raw.githubusercontent.com/xlenore/{repo}/main/covers/default/{serial}.jpg"
PATCH_SITES = {"ps4": "orbispatches.com", "ps5": "prosperopatches.com"}
_SERIAL = re.compile(r"\b(S[CL][EJKPU][SMDT])[-_ ]?(\d{3})[._]?(\d{2})\b", re.I)
_TITLE_ID = re.compile(r"\b((?:CUSA|PPSA|PCJS|PCAS|PCKS|PLJM|PLAS|PLKS)\d{5})\b")
_ICON = re.compile(r"https://cdn\.(?:orbis|prospero)patches\.com/titles/[A-Z0-9_]+[0-9a-f]*/icon0\.webp")


def disc_serial(item: dict[str, Any]) -> str | None:
    """'SLUS-20312' from the dump's name or folder, in the form the repositories use."""
    meta = item.get("meta") or {}
    m = _SERIAL.search(f"{meta.get('stem', '')} {item.get('rel', '')}")
    return f"{m.group(1).upper()}-{m.group(2)}{m.group(3)}" if m else None


def serial_cover_urls(item: dict[str, Any], kind: str = "box") -> list[str] | None:
    repo = SERIAL_REPOS.get(item.get("system", ""))
    if kind != "box" or not repo:
        return None
    serial = disc_serial(item)
    return [SERIAL_URL.format(repo=repo, serial=serial)] if serial else None


def title_id(item: dict[str, Any]) -> str | None:
    meta = item.get("meta") or {}
    tid = str(meta.get("title_id") or "")
    if _TITLE_ID.fullmatch(tid):
        return tid
    m = _TITLE_ID.search(f"{meta.get('stem', '')} {item.get('rel', '')}".upper())
    return m.group(1) if m else None


def patch_site_icon_urls(item: dict[str, Any], kind: str = "box", fetch=None) -> list[str] | None:
    """The icon0 of a PS4 / PS5 title from the patch sites' page for it (the CDN path has a hash,
    so the page has to be read first)."""
    site = PATCH_SITES.get(item.get("system", ""))
    if kind != "box" or not site:
        return None
    tid = title_id(item)
    if not tid:
        return None
    html = (fetch or _fetch)(f"https://{site}/{tid}")
    if not html:
        return None
    m = _ICON.search(html)
    return [m.group(0)] if m else None


def _fetch(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 GameShop/0.1"})
        with urllib.request.urlopen(req, timeout=15) as r:   # noqa: S310
            return r.read(400_000).decode("utf-8", "replace") if r.status == 200 else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
