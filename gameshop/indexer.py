"""Walk the source folders and turn files into library items, grouping multi-file games."""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

from .naming import parse_name
from .pkg import read_pkg
from .systems import System, SystemsTable

_CUE_FILE = re.compile(r'^\s*FILE\s+"?([^"\r\n]+?)"?\s+\w+\s*$', re.I | re.M)
_GDI_LINE = re.compile(r'^\s*\d+\s+\d+\s+\d+\s+\d+\s+"?([^"\r\n]+?)"?\s+\d+\s*$', re.M)
_SKIP_DIRS = {".git", "$recycle.bin", "system volume information", "node_modules", "__macosx"}
_SKIP_EXTS = {"txt", "nfo", "sfv", "md5", "sha1", "jpg", "jpeg", "png", "gif", "webp", "xml", "dat", "ini", "db", "url", "htm", "html", "pdf"}
_CONTAINER_EXTS = {"cue", "m3u", "gdi", "ccd"}
_TRACK_EXTS = {"bin", "img", "iso", "wav", "raw", "sub", "ccd", "mds", "mdf", "ape", "flac"}


def item_id(source: str, rel: str) -> str:
    return hashlib.sha1(f"{source}\0{rel}".encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _walk(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if not f.startswith("."):
                yield Path(dirpath) / f


def _ext(p: Path) -> str:
    return p.suffix.lower().lstrip(".")


def _referenced(container: Path) -> list[Path]:
    """Files a .cue / .gdi / .m3u refers to (resolved next to it), recursively for m3u."""
    try:
        text = container.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ext = _ext(container)
    names: list[str] = []
    if ext == "cue":
        names = _CUE_FILE.findall(text)
    elif ext == "gdi":
        names = _GDI_LINE.findall(text)
    elif ext == "m3u":
        names = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    out: list[Path] = []
    for n in names:
        p = (container.parent / n.replace("\\", "/")).resolve()
        if p.exists() and p.is_file():
            out.append(p)
            if _ext(p) in _CONTAINER_EXTS and p != container:
                out.extend(_referenced(p))
    return out


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


class Indexer:
    def __init__(self, systems: SystemsTable):
        self.systems = systems

    # ---- public ---------------------------------------------------------------
    def scan_source(self, source_path: str, system_id: str) -> list[dict[str, Any]]:
        root = Path(source_path)
        if not root.is_dir():
            return []
        system = self.systems.get(system_id)
        if system_id == "mixed" or system is None:
            return self._scan_mixed(root)
        if system.group == "folder":
            return self._scan_folders(root, system)
        return self._scan_files(root, system)

    # ---- strategies -------------------------------------------------------------
    def _scan_files(self, root: Path, system: System) -> list[dict[str, Any]]:
        exts = set(system.exts)
        files = [p for p in _walk(root) if _ext(p) in exts or (system.group == "cue" and _ext(p) in _TRACK_EXTS)]
        consumed: set[Path] = set()
        items: list[dict[str, Any]] = []
        if system.group == "cue":
            # containers first: m3u (multi-disc) over cue/gdi over loose tracks
            order = {"m3u": 0, "cue": 1, "gdi": 1, "ccd": 1}
            for p in sorted(files, key=lambda q: order.get(_ext(q), 2)):
                if p in consumed or _ext(p) not in _CONTAINER_EXTS:
                    continue
                refs = _referenced(p)
                group = [p] + [r for r in refs if r not in consumed]
                consumed.update(group)
                items.append(self._make(root, system, p, group))
            for p in files:
                if p in consumed:
                    continue
                e = _ext(p)
                if e in _TRACK_EXTS and e not in exts:
                    continue                      # a stray .bin nobody references: not a game
                if e in _TRACK_EXTS and self._has_sibling_container(p):
                    continue                      # referenced by a cue we could not parse
                consumed.add(p)
                items.append(self._make(root, system, p, [p]))
        else:
            for p in files:
                items.append(self._make(root, system, p, [p]))
        return items

    def _scan_folders(self, root: Path, system: System) -> list[dict[str, Any]]:
        items = []
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                files = [p for p in _walk(child)]
                if files:
                    items.append(self._make(root, system, child, files, kind="folder"))
            elif child.is_file() and system.exts and _ext(child) in system.exts:
                items.append(self._make(root, system, child, [child]))
        return items

    def _scan_mixed(self, root: Path) -> list[dict[str, Any]]:
        """A 'mixed' source: guess the system per file from its extension, unambiguous ones only."""
        by_ext: dict[str, list[System]] = {}
        for s in self.systems.all():
            for e in s.exts:
                by_ext.setdefault(e, []).append(s)
        items = []
        for p in _walk(root):
            cands = by_ext.get(_ext(p), [])
            if len(cands) == 1:
                items.append(self._make(root, cands[0], p, [p]))
        return items

    # ---- helpers -------------------------------------------------------------------
    @staticmethod
    def _has_sibling_container(p: Path) -> bool:
        for e in ("cue", "gdi", "ccd"):
            if p.with_suffix("." + e).exists():
                return True
        return False

    def _make(self, root: Path, system: System, primary: Path, files: list[Path], kind: str = "rom") -> dict[str, Any]:
        rel = primary.relative_to(root).as_posix()
        parsed = parse_name(primary.stem if primary.is_file() else primary.name)
        meta: dict[str, Any] = {}
        ext = _ext(primary)
        if ext == "pkg":
            kind = "pkg"
            info = read_pkg(primary)
            meta.update(info.as_meta())
            if info.title:
                parsed.title = info.title
        elif ext in ("exfat", "ffpkg", "ffpfs", "ffpfsc"):
            kind = "image"
        if parsed.disc:
            meta["disc"] = parsed.disc
        if system.id == "switch":
            from .switchdb import classify, parse_title_id
            tid = parse_title_id(parsed.stem)
            if tid:
                content, base = classify(tid)
                meta.update(title_id=tid, base_id=base, content=content)
        file_rows = [[f.relative_to(root).as_posix(), _size(f)] for f in files]
        return {
            "id": item_id(str(root), rel),
            "system": system.id,
            "title": parsed.title,
            "sort_title": parsed.sort_title,
            "region": parsed.region,
            "tags": parsed.tags,
            "kind": kind,
            "rel": rel,
            "files": file_rows,
            "size": sum(s for _, s in file_rows),
            "meta": {**meta, "stem": parsed.stem, "ext": ext},
        }


def scan_all(indexer: Indexer, db, sources) -> dict[str, int]:
    """Scan every configured source into the database. Returns {source path: item count}."""
    counts = {}
    for src in sources:
        t = time.time()
        items = indexer.scan_source(src.path, src.system)
        db.upsert_items(items, src.path)
        counts[src.path] = len(items)
    return counts
