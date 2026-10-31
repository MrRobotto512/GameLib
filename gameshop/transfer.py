"""The download worker: one background thread that runs queued jobs against the PS5."""
from __future__ import annotations

import posixpath
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .db import Database
from .ps5 import PS5Client, open_source


class TransferWorker:
    def __init__(self, db: Database, client_factory: Callable[[], PS5Client], file_url: Callable[[str, int], str],
                 skip_existing: Callable[[], bool] = lambda: True, after_ftp: Callable[[dict[str, Any]], None] | None = None):
        self.db = db
        self.client_factory = client_factory
        self.file_url = file_url            # (item_id, file_index) -> URL the PS5 can fetch
        self.skip_existing = skip_existing
        self.after_ftp = after_ftp
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancel: set[int] = set()
        self._thread: threading.Thread | None = None
        self.current: int | None = None

    # ---- lifecycle ----------------------------------------------------------------
    def start(self) -> None:
        self.db.reset_running()
        self._thread = threading.Thread(target=self._run, name="gameshop-transfer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def cancel(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if not job:
            return
        if job["state"] == "queued":
            self.db.update_job(job_id, state="cancelled", finished=time.time(), step="Cancelled")
        elif job["state"] in ("running", "verifying"):
            self._cancel.add(job_id)

    def retry(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if job and job["state"] in ("failed", "cancelled"):
            self.db.update_job(job_id, state="queued", step="", error="", done=0, finished=None)
            self.wake()

    # ---- loop -------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            job = self.db.next_job()
            if not job:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            self.current = job["id"]
            try:
                self._run_job(job)
            except Exception as e:                     # noqa: BLE001 — a job failure must not kill the worker
                self.db.update_job(job["id"], state="failed", error=str(e)[:500], finished=time.time(), step="Failed")
                traceback.print_exc()
            finally:
                self.current = None
                self._cancel.discard(job["id"])

    def _run_job(self, job: dict[str, Any]) -> None:
        jid = job["id"]
        self.db.update_job(jid, state="running", started=time.time(), step="Connecting")
        client = self.client_factory()
        if not client.host:
            raise IOError("No PS5 address set")
        route = job["route"]
        if route == "ftp":
            self._ftp_job(jid, job, client)
        elif route == "dpi":
            self._dpi_job(jid, job, client)
        elif route == "elf":
            self._elf_job(jid, job, client)
        else:
            raise IOError(f"unknown route {route}")

    # ---- routes -----------------------------------------------------------------------
    def _ftp_job(self, jid: int, job: dict[str, Any], client: PS5Client) -> None:
        files: list[list[str]] = job["src"]            # [[local_abs_or_url, remote_rel], ...]
        sizes = {}
        for l, _ in files:
            try:
                sizes[l] = open_source(l).size
            except OSError as e:
                raise IOError(f"cannot read {l}: {e}")
        total = sum(sizes.values())
        self.db.update_job(jid, total=total)
        done_before = 0
        last = [0.0]

        def progress_for(base: int):
            def cb(sent: int) -> None:
                now = time.time()
                if now - last[0] > 0.4:
                    last[0] = now
                    self.db.update_job(jid, done=base + sent)
            return cb

        n = len(files)
        for i, (local, rel) in enumerate(files, 1):
            if jid in self._cancel:
                self.db.update_job(jid, state="cancelled", finished=time.time(), step="Cancelled")
                return
            remote = posixpath.join(job["dest"], rel)
            name = posixpath.basename(rel)
            self.db.update_job(jid, step=f"Copying {name}" + (f" ({i} of {n})" if n > 1 else ""))
            res = client.upload(local, remote, progress=progress_for(done_before), cancelled=lambda: jid in self._cancel,
                                skip_existing=self.skip_existing())
            if res == "cancelled":
                self.db.update_job(jid, state="cancelled", finished=time.time(), step="Cancelled")
                return
            done_before += sizes[local]
            self.db.update_job(jid, done=done_before)
        self.db.update_job(jid, state="verifying", step="Checking sizes on the PS5")
        with client.ftp() as f:
            for local, rel in files:
                want = sizes[local]
                got = client.remote_size(f, posixpath.join(job["dest"], rel))
                if got != want:
                    raise IOError(f"{rel}: PS5 has {got} bytes, expected {want}")
        self.db.update_job(jid, state="done", finished=time.time(), step="On your PS5", done=total)
        if self.after_ftp:
            try:
                self.after_ftp(job)
            except Exception:                           # noqa: BLE001 — a courtesy call, never a failure
                pass

    def _dpi_job(self, jid: int, job: dict[str, Any], client: PS5Client) -> None:
        item_id = job["item_id"]
        if not item_id:
            raise IOError("DPI installs need a library item")
        url = self.file_url(item_id, 0)
        self.db.update_job(jid, step="Asking etaHEN to install", total=job.get("total") or 0)
        via = client.dpi_install(url)
        self.db.update_job(jid, state="done", finished=time.time(),
                           step=f"Handed to the PS5 installer ({'DPI v2' if via == 'dpi2' else 'DPI v1'}); watch the console's notifications")

    def _elf_job(self, jid: int, job: dict[str, Any], client: PS5Client) -> None:
        local = job["src"][0][0]
        delay = float((job.get("opts") or {}).get("delay") or 0)
        if delay > 0:
            for left in range(int(delay), 0, -1):
                if jid in self._cancel:
                    self.db.update_job(jid, state="cancelled", finished=time.time(), step="Cancelled")
                    return
                self.db.update_job(jid, step=f"Waiting {left}s for the previous payload to settle")
                time.sleep(1)
        self.db.update_job(jid, step="Sending payload", total=Path(local).stat().st_size)
        client.send_elf(local)
        self.db.update_job(jid, state="done", finished=time.time(), step="Sent to the ELF loader", done=Path(local).stat().st_size)
