"""One Game Shop per port.

A second copy used to start, fail to bind the port, and, in tray mode, sit there with a tray
icon that served nothing: that is what a freshly downloaded exe double-clicked next to the
running one looked like, and what the self-update's restart looked like when it raced the
old copy's exit. Now a starting copy claims the port first: if another Game Shop answers on
it, that one is asked to quit (over loopback only) and the port is waited for; anything else
on the port is reported and this copy exits."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Claim:
    ok: bool
    message: str = ""            # what to tell the user when not ok
    replaced: str = ""           # the version of the Game Shop that was asked to quit


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def other_gameshop(port: int, timeout: float = 3.0) -> dict | None:
    """The /api/state of whatever Game Shop answers on 127.0.0.1:port, else None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=timeout) as r:   # noqa: S310
            d = json.load(r)
        return d if isinstance(d, dict) and "version" in d and "systems" in d else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def ask_to_quit(port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", method="POST", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:   # noqa: S310
            return r.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def claim_port(host: str, port: int, wait: float = 25.0, log=print) -> Claim:
    """Make the port ours: free already, or freed by the Game Shop that held it."""
    if port_free(host, port):
        return Claim(True)
    other = other_gameshop(port)
    replaced = ""
    if other:
        replaced = str(other.get("version", "?"))
        log(f"Game Shop v{replaced} is already on port {port}: asking it to quit")
        ask_to_quit(port)
    else:
        log(f"port {port} is busy; waiting for it")
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        if port_free(host, port):
            return Claim(True, replaced=replaced)
    if other:
        return Claim(False, f"Game Shop v{replaced} is already running on port {port} and did not quit.\n"
                            f"Quit it from its tray icon, then start this one again.", replaced)
    return Claim(False, f"Port {port} is in use by another program. Change the port in config.json "
                        f"or start with --port.")
