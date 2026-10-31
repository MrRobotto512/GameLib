"""A Windows tray icon for the agent: open the store, rescan, load the payload chain, quit."""
from __future__ import annotations

import threading
import webbrowser

from .server import Agent


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=16, fill=(245, 196, 81, 255))
    d.polygon([(32, 14), (50, 32), (32, 50), (14, 32)], fill=(26, 22, 48, 255))
    return img


def run_with_tray(agent: Agent, app, open_browser: bool = False) -> None:
    import pystray
    import uvicorn

    url = f"http://127.0.0.1:{agent.config.port}/"
    config = uvicorn.Config(app, host=agent.config.bind, port=agent.config.port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="gameshop-http", daemon=True).start()
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

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
        pystray.MenuItem("Quit", quit_),
    )
    icon = pystray.Icon("gameshop", _icon_image(), f"Game Shop — {agent.config.name}", menu)
    icon.run()
