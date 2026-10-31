"""The systems table: which console is which, where its games go on the PS5, how they look."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent


@dataclass(frozen=True)
class System:
    id: str
    name: str
    maker: str
    route: str          # ftp | dpi | elf | none
    dest: str           # may contain {ra}
    exts: tuple[str, ...]
    group: str          # file | cue | folder
    case: str
    color: str
    thumbs: str = ""
    note: str = ""

    def resolve_dest(self, ra_root: str, overrides: dict[str, str] | None = None) -> str:
        """The PS5 folder this system's games go to, after user overrides."""
        if overrides and overrides.get(self.id):
            return overrides[self.id]
        return self.dest.replace("{ra}", ra_root.rstrip("/")) if self.dest else ""


@dataclass
class SystemsTable:
    systems: dict[str, System] = field(default_factory=dict)
    ra_default: str = "/data/homebrew/PPSA99169"

    def get(self, system_id: str) -> System | None:
        return self.systems.get(system_id)

    def all(self) -> list[System]:
        return list(self.systems.values())

    def by_maker(self) -> dict[str, list[System]]:
        out: dict[str, list[System]] = {}
        for s in self.systems.values():
            out.setdefault(s.maker, []).append(s)
        return out


def load_systems(path: Path | None = None) -> SystemsTable:
    data = tomllib.loads((path or HERE / "systems.toml").read_text(encoding="utf-8"))
    table = SystemsTable(ra_default=data.get("ra_default", "/data/homebrew/PPSA99169"))
    for raw in data.get("system", []):
        s = System(
            id=raw["id"], name=raw["name"], maker=raw.get("maker", ""), route=raw.get("route", "ftp"),
            dest=raw.get("dest", ""), exts=tuple(e.lower() for e in raw.get("exts", [])),
            group=raw.get("group", "file"), case=raw.get("case", "generic"),
            color=raw.get("color", "#888888"), thumbs=raw.get("thumbs", ""), note=raw.get("note", ""),
        )
        table.systems[s.id] = s
    return table
