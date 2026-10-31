"""Where things live, for a normal checkout and for the one-file PyInstaller build."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """The folder holding systems.toml and web/ (the bundle's temp dir when frozen)."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "gameshop"
    return Path(__file__).resolve().parent


def default_data_dir() -> Path:
    env = os.environ.get("GAMESHOP_DATA")
    if env:
        return Path(env)
    if frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "GameShop"
    return Path(__file__).resolve().parent.parent / "data"
