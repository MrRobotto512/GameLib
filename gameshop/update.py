"""Self-update for the Windows build: ask GitHub for the latest release, download its
GameShopSetup.exe and run it, then exit; the setup installs over the copy in Program Files and
starts the new one. A release without a setup falls back to the old way: download GameShop.exe
next to the running exe and hand over to a tiny cmd script that waits for us to exit, swaps the
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
SETUP_NAME = "GameShopSetup.exe"
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
    setup_url: str = ""          # browser_download_url of GameShopSetup.exe, "" when the release has none
    setup_size: int = 0

    @property
    def newer(self) -> bool:
        return parse_version(self.version) > parse_version(__version__)


def release_from_api(data: dict) -> Release | None:
    """Pick the exe and setup assets out of a GitHub release record."""
    if not data or data.get("draft") or data.get("prerelease"):
        return None
    assets = {a.get("name"): a for a in data.get("assets", [])}
    exe, setup = assets.get(ASSET_NAME) or {}, assets.get(SETUP_NAME) or {}
    tag = data.get("tag_name", "")
    return Release(version=".".join(map(str, parse_version(tag))), tag=tag, url=data.get("html_url", ""),
                   asset_url=exe.get("browser_download_url", ""), asset_size=int(exe.get("size") or 0),
                   notes=data.get("body") or "",
                   setup_url=setup.get("browser_download_url", ""), setup_size=int(setup.get("size") or 0))


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


def uses_setup(rel: Release) -> bool:
    """Update by running the release's setup: always, once a release has one. That is also how a
    copy started from Downloads moves into Program Files."""
    return bool(rel.setup_url)


def download_size(rel: Release) -> int:
    return rel.setup_size if uses_setup(rel) else rel.asset_size


def download(rel: Release, progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the release's setup to the temp folder, or else its exe next to the current one as
    GameShop.exe.new; returns its path."""
    if uses_setup(rel):
        url, target = rel.setup_url, Path(tempfile.gettempdir()) / f"GameShopSetup-{rel.version}.exe"
    elif rel.asset_url:
        url, target = rel.asset_url, Path(sys.executable).with_suffix(".exe.new")
    else:
        raise RuntimeError("This release has no GameShop.exe attached.")
    req = urllib.request.Request(url, headers={"User-Agent": f"GameShop/{__version__}"})
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
    log = '"%TEMP%\\gameshop-update.log"'
    return (
        "@echo off\r\n"
        f"rem Game Shop update: waiting for process {pid} to exit\r\n"
        f"echo %DATE% %TIME% waiting for process {pid}, then moving \"{new_exe}\" over \"{exe}\" >> {log}\r\n"
        "set TRIES=0\r\n"
        ":move\r\n"
        "ping -n 2 127.0.0.1 >NUL\r\n"
        f'move /y "{new_exe}" "{exe}" >NUL 2>&1\r\n'
        "if not errorlevel 1 goto start\r\n"
        "set /a TRIES+=1\r\n"
        "if %TRIES% GEQ 120 goto giveup\r\n"
        "goto move\r\n"
        ":start\r\n"
        f"echo %DATE% %TIME% moved after %TRIES% tries, starting {quoted} >> {log}\r\n"
        f'start "" /d "{exe.parent}" "{exe}" {quoted}\r\n'
        "goto done\r\n"
        ":giveup\r\n"
        f"echo %DATE% %TIME% gave up: the old exe never let go >> {log}\r\n"
        ":done\r\n"
        'del "%~f0"\r\n')


def child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Our environment minus what PyInstaller's bootloader put in it. A one-file exe started with
    those still set takes itself for a child of *this* exe and looks for Python in our unpacked
    temp folder, which is gone by then: "Failed to load Python DLL" instead of the new version."""
    env = dict(os.environ if env is None else env)
    for k in [k for k in env if k.startswith("_PYI_") or k.startswith("_MEIPASS")]:
        del env[k]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def run_detached(cmd: list[str], cwd: Path) -> None:
    """Start `cmd` with a hidden console of its own, so it outlives us and nothing it runs
    pops up a window."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=str(cwd), env=child_env(),  # noqa: S603
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


SETUP_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


def run_setup_and_exit(setup: Path) -> None:
    """Start the downloaded setup and exit. Windows asks for administrator rights; the setup
    then closes any Game Shop still running, installs, and starts the new version."""
    print(f"[update] starting {setup}; exiting so it can replace this copy", flush=True)
    run_detached([str(setup), *SETUP_ARGS], cwd=setup.parent)
    threading.Timer(0.5, lambda: os._exit(0)).start()


def apply_and_restart(new_exe: Path, extra_args: list[str] | None = None) -> None:
    """Spawn the swap script and exit this process. The script waits for our PID to go away,
    moves the new exe over the old one and starts it with the same arguments."""
    exe = Path(sys.executable)
    script = Path(tempfile.gettempdir()) / "gameshop-update.cmd"
    script.write_text(swap_script(exe, new_exe, os.getpid(), extra_args if extra_args is not None else sys.argv[1:]), encoding="ascii")
    print(f"[update] swap script {script} written; exiting so it can replace {exe}", flush=True)
    run_detached(["cmd.exe", "/c", str(script)], cwd=exe.parent)
    threading.Timer(0.5, lambda: os._exit(0)).start()
