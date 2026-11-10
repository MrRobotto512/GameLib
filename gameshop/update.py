"""Self-update for the one-file build: ask GitHub for the latest release, download GameShop.exe
next to the running exe, then hand over to a tiny cmd script that waits for us to exit, swaps the
files and starts the new one. From source there is nothing to swap; `git pull` is the update."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .paths import frozen

REPO = "MrRobotto512/GameLib"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "GameShop.exe"
_VER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int]:
    m = _VER.search(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


@dataclass
class Release:
    version: str                 # "0.1.1"
    tag: str                     # "v0.1.1"
    url: str                     # html page
    asset_url: str               # browser_download_url of GameShop.exe, "" when the release has none
    asset_size: int
    notes: str

    @property
    def newer(self) -> bool:
        return parse_version(self.version) > parse_version(__version__)


def release_from_api(data: dict) -> Release | None:
    """Pick the exe asset out of a GitHub release record."""
    if not data or data.get("draft") or data.get("prerelease"):
        return None
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
    tag = data.get("tag_name", "")
    return Release(version=".".join(map(str, parse_version(tag))), tag=tag, url=data.get("html_url", ""),
                   asset_url=(asset or {}).get("browser_download_url", ""), asset_size=int((asset or {}).get("size") or 0),
                   notes=data.get("body") or "")


def check(timeout: float = 15.0) -> Release | None:
    """The latest published release, or None when GitHub cannot be reached."""
    try:
        req = urllib.request.Request(LATEST_URL, headers={"User-Agent": f"GameShop/{__version__}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
            return release_from_api(json.load(r))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def can_self_update() -> bool:
    return frozen() and sys.platform == "win32"


def download(rel: Release, progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the new exe next to the current one as GameShop.exe.new; returns its path."""
    if not rel.asset_url:
        raise RuntimeError("This release has no GameShop.exe attached.")
    target = Path(sys.executable).with_suffix(".exe.new")
    req = urllib.request.Request(rel.asset_url, headers={"User-Agent": f"GameShop/{__version__}"})
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(target, "wb") as f:   # noqa: S310
        total = int(r.headers.get("Content-Length") or rel.asset_size or 0)
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if done < 1_000_000 or target.read_bytes()[:2] != b"MZ":
        target.unlink(missing_ok=True)
        raise RuntimeError("The downloaded file is not a Windows program.")
    return target


def swap_script(exe: Path, new_exe: Path, pid: int, args: list[str]) -> str:
    """The cmd script that moves new_exe over exe once we have exited, then starts exe again.

    Windows will not let the running exe be overwritten, so the move itself is the wait: it
    fails until this process and the PyInstaller bootloader that started it are gone. Only cmd
    built-ins and `ping` (as a one-second sleep) are used: `timeout` refuses to run without a
    console and `tasklist | find` opened a console window per try. `pid` is kept for the log."""
    quoted = " ".join(f'"{a}"' for a in args)
    return (
        "@echo off\r\n"
        f"rem Game Shop update: waiting for process {pid} to exit\r\n"
        "set TRIES=0\r\n"
        ":move\r\n"
        "ping -n 2 127.0.0.1 >NUL\r\n"
        f'move /y "{new_exe}" "{exe}" >NUL 2>&1\r\n'
        "if not errorlevel 1 goto start\r\n"
        "set /a TRIES+=1\r\n"
        "if %TRIES% GEQ 120 goto done\r\n"
        "goto move\r\n"
        ":start\r\n"
        f'start "" /d "{exe.parent}" "{exe}" {quoted}\r\n'
        ":done\r\n"
        'del "%~f0"\r\n')


def run_detached(cmd: list[str], cwd: Path) -> None:
    """Start `cmd` with a hidden console of its own, so it outlives us and nothing it runs
    pops up a window."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=str(cwd),  # noqa: S603
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_and_restart(new_exe: Path, extra_args: list[str] | None = None) -> None:
    """Spawn the swap script and exit this process. The script waits for our PID to go away,
    moves the new exe over the old one and starts it with the same arguments."""
    exe = Path(sys.executable)
    script = Path(tempfile.gettempdir()) / "gameshop-update.cmd"
    script.write_text(swap_script(exe, new_exe, os.getpid(), extra_args if extra_args is not None else sys.argv[1:]), encoding="ascii")
    run_detached(["cmd.exe", "/c", str(script)], cwd=exe.parent)
    threading.Timer(0.5, lambda: os._exit(0)).start()
