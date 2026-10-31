"""User configuration: a JSON file in the data folder, with sensible defaults."""
from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def default_data_dir() -> Path:
    env = os.environ.get("GAMESHOP_DATA")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data"


@dataclass
class Source:
    path: str
    system: str          # a system id, or "mixed" to pick per item later
    label: str = ""


@dataclass
class Config:
    port: int = 8750
    bind: str = "0.0.0.0"
    name: str = ""                       # this agent's display name (defaults to the hostname)
    ps5_host: str = ""
    ra_root: str = "/data/homebrew/PPSA99169"
    destinations: dict[str, str] = field(default_factory=dict)   # system id -> PS5 folder override
    sources: list[Source] = field(default_factory=list)
    peers: list[str] = field(default_factory=list)                # host[:port] of other agents (Phase 2)
    art_enabled: bool = True
    skip_existing: bool = True
    tv_mode: bool = False

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Config":
        c = Config()
        for k, v in d.items():
            if k == "sources":
                c.sources = [Source(**s) if isinstance(s, dict) else s for s in v]
            elif hasattr(c, k):
                setattr(c, k, v)
        return c

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConfigStore:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / "config.json"
        self._lock = threading.Lock()
        self.config = self._load()

    def _load(self) -> Config:
        if self.path.exists():
            try:
                return Config.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass
        c = Config()
        c.name = os.environ.get("COMPUTERNAME") or socket.gethostname() or "gameshop"
        return c

    def save(self) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.config.to_dict(), indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def update(self, patch: dict[str, Any]) -> Config:
        with self._lock:
            merged = self.config.to_dict()
            for k, v in patch.items():
                if k in merged:
                    merged[k] = v
            self.config = Config.from_dict(merged)
        self.save()
        return self.config
