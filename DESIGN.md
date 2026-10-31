# Game Shop — design

Copy games from the Windows PCs that hold your backup collections to a jailbroken PS5
(firmware 13.60) over the home network, on demand, from the couch or from a PC.

Status: design for review, 2026-10-31. Nothing built yet.

---

## 1. What we learned (and what it changes)

| Fact | Source | Consequence for the design |
|---|---|---|
| 13.60 is jailbroken by the browser-based **Relapse** exploit; the usual payload chain is kstuff → ShadowMount+ → etaHEN. Payloads vanish on reboot or rest mode and must be resent. | Relapse writeups, ps5upload FAQ | Game Shop must assume the PS5 side is *stateless*: nothing we install persists on the console. Everything the app depends on must be reloaded with the jailbreak, or be built in to etaHEN. |
| **etaHEN** exposes, on the PS5: FTP on **1337**, ELF loader on **9021**, Direct PKG Installer (DPI) v1 on **9090** (TCP, JSON `{"url": ...}` → `{"res": "0"}`), DPI **v2** HTTP on **12800** (`GET /` answers with a page containing "etaHEN"; `POST /upload` multipart with a `url` field → body contains `SUCCESS:`). | etaHEN README, DirectPackageInstaller source | We get an install API and a file-copy API **for free**. A PS5 payload of our own is not required for version 1. |
| **PS5 fake packages (PPSA…) install on firmware above 11.60, including 13.60, but do not launch.** PS4 fake packages (CUSA…) and PS5 homebrew apps work normally. | ps5upload README + FAQ | The PS5 half of a collection cannot be served as `.pkg` installs. The playable route on 13.60 is a **game image** (`.exfat`, `.ffpkg`, `.ffpfs`, `.ffpfsc`) dropped into a folder **ShadowMount+** watches (default `/data/homebrew`, also `homebrew/` on an external drive); it mounts the image and puts the game on the home screen within about 15 s. Loose game folders are *not* mounted; they need an "add to home screen" call from a payload. |
| **ps5upload** (GPL‑3, phantomptr) already does nearly all of this: resumable encrypted uploads, stream-install, image conversion, a couch UI served to the PS5's browser, tested on 13.60. Its PS5 half is a C payload (ps5-payload-sdk v0.45) speaking its own AVA1 protocol; its PC half is Rust + Tauri. | references/ps5upload | Building a second ps5upload would be months of duplicated work. Game Shop should solve the piece ps5upload does not: **one library across several PCs, for several systems, operated from the couch**, and should lean on etaHEN (v1) and optionally ps5upload's engine (v2) for the console mechanics. |
| The PS5's browser is WebKit, reachable via the exploit page / user guide; ps5upload serves its "Minion" page to it on port 9180 with D‑pad navigation. | ps5upload README | A controller-friendly web page served from the PC is a proven way to give the PS5 a UI without writing a payload. |

## 2. Goals and non-goals

**Goals**

1. Browse the whole collection (every PC, every system) from one screen, on the PS5 or on a PC.
2. "Send to PS5" for any item picks the right route automatically and reports progress.
3. Nothing to install on the PS5 beyond the jailbreak chain you already load.
4. Each PC runs the same small program; no central server to maintain. Any one of them can show the merged library.
5. Resume interrupted copies; never leave half-copied files where ShadowMount+ or the installer will pick them up.

**Non-goals (v1)**

- Jailbreaking, payload hosting, or anything that touches PSN.
- Converting PS5 packages to images (v2, see §8).
- Remote Play, cheats, save management — ps5upload covers these.
- Any internet-facing feature. LAN only.

## 3. Architecture

```
 PC "Office"                       PC "Basement"                     PC "Laptop"
 ┌──────────────────────┐          ┌──────────────────────┐          ┌──────────────┐
 │ gameshop agent :8750 │◄──mDNS──►│ gameshop agent :8750 │◄──mDNS──►│ agent :8750  │
 │  • library index     │          │  • library index     │          │              │
 │  • HTTP file server  │          │  • HTTP file server  │          │              │
 │  • transfer worker   │          │  • transfer worker   │          │              │
 │  • web UI (merged)   │          │  • web UI (merged)   │          │              │
 └─────────┬────────────┘          └──────────┬───────────┘          └──────────────┘
           │  HTTP GET /files/<id>  (Range)   │  FTP PUT (REST for resume)
           │  ◄────────── pulled by PS5       │  ──────────► pushed by agent
           ▼                                  ▼
 ┌───────────────────────────────────────────────────────────────────────┐
 │ PS5 13.60 + kstuff + ShadowMount+ + etaHEN                             │
 │   :12800 DPI v2  (POST /upload url=…)  → PS5 downloads pkg → installs │
 │   :9090  DPI v1  (fallback, JSON)                                      │
 │   :1337  FTP     → /data/homebrew (images), ROM folders, homebrew      │
 │   :9021  elfldr  → send .elf payloads                                  │
 │   browser → http://<any agent>:8750/couch   (D-pad UI)                 │
 └───────────────────────────────────────────────────────────────────────┘
```

**One program, three jobs.** `gameshop` is a single Python process per PC:

- **Library indexer.** Scans the folders you configure, classifies each item (§5), extracts
  title/ID/size/icon, stores the result in a local SQLite file, rescans on a timer and on file
  change. Only metadata leaves the PC until a transfer starts.
- **File server + transfer worker.** Serves files over HTTP with `Range` support (what DPI
  needs to stream-install), and runs a queue of outbound FTP jobs to the PS5.
- **Web UI.** Two skins over the same API: *Desktop* (mouse, dense) and *Couch* (`/couch`:
  large tiles, arrow-key / D‑pad focus, no frameworks, ES2017 only so the PS5's WebKit renders
  it). The UI shows the **merged** library: the local agent asks every peer it discovered for
  its index and unions the results, de-duplicated by content ID + size.

**Peer discovery.** Agents announce `_gameshop._tcp` via mDNS/zeroconf and also accept a static
peer list in the config, for networks where multicast is blocked. A peer's items are served by
*that* peer: when the PS5 streams a package that lives on "Basement", the install URL points at
Basement's agent, not at the agent whose page you clicked in.

**Transfers are jobs.** Every "Send to PS5" creates a job record (source item, route, state,
bytes done, error). Jobs survive an agent restart and resume where possible. The UI polls
`/api/jobs` (no WebSockets: simpler, and fine for the PS5 browser).

## 4. How each kind of item reaches the PS5

| Item (detected how) | Route | Playable on 13.60? | Notes |
|---|---|---|---|
| **PS4 fake package** `.pkg`, content ID `…CUSA…` | **Install via DPI**: agent POSTs `http://<agent>:8750/files/<id>` to `:12800/upload`; PS5 pulls the file with Range requests and installs it. Falls back to `:9090` JSON if v2 is off. | Yes | kstuff must be running. Nothing staged on the PS5, so no free-space problem on our side. Agent must be reachable from the PS5: Windows Firewall rule for port 8750 (installer adds it). |
| **PS5 fake package** `.pkg`, content ID `…PPSA…` | Same DPI route, but the UI shows a clear warning: *installs, will not launch on 13.60*. Default action is **disabled** unless you opt in; the card suggests "convert to image" (v2) or the image/folder you may already have. | **No** | Homebrew apps packaged as PS5 pkg (e.g. RetroArch, emulators) *do* launch; we detect those by the `app type`/category in the param and allow them. |
| **PS5 game image** `.exfat` `.ffpkg` `.ffpfs` `.ffpfsc` | **FTP copy** to `/data/homebrew/<name>.<ext>` (or `/mnt/ext0/homebrew` if you choose the external drive). Copy goes to a temp name (`.gameshop-partial`) and is renamed when complete, so ShadowMount+ never sees a half file. | Yes | ShadowMount+ mounts within ~15 s. Resume via FTP `REST`. Agent checks free space first via FTP `SIZE`/dir listing heuristics; v2 uses a payload for an exact number. |
| **PS5 game folder** (has `eboot.bin` + `sce_sys/param.json`) | **FTP recursive copy** to `/data/homebrew/<TitleID>/`. | Copy only | ShadowMount+ does not mount folders; the home-screen registration needs a payload (ps5upload "Add to home screen"). v1 copies and tells you; v2 builds an `.ffpfsc` image on the PC first (§8) so it lands as a mountable image instead. |
| **Homebrew payload** `.elf` `.bin` | Send to ELF loader `:9021` (one file per connection). | — | A "Payloads" shelf with your usual kstuff / ShadowMount+ / etaHEN order and a one-click "load chain". Handy after every reboot. |
| **Emulator content** (ROMs, ISOs for other systems; detected by folder rules you set, e.g. `D:\Games\SNES\**` → system `snes`) | **FTP copy** to the destination you map per system, e.g. `/data/RetroArch/roms/snes/`. Whole folders or single files. | Yes (in the emulator) | This is the "multiple systems" half of your collection. Per-system destination map lives in the config; defaults for RetroArch provided. |
| **Anything else** (saves, media, mods) | **FTP copy** to a path you pick in a remote file browser. | — | The remote browser is a thin FTP `LIST` view. |

Route selection is a pure function of the item's classification, so it is unit-testable without
a PS5.

## 5. Library classification

The indexer classifies on cheap evidence first and reads headers only when needed:

1. **Extension + folder rules** (fast): `.pkg`, image extensions, `.elf`, and the user's
   per-system globs.
2. **PKG header** (read the first ~4 KB only): magic, content ID (`UP1234-CUSA12345_00-…` →
   PS4; `…PPSA…` → PS5), package type, and the `param.sfo` / `param.json` offset to pull the
   title, version, and app category (game vs. homebrew app vs. update vs. DLC).
3. **Game folders**: presence of `eboot.bin` and `sce_sys/param.json`; title from the JSON.
4. **Images**: name is the only cheap metadata (`PPSA17599.ffpkg`); the ID in the filename
   is matched against the PKG/folder entries to group a game with its update/DLC/image.

Icons: `sce_sys/icon0.png` from folders and from PKG entry tables where present; cached as
small JPEGs so the couch UI stays light.

## 6. PS5 connection model

- You enter the PS5's IP once (or press **Scan**: the agent probes the subnet for `:12800`
  answering with "etaHEN" and `:1337` accepting FTP).
- Health panel shows, live: DPI v2 up / DPI v1 up / FTP up / elfldr up, and a one-line hint
  per missing service ("DPI is disabled in `/data/etaHEN/config.ini`; set `DPI_v2=1`").
- No pairing or encryption: etaHEN's own services are plain FTP/HTTP on your LAN, so Game Shop
  would gain nothing by encrypting its half. The agent binds to the LAN interface only and
  refuses requests from outside the configured subnet.

## 7. Technology choices

| Part | Choice | Why |
|---|---|---|
| Agent | **Python 3.13** (already installed), FastAPI + uvicorn, stdlib `ftplib`, `zeroconf`, SQLite | Fast to iterate, good async HTTP with Range support, every PC gets the same code. Packaged with **PyInstaller** into one `gameshop.exe` for the PCs without Python. Runs as a tray app / Windows service via `pywin32` later. |
| Web UI | Plain HTML/CSS/JS, no build step | The PS5 WebKit is the constraint; also keeps the agent a single folder. |
| PS5 side (v1) | **None of our own code.** etaHEN DPI + FTP + elfldr. | Zero install, survives etaHEN updates, nothing to rebuild per firmware. |
| PS5 side (v2, optional) | Either a small C payload on **ps5-payload-dev/sdk** (needs a Linux/WSL toolchain; nothing here yet) or driving **ps5upload's engine** (`ps5upload-engine.exe` on :19113, HTTP API) for register/launch/free-space/mount. | Only if v1's gaps (folder registration, exact free space, launch) matter to you. Reusing ps5upload's engine means Game Shop ships under GPL‑3 too, which is fine for a home tool. |
| Repo | `D:\Game Shop`, git; `references/` is git-ignored clones of etaHEN, ps5upload, ps5-payload-dev used for reading only | |

## 8. Phased plan

**Phase 1 — core loop (first usable build)**
- Agent with config file, indexer for `.pkg` / images / folders / ELF / per-system globs, SQLite index.
- HTTP file server with Range; DPI v2 install with v1 fallback; FTP copy with temp-name-then-rename and resume.
- Desktop UI: library grid with filters, PS5 health panel, job queue. Firewall rule helper.
- Tests: PKG header parser on synthetic headers; route selection; a fake etaHEN (tiny local HTTP+FTP stub) so transfers are testable without the console.

**Phase 2 — multi-PC and couch**
- mDNS discovery + static peers, merged library, cross-peer install URLs.
- `/couch` UI tuned on the PS5 browser: focus ring, large text, D‑pad, Back button, progress overlay.
- Payload chain shelf (send kstuff/ShadowMount+/etaHEN in order with delays).

**Phase 3 — PS5-game quality of life**
- Build `.ffpfsc`/`.exfat` images from game folders on the PC (port or shell out to ps5upload's `ps5-dump-forge` crates / its engine) so folders arrive mountable.
- Optional ps5upload-engine integration for exact free space, "add to home screen", launch.
- Windows service mode, auto-start, tray icon, PyInstaller release.

## 9. Risks and open questions

- **DPI v2 is marked "WIP" in etaHEN's README.** DirectPackageInstaller uses it successfully, and v1 on 9090 is the fallback, but we should verify on your console in Phase 1 before building on it further.
- **FTP throughput** through etaHEN's server is unmeasured here; ps5upload chose its own protocol partly for speed. If a 60 GB image takes too long over FTP, Phase 3 moves large copies to ps5upload's engine or our own payload.
- **PS5 browser access on 13.60.** The Relapse exploit runs in the browser, so it exists; whether it stays usable alongside the exploit page is something to confirm on your console. If it is awkward, the couch UI still works from a phone or tablet.
- **Your PS5 collection's format.** If it is mostly PS5 `.pkg` files, the honest answer is that on 13.60 they install but will not run, and converting them to images is only possible when they were made with a debug/fake key. We need a look at what you actually have before promising Phase 3.
- **Decision for you:** ps5upload already works on 13.60 today. Game Shop's reason to exist is the multi-PC, multi-system library and the couch flow. If that's not worth a custom tool to you, installing ps5upload is the fastest path to copying games tonight.

## 10. Sources

- Relapse exploit coverage: [VideoCardz](https://videocardz.com/newz/ps5-13-60-jailbreak-released-almost-every-ps5-can-now-be-jailbroken), [Tom's Hardware](https://www.tomshardware.com/video-games/playstation/new-ps5-relapse-jailbreak-enables-homebrew-and-switch-emulation-but-is-hamstrung-by-rapidly-changing-firmware-revisions-jailbreak-unlocks-firmware-13-60-but-newer-games-already-demand-firmware-14-00)
- etaHEN services, ports, DPI v1 API: `references/etaHEN/README.md` ([GitHub](https://github.com/LightningMods/etaHEN))
- DPI v2 HTTP call shape: `references/DirectPackageInstaller/.../Tasks/Installer.cs` ([GitHub](https://github.com/devcrono/DirectPackageInstaller))
- 11.60 limit, ShadowMount+ behaviour, stream-install model: `references/ps5upload/README.md`, `FAQ.md` ([GitHub](https://github.com/phantomptr/ps5upload))
- Payload SDK, websrv, ftpsrv: [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk), [websrv wiki](https://github.com/ps5-payload-dev/websrv/wiki), [ftpsrv](https://github.com/ps5-payload-dev/ftpsrv)
