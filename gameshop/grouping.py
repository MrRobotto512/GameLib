"""Fold updates and DLC under the game they belong to, so the library shows games, not files.

A Switch dump of Mario Kart is three or four files (base, update, DLC); PS4 packages come as
game + patch + additional content. Each is its own item in the index (it is copied on its own),
but the store shows one product with the add-ons listed on it.
"""
from __future__ import annotations

import re
from typing import Any

_ADDON_TAGS = {"update": "update", "upd": "update", "patch": "update", "dlc": "dlc", "addon": "dlc", "add-on": "dlc"}
_VER_DOTTED = re.compile(r"v?(\d+(?:\.\d+)+)", re.I)
_VER_RAW = re.compile(r"v(\d{4,})", re.I)
_NORM = re.compile(r"[^a-z0-9]+")
_APOS = re.compile("[’'ʼ`]")


def _norm(s: str) -> str:
    return _NORM.sub(" ", _APOS.sub("", s.lower())).strip()     # "Luigi's" and "Luigis" are one name


def part_of(item: dict[str, Any]) -> str:
    """base | update | dlc — from the Switch title id, the PS4 package category, or name tags."""
    meta = item.get("meta") or {}
    c = str(meta.get("content") or meta.get("category") or "").lower()
    if c in ("update", "dlc"):
        return c
    if c in ("base", "game"):
        return "base"
    for t in item.get("tags") or []:
        k = _ADDON_TAGS.get(str(t).lower().strip())
        if k:
            return k
    return "base"


def group_key(item: dict[str, Any]) -> str:
    """What ties a game and its add-ons together: the Switch base title id, the PS4 title id
    (game, patch and DLC share it), else the sortable title."""
    meta = item.get("meta") or {}
    key = meta.get("base_id") or (meta.get("title_id") if meta.get("content_id") else None)
    return f"{item.get('system', '')}:{key or item.get('sort_title') or str(item.get('title', '')).lower()}"


def version_label(item: dict[str, Any]) -> str:
    """'v1.0.1' from the package or the name; Switch raw versions (v65536) become v1, v2, ..."""
    meta = item.get("meta") or {}
    if meta.get("app_version"):
        return "v" + str(meta["app_version"]).lstrip("vV")
    raw = ""
    for t in item.get("tags") or []:
        t = str(t)
        m = _VER_DOTTED.fullmatch(t)
        if m:
            return "v" + m.group(1)
        m = _VER_RAW.fullmatch(t)
        if m and not raw:
            n = int(m.group(1))
            raw = f"v{n // 65536}" if n % 65536 == 0 else t
    return raw


def strip_game(title: str, game: str) -> str:
    """'Game - Season Pass' / 'Game(TM): Season Pass' -> 'Season Pass'; the game's own name -> ''.
    Punctuation and trademark signs do not count, so 'LEGO(R) Brawls' still matches 'LEGO Brawls'."""
    g = _norm(game)
    if not g:
        return title
    for _ in range(3):                                    # "Game - Game Season Pass" names the game twice
        if _norm(title) == g:
            return ""
        cut = ""
        for i in range(1, len(title) + 1):
            if _norm(title[:i]) == g:
                rest = title[i:].lstrip(" -:–—·꞉")
                if rest and _norm(rest):
                    cut = rest
                break
        if not cut:
            return title
        title = cut
    return title


def _slim(item: dict[str, Any], part: str, base: dict[str, Any]) -> dict[str, Any]:
    title = strip_game(str(item.get("title", "")), str(base.get("title", "")))
    return {"id": item["id"], "title": title, "part": part, "size": item.get("size", 0),
            "version": version_label(item), "rel": item.get("rel", ""), "peer": item.get("peer", "")}


def fold(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach every update / DLC to its game as `extras` and drop it from the top level. Add-ons
    whose game is not in the list stay, marked with `part`. Items are modified in place."""
    bases: dict[str, dict[str, Any]] = {}
    for it in items:
        if part_of(it) == "base":
            bases.setdefault(group_key(it), it)
    # add-ons without their game: the update (else the first add-on) stands in for it, so an
    # update + two DLC of a game that is not here still make one tile, not three
    heads: dict[str, dict[str, Any]] = {}
    for it in items:
        part = part_of(it)
        if part == "base" or group_key(it) in bases:
            continue
        key = group_key(it)
        if key not in heads or (part == "update" and part_of(heads[key]) != "update"):
            heads[key] = it
    out: list[dict[str, Any]] = []
    for it in items:
        part = part_of(it)
        if part != "base":
            base = bases.get(group_key(it)) or heads.get(group_key(it))
            if base is not None and base is not it:
                base.setdefault("extras", []).append(_slim(it, part, base))
                continue
        it["part"] = part
        out.append(it)
    for it in out:
        if it.get("extras"):
            it["extras"].sort(key=lambda x: (x["part"] != "update", x["title"].lower(), x["version"]))
    return out
