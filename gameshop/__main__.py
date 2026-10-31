"""python -m gameshop  — run the agent (console), or with --tray as a tray icon."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="gameshop", description="Game Shop agent")
    ap.add_argument("--data", help="data folder (config, index, art cache)")
    ap.add_argument("--port", type=int, help="HTTP port (default from config, 8750)")
    ap.add_argument("--bind", help="interface to bind (default 0.0.0.0)")
    ap.add_argument("--open", action="store_true", help="open the store in a browser")
    ap.add_argument("--tray", action="store_true", help="run as a tray icon (Windows)")
    args = ap.parse_args()
    if args.data:
        os.environ["GAMESHOP_DATA"] = str(Path(args.data).resolve())
    from .server import Agent, create_app      # after GAMESHOP_DATA is set
    agent = Agent()
    if args.port:
        agent.store.config.port = args.port
    if args.bind:
        agent.store.config.bind = args.bind
    app = create_app(agent)
    url = f"http://127.0.0.1:{agent.config.port}/"
    print(f"Game Shop {agent.config.name}: {url}  (PS5 opens http://{agent.local_ip_for(agent.config.ps5_host)}:{agent.config.port}/)")
    if args.tray:
        from .tray import run_with_tray
        run_with_tray(agent, app, open_browser=args.open)
        return
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=agent.config.bind, port=agent.config.port, log_level="warning")


if __name__ == "__main__":
    main()
