"""A Windows tray icon for the agent: open the store, rescan, load the payload chain, update, quit."""
from __future__ import annotations

import ctypes
import threading
import webbrowser

from . import __version__, update
from .server import Agent


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=16, fill=(245, 196, 81, 255))
    d.polygon([(32, 14), (50, 32), (32, 50), (14, 32)], fill=(26, 22, 48, 255))
    return img


def _ask(text: str, title: str = "Game Shop") -> bool:
    """Yes/No message box (the tray app has no window of its own)."""
    try:
        return ctypes.windll.user32.MessageBoxW(None, text, title, 0x04 | 0x40 | 0x40000) == 6   # MB_YESNO | MB_ICONINFORMATION | MB_TOPMOST
    except Exception:   # noqa: BLE001 — not Windows, or no desktop
        return False


def _say(text: str, title: str = "Game Shop") -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 | 0x40000 | 0x10000)   # MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
    except Exception:   # noqa: BLE001
        pass


say = _say


def _log(text: str) -> None:
    """A line in gameshop.log (stdout is redirected there in the windowed build)."""
    print(f"[update] {text}", flush=True)


class Updater:
    """Tray-side state for self-update: last check result and a one-line status for the menu."""

    def __init__(self, icon_ref):
        self.icon_ref = icon_ref          # lambda: pystray.Icon
        self.latest: update.Release | None = None
        self.status = ""
        self.busy = False

    def label(self, _item=None) -> str:
        if self.busy and self.status:
            return self.status
        if self.latest and self.latest.newer:
            return f"Update to v{self.latest.version}…"
        return "Check for updates"

    def check_quietly(self) -> None:
        """At start-up: look once, and only speak up when there is something newer."""
        rel = update.check()
        self.latest = rel
        if rel and rel.newer:
            self._notify(f"Game Shop v{rel.version} is available. Use the tray menu to update.")
            self._refresh()

    def clicked(self, _icon=None, _item=None) -> None:
        if self.busy:
            return
        threading.Thread(target=self._run, name="gameshop-update", daemon=True).start()

    def _run(self) -> None:
        self.busy, self.status = True, "Checking for updates…"
        self._refresh()
        _log("checking GitHub")
        try:
            rel = update.check()
            self.latest = rel
            if rel is None:
                _log("GitHub not reachable")
                _say("Could not reach GitHub to check for updates.")
                return
            _log(f"latest is v{rel.version}, this is v{__version__}")
            if not rel.newer:
                _say(f"You have the latest version (v{__version__}).")
                return
            notes = rel.notes.strip().splitlines()
            summary = "\n".join(line for line in notes[:8])
            if not update.can_self_update():
                _say(f"Game Shop v{rel.version} is available (you have v{__version__}).\n\n"
                     f"You are running from source: update with  git pull  and restart.\n\n{rel.url}")
                return
            if not rel.asset_url:
                _say(f"Game Shop v{rel.version} is available but has no GameShop.exe attached yet.\n{rel.url}")
                return
            size_mb = rel.asset_size / 1e6
            if not _ask(f"Game Shop v{rel.version} is available (you have v{__version__}).\n\n{summary}\n\n"
                        f"Download {size_mb:.0f} MB and restart now?"):
                return
            self.status = "Downloading update… 0%"
            self._refresh()
            self._notify(f"Downloading Game Shop v{rel.version} ({size_mb:.0f} MB)…")
            _log(f"downloading {rel.asset_url}")

            def prog(done, total):
                pct = int(done * 100 / total) if total else 0
                self.status = f"Downloading update… {pct}%"
                self._refresh()
            new_exe = update.download(rel, prog)
            _log(f"downloaded {new_exe} ({new_exe.stat().st_size} bytes); swapping and restarting")
            self.status = "Restarting…"
            self._refresh()
            self._notify(f"Installing v{rel.version}. Game Shop closes and comes back in a few seconds.")
            update.apply_and_restart(new_exe)
        except Exception as exc:   # noqa: BLE001
            _log(f"failed: {exc!r}")
            _say(f"Update failed: {exc}")
        finally:
            self.busy, self.status = False, ""
            self._refresh()

    def _refresh(self) -> None:
        icon = self.icon_ref()
        if icon is not None:
            try:
                icon.update_menu()
            except Exception:   # noqa: BLE001
                pass

    def _notify(self, text: str) -> None:
        icon = self.icon_ref()
        if icon is not None:
            try:
                icon.notify(text, "Game Shop")
            except Exception:   # noqa: BLE001
                pass


def run_with_tray(agent: Agent, app, open_browser: bool = False) -> None:
    import pystray
    import uvicorn

    url = f"http://127.0.0.1:{agent.config.port}/"
    config = uvicorn.Config(app, host=agent.config.bind, port=agent.config.port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="gameshop-http", daemon=True).start()

    def check_started():
        """A copy whose server never came up must not sit in the tray looking alive."""
        if not server.started:
            print(f"the server did not start on {agent.config.bind}:{agent.config.port}; quitting", flush=True)
            _say(f"Game Shop could not listen on port {agent.config.port}. See gameshop.log in the data folder.")
            try:
                holder["icon"].stop()
            except Exception:   # noqa: BLE001
                pass
            import os
            os._exit(1)
    threading.Timer(15.0, check_started).start()
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    holder: dict = {}
    updater = Updater(lambda: holder.get("icon"))

    def ps5_line(_item=None):
        h = agent.health()
        return f"PS5 {agent.config.ps5_host or 'not set'}: {'online' if h.online else 'offline'}"

    def quit_(icon, _item):
        server.should_exit = True
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Game Shop", lambda i, _: webbrowser.open(url), default=True),
        pystray.MenuItem(lambda i: f"On the PS5: http://{agent.local_ip_for(agent.config.ps5_host)}:{agent.config.port}/", None, enabled=False),
        pystray.MenuItem(ps5_line, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Rescan folders", lambda i, _: threading.Thread(target=agent.rescan, daemon=True).start()),
        pystray.MenuItem("Load payload chain", lambda i, _: agent.enqueue_chain(agent.config.payloads) if agent.config.payloads else None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(updater.label, updater.clicked),
        pystray.MenuItem(f"Game Shop v{__version__}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_),
    )
    icon = pystray.Icon("gameshop", _icon_image(), f"Game Shop — {agent.config.name}", menu)
    holder["icon"] = icon
    agent.quit_hooks.append(lambda: (setattr(server, "should_exit", True), icon.stop()))
    threading.Timer(20.0, updater.check_quietly).start()
    icon.run()
