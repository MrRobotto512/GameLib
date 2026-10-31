"""Other Game Shop agents on the network: found with mDNS or listed by name (LAN or Tailscale).

Each peer's library is polled now and then and kept in memory; its files are always served by
that peer, and this agent streams them through itself (the gateway) when the PS5 cannot reach the
peer directly, which is the normal case for a PC behind Tailscale.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

SERVICE = "_gameshop._tcp.local."
POLL_SECONDS = 60
PEER_TIMEOUT = 6


@dataclass
class Peer:
    key: str                     # host:port, what the UI and item ids use (safe: no '~')
    url: str                     # http://host:port
    name: str = ""
    agent_id: str = ""
    source: str = "static"       # static | mdns
    online: bool = False
    last_seen: float = 0.0
    error: str = ""
    count: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"key": self.key, "url": self.url, "name": self.name or self.key, "source": self.source,
                "online": self.online, "last_seen": self.last_seen, "error": self.error, "count": self.count}


def _get_json(url: str, timeout: float = PEER_TIMEOUT) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "GameShop-peer"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — LAN/tailnet address from settings
        return json.loads(r.read().decode("utf-8"))


def peer_key(host: str, port: int) -> str:
    host = host.strip().strip("[]")
    return f"{host}:{port}"


class PeerDirectory:
    def __init__(self, my_id: str, my_name: str, my_port: Callable[[], int], static: Callable[[], list[str]],
                 discovery: Callable[[], bool], default_port: int = 8750, my_ip: Callable[[], str] = lambda: ""):
        self.my_id, self.my_name, self.my_port, self.my_ip = my_id, my_name, my_port, my_ip
        self.static, self.discovery, self.default_port = static, discovery, default_port
        self.peers: dict[str, Peer] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._zc = None
        self._browser = None
        self._info = None

    # ---- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._loop, name="gameshop-peers", daemon=True).start()
        if self.discovery():
            threading.Thread(target=self._start_mdns, name="gameshop-mdns", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            if self._zc:
                if self._info:
                    self._zc.unregister_service(self._info)
                self._zc.close()
        except Exception:         # noqa: BLE001 — shutting down
            pass

    def refresh(self) -> None:
        self._wake.set()

    # ---- mDNS -------------------------------------------------------------------
    def _start_mdns(self) -> None:
        try:
            from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
        except ImportError:
            return
        try:
            self._zc = Zeroconf()
            host = socket.gethostname()
            addrs = []
            try:
                for fam, _t, _p, _c, sa in socket.getaddrinfo(host, None, socket.AF_INET):
                    if not sa[0].startswith("127."):
                        addrs.append(socket.inet_aton(sa[0]))
            except OSError:
                pass
            if not addrs:
                addrs = [socket.inet_aton("127.0.0.1")]
            self._info = ServiceInfo(
                SERVICE, f"{self.my_name}-{self.my_id}.{SERVICE}", addresses=addrs, port=self.my_port(),
                properties={"id": self.my_id, "name": self.my_name}, server=f"{host}.local.",
            )
            self._zc.register_service(self._info)
            directory = self

            class Listener:
                def add_service(self, zc, type_, name):
                    directory._mdns_seen(zc, type_, name)

                def update_service(self, zc, type_, name):
                    directory._mdns_seen(zc, type_, name)

                def remove_service(self, zc, type_, name):
                    pass

            self._browser = ServiceBrowser(self._zc, SERVICE, Listener())
        except Exception as e:    # noqa: BLE001 — discovery is optional
            print(f"mDNS unavailable: {e}")

    def _mdns_seen(self, zc, type_, name) -> None:
        try:
            info = zc.get_service_info(type_, name, timeout=2000)
        except Exception:         # noqa: BLE001
            info = None
        if not info:
            return
        props = {k.decode() if isinstance(k, bytes) else k: (v.decode() if isinstance(v, bytes) else v) for k, v in (info.properties or {}).items()}
        if props.get("id") == self.my_id:
            return
        addrs = [a for a in (info.parsed_addresses() if hasattr(info, "parsed_addresses") else []) if ":" not in a]
        if not addrs:
            return
        # prefer the address on the same network as this PC's LAN interface (not a VPN or Tailscale one)
        mine = self.my_ip() or ""
        prefix = ".".join(mine.split(".")[:2]) + "."
        addrs.sort(key=lambda a: 0 if mine and a.startswith(prefix) else 1)
        key = peer_key(addrs[0], info.port)
        with self._lock:
            p = self.peers.get(key) or Peer(key=key, url=f"http://{addrs[0]}:{info.port}", source="mdns")
            p.name = props.get("name", p.name)
            p.agent_id = props.get("id", p.agent_id)
            self.peers[key] = p
        self._wake.set()

    # ---- polling --------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sync_static()
            for key in list(self.peers):
                if self._stop.is_set():
                    break
                self._poll(key)
            self._wake.wait(timeout=POLL_SECONDS)
            self._wake.clear()

    def _sync_static(self) -> None:
        wanted = {}
        for entry in self.static():
            entry = entry.strip()
            if not entry:
                continue
            if entry.startswith("http://") or entry.startswith("https://"):
                hostport = entry.split("//", 1)[1].rstrip("/")
                url = entry.rstrip("/")
            else:
                hostport = entry
                url = f"http://{entry}"
            host, _, port = hostport.rpartition(":")
            if not host or not port.isdigit():
                host, port = hostport, str(self.default_port)
                url = f"http://{host}:{port}"
            wanted[peer_key(host, int(port))] = url
        with self._lock:
            for key, url in wanted.items():
                if key not in self.peers:
                    self.peers[key] = Peer(key=key, url=url, source="static")
            for key in list(self.peers):
                p = self.peers[key]
                if p.source == "static" and key not in wanted:
                    del self.peers[key]

    def _poll(self, key: str) -> None:
        p = self.peers.get(key)
        if not p:
            return
        try:
            info = _get_json(p.url + "/api/peer/info")
            if info.get("agent_id") == self.my_id:
                with self._lock:
                    self.peers.pop(key, None)
                return
            items = _get_json(p.url + "/api/peer/items", timeout=30)
            with self._lock:
                p.name = info.get("name") or p.name
                p.agent_id = info.get("agent_id", "")
                p.items = items
                p.count = len(items)
                p.online, p.error, p.last_seen = True, "", time.time()
        except (urllib.error.URLError, OSError, ValueError) as e:
            with self._lock:
                p.online = False
                p.error = str(getattr(e, "reason", e))[:200]

    # ---- queries ---------------------------------------------------------------
    def online(self) -> list[Peer]:
        with self._lock:
            return [p for p in self.peers.values() if p.online]

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [p.public() for p in self.peers.values()]

    def get(self, key: str) -> Peer | None:
        with self._lock:
            return self.peers.get(key)

    def remote_items(self) -> list[tuple[Peer, dict[str, Any]]]:
        out = []
        with self._lock:
            for p in self.peers.values():
                if p.online:
                    for it in p.items:
                        out.append((p, it))
        return out
