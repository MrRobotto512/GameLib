"""Talking to the PS5: FTP (etaHEN's on 1337, or ftpsrv.elf on 2121), Direct Package Installer (12800 / 9090),
the ELF loader (9021) and RetroArch's WebUI (6769). Nothing here is ours on the console."""
from __future__ import annotations

import concurrent.futures
import ftplib
import json
import posixpath
import io
import socket
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .paths import resource_dir

FTP_PORT = 1337
FTP_PORTS = (FTP_PORT, 2121)          # etaHEN's built-in FTP, then john-tornblom's ftpsrv.elf; 0 in settings = try these
DPI2_PORT = 12800
DPI1_PORT = 9090
ELF_PORT = 9021
RA_PORT = 6769
ETAHEN_DIR = "/data/etaHEN"
# etaHEN reads config.ini; its multi-firmware experimental builds read a file of their own.
ETAHEN_CONFIGS = ("config.ini", "config-multifw-experimental.ini")
PARTIAL_SUFFIX = ".gameshop-partial"
PAYLOAD_DIR = resource_dir() / "payloads"                    # built from ps5/ with the ps5-payload-sdk
TILE_ID = "PPSA87500"                                         # the Game Shop tile app's title id on the PS5
TILE_APP = resource_dir() / "ps5app" / TILE_ID                # built from ps5/app/ with the app kit
URL_MARK = b"GAMESHOP_URL_PLACEHOLDER"                        # start of a 512-byte buffer in gameshop-open.elf
BLOCK = 1 << 20


@dataclass
class RemoteEntry:
    name: str
    is_dir: bool
    size: int = 0


@dataclass
class Health:
    host: str
    ftp: bool = False
    ftp_port: int = 0                  # the port FTP answered on
    dpi2: bool = False
    dpi1: bool = False
    elfldr: bool = False
    retroarch: bool = False
    dpi_setting: str = ""              # "on" / "off" in etaHEN's config on the PS5, "" when it could not be read
    etahen_config: str = ""            # the config file that was read
    checked: float = field(default_factory=time.time)
    hints: list[str] = field(default_factory=list)

    @property
    def online(self) -> bool:
        return self.ftp or self.dpi2 or self.dpi1 or self.elfldr


def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _tcp_open_many(host: str, ports, timeout: float = 1.5) -> dict[int, bool]:
    """Which of ports answer, asked all at once: a PS5 that is off (or a port its firewall drops)
    costs one timeout, not one for every port."""
    ports = list(dict.fromkeys(ports))
    if not ports:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ports)) as ex:
        return dict(zip(ports, ex.map(lambda p: _tcp_open(host, p, timeout), ports)))


def _http_get(url: str, timeout: float = 3.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310 (LAN address from settings)
        return r.read(65536).decode("utf-8", "replace")


class PS5Client:
    def __init__(self, host: str, ftp_port: int = 0):
        self.host = host
        self.ftp_port = ftp_port       # 0: whichever of FTP_PORTS answers

    def find_ftp_port(self, timeout: float = 1.5) -> int:
        """The FTP port to use: the configured one, else the first of FTP_PORTS that answers (0 if none)."""
        if self.ftp_port:
            return self.ftp_port if _tcp_open(self.host, self.ftp_port, timeout) else 0
        opened = _tcp_open_many(self.host, FTP_PORTS, timeout)
        return next((p for p in FTP_PORTS if opened[p]), 0)

    # ---- health ---------------------------------------------------------------
    def health(self) -> Health:
        h = Health(host=self.host)
        if not self.host:
            h.hints.append("Enter the PS5's address in Settings.")
            return h
        ftp_ports = [self.ftp_port] if self.ftp_port else list(FTP_PORTS)
        opened = _tcp_open_many(self.host, [*ftp_ports, ELF_PORT, DPI1_PORT, DPI2_PORT, RA_PORT])
        h.ftp_port = next((p for p in ftp_ports if opened[p]), 0)
        h.ftp = bool(h.ftp_port)
        h.elfldr = opened[ELF_PORT]
        h.dpi1 = opened[DPI1_PORT]
        if opened[DPI2_PORT]:
            try:
                h.dpi2 = "etaHEN" in _http_get(f"http://{self.host}:{DPI2_PORT}/")
            except OSError:
                h.dpi2 = False
        h.retroarch = opened[RA_PORT]
        if not h.online:
            h.hints.append("Nothing answered. Is the PS5 on, jailbroken, and etaHEN loaded?")
        else:
            if not h.ftp:
                h.hints.append(f"FTP ({self.ftp_port}) is off." if self.ftp_port else
                               "FTP is off: set FTP=1 in /data/etaHEN/config.ini (port 1337), or run ftpsrv.elf (port 2121).")
            if h.ftp:
                try:
                    configs = self.etahen_configs()
                except (OSError, ftplib.Error):
                    configs = {}
                if configs:
                    path, text = next(iter(configs.items()))
                    vals = ini_values(text)
                    h.etahen_config = path
                    h.dpi_setting = "on" if vals.get("DPI_v2") == "1" or vals.get("DPI") == "1" else "off"
            if not (h.dpi2 or h.dpi1):
                h.hints.append(self._dpi_hint(h, configs if h.ftp else {}))
            if not h.retroarch:
                h.hints.append("RetroArch's WebUI (6769) is not running; games copied in still appear on its next scan.")
        return h

    def _dpi_hint(self, h: Health, configs: dict[str, str]) -> str:
        name = posixpath.basename(h.etahen_config)
        if not h.dpi_setting:
            return ("Direct Package Installer is off: set DPI_v2=1 in /data/etaHEN/config.ini. "
                    "Needed for PS4 packages only.")
        # etaHEN's own FTP (1337) is a sign of life when its settings have it on.
        etahen_ftp = ini_values(configs.get(h.etahen_config, "")).get("FTP") == "1"
        not_running = " etaHEN does not seem to be running (its FTP on 1337 does not answer)." \
            if etahen_ftp and not _tcp_open(self.host, FTP_PORT) else ""
        if h.dpi_setting == "off":
            return (f"Direct Package Installer is off in etaHEN's settings ({name}). Turn it on above; it starts "
                    f"the next time etaHEN loads. Needed for PS4 packages only.{not_running}")
        return (f"Direct Package Installer is on in etaHEN's settings ({name}) but does not answer: etaHEN "
                f"reads its settings when it starts, so load etaHEN again.{not_running}")

    # ---- etaHEN's settings ---------------------------------------------------------
    def etahen_configs(self) -> dict[str, str]:
        """etaHEN's config files on the PS5 (path -> text), the one in use first."""
        out: dict[str, str] = {}
        with self.ftp(timeout=10) as f:
            for name in ETAHEN_CONFIGS:
                buf = io.BytesIO()
                try:
                    f.retrbinary(f"RETR {ETAHEN_DIR}/{name}", buf.write)
                except ftplib.error_perm:
                    continue
                out[f"{ETAHEN_DIR}/{name}"] = buf.getvalue().decode("utf-8", "replace")
        return out

    def set_etahen(self, values: dict[str, int]) -> list[str]:
        """Write settings into every etaHEN config file on the PS5; returns the files written.
        etaHEN reads them when it starts."""
        configs = self.etahen_configs()
        if not configs:
            raise FileNotFoundError(f"etaHEN's settings are not on the PS5 ({ETAHEN_DIR}/{ETAHEN_CONFIGS[0]}). "
                                    "Load etaHEN once so it writes them.")
        with self.ftp() as f:
            f.voidcmd("TYPE I")
            for path, text in configs.items():
                for k, v in values.items():
                    text = ini_set(text, k, str(v))
                f.storbinary(f"STOR {path}", io.BytesIO(text.encode("utf-8")))
        return list(configs)

    # ---- FTP ----------------------------------------------------------------------
    def ftp(self, timeout: float = 20.0) -> ftplib.FTP:
        port = self.ftp_port or self.find_ftp_port()
        if not port:
            raise OSError(f"no FTP server answered on {self.host} (ports {', '.join(map(str, FTP_PORTS))})")
        f = ftplib.FTP()
        f.connect(self.host, port, timeout=timeout)
        f.login()                  # both PS5 FTP servers are anonymous
        f.set_pasv(True)
        return f

    def listdir(self, path: str) -> list[RemoteEntry]:
        with self.ftp() as f:
            return _listdir(f, path)

    def mkdirs(self, f: ftplib.FTP, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        cur = ""
        for p in parts:
            cur += "/" + p
            try:
                f.mkd(cur)
            except ftplib.error_perm:
                pass           # exists (or cannot be made; the STOR will say)

    def remote_size(self, f: ftplib.FTP, path: str) -> int | None:
        try:
            f.voidcmd("TYPE I")
            return f.size(path)
        except (ftplib.error_perm, ftplib.error_temp, OSError):
            return None

    def upload(self, local: str, remote: str, progress: Callable[[int], None] | None = None,
               cancelled: Callable[[], bool] | None = None, skip_existing: bool = True) -> str:
        """Copy one file (a local path or an http(s) URL of a peer's file) to the PS5.
        Writes <remote>.gameshop-partial, resumes it when present, renames at the end.
        Returns 'copied' | 'skipped' | 'cancelled'."""
        src = open_source(local)
        total = src.size
        partial = remote + PARTIAL_SUFFIX
        with self.ftp() as f:
            self.mkdirs(f, posixpath.dirname(remote))
            if skip_existing:
                have = self.remote_size(f, remote)
                if have == total:
                    if progress:
                        progress(total)
                    return "skipped"
            offset = self.remote_size(f, partial) or 0
            if offset > total:
                offset = 0
            f.voidcmd("TYPE I")
            sent = offset
            with src.open(offset) as fp:
                conn = f.transfercmd(f"STOR {partial}", rest=offset or None)
                try:
                    while True:
                        if cancelled and cancelled():
                            conn.close()
                            try:
                                f.voidresp()
                            except (ftplib.Error, OSError):
                                pass
                            return "cancelled"
                        chunk = fp.read(BLOCK)
                        if not chunk:
                            break
                        conn.sendall(chunk)
                        sent += len(chunk)
                        if progress:
                            progress(sent)
                finally:
                    conn.close()
                f.voidresp()
            got = self.remote_size(f, partial)
            if got is not None and got != total:
                raise IOError(f"size mismatch after copy: PS5 has {got} bytes, file is {total}")
            try:
                _dele(f, remote)
            except ftplib.error_perm:
                pass
            f.rename(partial, remote)
        return "copied"

    def delete(self, path: str, is_dir: bool = False) -> None:
        with self.ftp() as f:
            if is_dir:
                _rmtree(f, path)
            else:
                _dele(f, path)

    def free_space_hint(self) -> dict[str, int] | None:
        """etaHEN's FTP has no reliable free-space command; return None (the UI says 'unknown')."""
        return None

    # ---- Direct Package Installer -------------------------------------------------
    def dpi_install(self, url: str) -> str:
        """Ask etaHEN to download and install a package from url. Returns the route used.
        v2 (HTTP 12800) first, then v1 (TCP 9090)."""
        errors = []
        try:
            return self._dpi2(url)
        except Exception as e:                       # noqa: BLE001 — surface the real reason below
            errors.append(f"DPI v2: {e}")
        try:
            return self._dpi1(url)
        except Exception as e:                       # noqa: BLE001
            errors.append(f"DPI v1: {e}")
        raise IOError("; ".join(errors))

    def _dpi2(self, url: str) -> str:
        boundary = "----GameShop" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"url\"\r\n\r\n{url}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        req = urllib.request.Request(
            f"http://{self.host}:{DPI2_PORT}/upload", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:    # noqa: S310
            text = r.read().decode("utf-8", "replace")
        if "SUCCESS" not in text.upper():
            raise IOError(text.strip()[:300] or "no answer")
        return "dpi2"

    def _dpi1(self, url: str) -> str:
        with socket.create_connection((self.host, DPI1_PORT), timeout=120) as s:
            s.sendall(json.dumps({"url": url}).encode())
            s.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        try:
            res = json.loads(data.decode("utf-8", "replace") or "{}")
        except ValueError:
            raise IOError(f"unexpected reply: {data[:200]!r}")
        if str(res.get("res", "1")) != "0":
            raise IOError(f"etaHEN returned {res}")
        return "dpi1"

    # ---- ELF loader ----------------------------------------------------------------
    def send_elf(self, local: str, port: int = ELF_PORT) -> None:
        with open(local, "rb") as fp:
            self.send_bytes(fp.read(), port)

    def send_bytes(self, data: bytes, port: int = ELF_PORT) -> None:
        with socket.create_connection((self.host, port), timeout=10) as s:
            s.sendall(data)

    # ---- the Game Shop tile and the open payload -----------------------------------
    def install_tile(self, url: str) -> None:
        """Put Game Shop on the PS5 home screen: copy the Game Shop app (gameshop/ps5app/<id>/) to
        /data/homebrew/<id>/, where Shadow Mount+ registers it on its next scan, with the store's
        address in url.txt beside the eboot (the app reads it to find this PC). Repeat to update
        the app or the address."""
        base = f"/data/homebrew/{TILE_ID}"
        with self.ftp() as f:
            for d in sorted({posixpath.dirname(rel) for rel in tile_files()} - {""}):
                self.mkdirs(f, f"{base}/{d}")
            f.voidcmd("TYPE I")
            f.storbinary(f"STOR {base}/url.txt", io.BytesIO((url + "\n").encode("utf-8")))
            for rel in tile_files():
                with open(TILE_APP / rel, "rb") as fp:
                    f.storbinary(f"STOR {base}/{rel}", fp)

    def open_browser(self, url: str) -> None:
        """Open the PS5's web browser at url, by sending the gameshop-open payload with the url
        written into its buffer."""
        self.send_bytes(open_payload(url))

    # ---- RetroArch WebUI -----------------------------------------------------------
    def retroarch_refresh(self) -> bool:
        """Best effort: tell RetroArch's WebUI to look again. Returns False when unreachable."""
        try:
            _http_get(f"http://{self.host}:{RA_PORT}/api/status", timeout=3)
            return True
        except OSError:
            return False


def tile_files() -> list[str]:
    """The Game Shop app's files, relative to its folder, eboot.bin last: Shadow Mount+ takes a
    folder with an eboot for a finished app, so everything else is in place first."""
    rels = [p.relative_to(TILE_APP).as_posix() for p in TILE_APP.rglob("*") if p.is_file()]
    return sorted(rels, key=lambda r: (r == "eboot.bin", r))


def open_payload(url: str) -> bytes:
    elf = (PAYLOAD_DIR / "gameshop-open.elf").read_bytes()
    u = url.encode("utf-8")
    if len(u) >= 512:
        raise ValueError("url too long for the payload")
    i = elf.index(URL_MARK)
    return elf[:i] + u + b"\0" * (512 - len(u)) + elf[i + 512:]


# ---- sources ---------------------------------------------------------------------------
class LocalSource:
    def __init__(self, path: str):
        import os
        self.path = path
        self.size = os.path.getsize(path)

    def open(self, offset: int = 0):
        fp = open(self.path, "rb")
        fp.seek(offset)
        return fp


class HttpSource:
    """A file another agent serves (/files/<id>/<n>), read with Range requests so copies resume."""
    def __init__(self, url: str):
        self.url = url
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:    # noqa: S310
            length = r.headers.get("Content-Length")
        if length is None:
            raise IOError(f"{url}: no Content-Length")
        self.size = int(length)

    def open(self, offset: int = 0):
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        req = urllib.request.Request(self.url, headers=headers)
        r = urllib.request.urlopen(req, timeout=60)             # noqa: S310
        if offset and r.status != 206:
            r.close()
            raise IOError(f"{self.url}: the peer did not honour the Range request")
        return r


def open_source(spec: str):
    if spec.startswith("http://") or spec.startswith("https://"):
        return HttpSource(spec)
    return LocalSource(spec)


# ---- etaHEN's config.ini ----------------------------------------------------------------
def ini_values(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        k, eq, v = line.partition("=")
        if eq and not line.lstrip().startswith((";", "#", "[")):
            out[k.strip()] = v.strip()
    return out


def ini_set(text: str, key: str, value: str) -> str:
    """text with key=value: the existing line changed in place, else a line added under [Settings]
    (or at the end). Line endings and everything else are kept."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        k, eq, _ = line.partition("=")
        if eq and k.strip() == key:
            end = line[len(line.rstrip("\r\n")):] or ""
            lines[i] = f"{k}={value}{end}"
            return "".join(lines)
    new = f"{key}={value}{nl}"
    for i, line in enumerate(lines):
        if line.strip().lower() == "[settings]":
            lines.insert(i + 1, new if line.endswith(("\n", "\r")) else nl + new)
            return "".join(lines)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += nl
    return "".join(lines) + new


# ---- FTP helpers ---------------------------------------------------------------------
def _listdir(f: ftplib.FTP, path: str) -> list[RemoteEntry]:
    entries: list[RemoteEntry] = []
    f.cwd(path)              # ftpsrv.elf lists the current directory whatever path MLSD is given; a missing folder is a 550 here
    try:
        for name, facts in f.mlsd():
            if name in (".", ".."):
                continue
            entries.append(RemoteEntry(name, facts.get("type") == "dir", int(facts.get("size", 0) or 0)))
        return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))
    except (ftplib.error_perm, ftplib.error_temp):
        pass
    lines: list[str] = []
    f.retrlines("LIST", lines.append)
    for ln in lines:
        parts = ln.split(None, 8)
        if len(parts) < 9:
            continue
        mode, size, name = parts[0], parts[4], parts[8]
        if name in (".", ".."):
            continue
        entries.append(RemoteEntry(name, mode.startswith("d"), int(size) if size.isdigit() else 0))
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


def _rmtree(f: ftplib.FTP, path: str) -> None:
    for e in _listdir(f, path):
        full = posixpath.join(path, e.name)
        if e.is_dir:
            _rmtree(f, full)
        else:
            _dele(f, full)
    f.rmd(path)


def _dele(f: ftplib.FTP, path: str) -> None:
    """DELE accepting any 2xx reply: ftpsrv.elf says 226, which ftplib's delete() refuses."""
    f.voidcmd(f"DELE {path}")


def scan_subnet(prefix: str, ports: Iterable[int] = FTP_PORTS + (DPI2_PORT,), timeout: float = 0.4) -> list[str]:
    """Find PS5s on a /24: hosts answering on any of the ports. prefix like '192.168.1'."""
    import concurrent.futures as cf
    found: list[str] = []

    def probe(i: int) -> str | None:
        host = f"{prefix}.{i}"
        return host if any(_tcp_open(host, p, timeout) for p in ports) else None

    with cf.ThreadPoolExecutor(max_workers=64) as ex:
        for r in ex.map(probe, range(1, 255)):
            if r:
                found.append(r)
    return found
