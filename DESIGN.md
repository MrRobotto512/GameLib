# Game Shop — design

Copy games from the Windows PCs that hold your backup collections to a jailbroken PS5
(firmware 13.60) over the home network, on demand, from the couch or from a PC.

Status: design for review, revision 2, 2026-10-31. Nothing built yet.

Revision 2 reflects two things learned after the first draft: the collection is mostly retro
systems (PS1, PS2, PS3, PS4, Xbox 360, Switch, N64, Sega, …) rather than PS5 titles, and folder
organisation on the PCs is personal, so system/destination mapping is **manual by design**.

---

## 1. What we learned (and what it changes)

| Fact | Source | Consequence for the design |
|---|---|---|
| 13.60 is jailbroken by the browser-based **Relapse** exploit; the usual payload chain is kstuff → ShadowMount+ → etaHEN. Payloads vanish on reboot or rest mode and must be resent. | Relapse writeups, ps5upload FAQ | The PS5 side is *stateless*: nothing we install persists on the console. Lean on what etaHEN and the emulators already provide. |
| **etaHEN** exposes on the PS5: FTP on **1337**, ELF loader on **9021**, Direct PKG Installer (DPI) v1 on **9090** (TCP, JSON `{"url": ...}` → `{"res": "0"}`), DPI **v2** HTTP on **12800** (`GET /` answers with a page containing "etaHEN"; `POST /upload` multipart with a `url` field → body contains `SUCCESS:`). | etaHEN README, DirectPackageInstaller source | We get an install API and a file-copy API for free. No PS5 payload of our own in version 1. |
| **PS5 fake packages (PPSA…) install on 13.60 but do not launch.** PS4 fake packages (CUSA…) and PS5 homebrew apps work. | ps5upload README + FAQ | PS4 backups are the only native "install" route. PS5 titles are out of scope unless they are game images for ShadowMount+ (kept as a secondary route). |
| **Retro systems run in emulators that are PS5 homebrew titles under `/data/homebrew/<PPSA…>/`.** The big one is Mihawk's **RetroArch port**: 33 cores (PS1, PS2 via PS5SX2, PSP, N64, GameCube/Wii, every Sega system, arcade, Atari, …), content under `content/<system>/` using EmulationStation (ES-DE) folder names, BIOS under `system/`, a **WebUI on port 6769** with upload/library/forwarder APIs, and built-in **SMB** browsing of one share. Other emulators are separate titles: **PS5SX2** standalone (PS2, `/data/PCSX2/games/`), **ProsperoEden** (Switch, alpha, slow), **XPSemu** (original Xbox), a Xenia (Xbox 360) demo with no public build, RPCS3 (PS3) experimental. | PS5_RetroArch README/source, news coverage | The core job is **"put this file in that emulator's folder."** Destinations are fixed per emulator, so Game Shop ships a table of them and you only choose which of your folders feed which system. |
| **ps5upload** (GPL‑3) already does the PS4/PS5 package and image work on 13.60. | references/ps5upload | Game Shop does not compete with it; it fills the retro, multi-PC, couch gap. |

## 2. Goals and non-goals

**Goals**

1. Browse the whole collection (every PC, every system) from one screen, on the PS5 or a PC.
2. "Send to PS5" on any item puts it where its emulator will find it, and reports progress.
3. Nothing to install on the PS5 beyond the jailbreak chain and the emulators you already use.
4. Each PC runs the same small program; any one of them shows the merged library.
5. Manual control: you choose folder → system mappings, you can override any destination, and a plain two-pane copy exists for anything the rules don't cover.
6. Resume interrupted copies; never leave half-copied files where an emulator will pick them up.

**Non-goals (v1)**

- Jailbreaking, payload hosting, anything that touches PSN or the internet.
- PS5 native games (except copying ready-made ShadowMount+ images).
- Converting formats (ISO→CHD etc.). Copy what you have; the emulators accept the common formats.
- Save management, cheats, Remote Play (ps5upload covers these).

## 3. Architecture

```
 PC "Office"                       PC "Basement"                     PC "Laptop"
 ┌──────────────────────┐          ┌──────────────────────┐          ┌──────────────┐
 │ gameshop agent :8750 │◄──mDNS──►│ gameshop agent :8750 │◄──mDNS──►│ agent :8750  │
 │  • sources + index   │          │  • sources + index   │          │              │
 │  • HTTP file server  │          │  • HTTP file server  │          │              │
 │  • transfer worker   │          │  • transfer worker   │          │              │
 │  • web UI (merged)   │          │  • web UI (merged)   │          │              │
 └─────────┬────────────┘          └──────────┬───────────┘          └──────────────┘
           │ HTTP GET /files/<id> (Range)      │ FTP STOR/REST (resume)
           │ ◄── pulled by PS5 (DPI)           │ ──► pushed by agent
           ▼                                   ▼
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ PS5 13.60 + kstuff + ShadowMount+ + etaHEN                                  │
 │   :1337  FTP     → /data/homebrew/PPSA99169/content/<system>/  (RetroArch) │
 │                  → /data/PCSX2/games/ (PS5SX2), other emulator folders      │
 │                  → /data/homebrew/*.ffpkg (ShadowMount+ images, optional)   │
 │   :12800 DPI v2  → PS4 .pkg stream install (PS5 downloads from the agent)   │
 │   :6769  RetroArch WebUI → library refresh / forwarder tile (optional)      │
 │   :9021  elfldr  → send .elf payloads after a reboot                        │
 │   browser → http://<any agent>:8750/couch   (D-pad UI)                      │
 └────────────────────────────────────────────────────────────────────────────┘
```

**One program, three jobs.** `gameshop` is a single Python process per PC:

- **Sources and index.** A *source* is a folder you add by hand, tagged with a system (or
  "mixed / pick per item"). The indexer lists files under each source, groups multi-file games
  (`.cue`+`.bin`, `.m3u` sets, folders for DOS/ScummVM), and stores name, size, system and
  path in SQLite. No guessing from folder names unless you ask it to suggest.
- **File server + transfer worker.** HTTP with `Range` support (what DPI needs) and a queue of
  FTP jobs to the PS5.
- **Web UI.** Two skins over one API: *Desktop* and *Couch* (`/couch`: big tiles, D‑pad focus,
  plain ES2017 so the PS5's WebKit renders it). The UI shows the **merged** library from every
  peer; a peer's files are always served by that peer.

**Peer discovery.** mDNS (`_gameshop._tcp`) plus a static peer list for networks that block
multicast.

**Transfers are jobs.** Each "Send to PS5" is a job record (item, destination, state, bytes,
error) in SQLite. Jobs survive restarts and resume via FTP `REST`. UI polls `/api/jobs`.

## 4. Systems matrix: where each system's content goes on the PS5

The destination table is data (`systems.toml`), editable by you, with these defaults. `RA` =
`/data/homebrew/PPSA99169` (the RetroArch title folder; configurable in case yours differs).

| Your system | Plays via | PS5 destination (default) | Formats the emulator takes | v1 status |
|---|---|---|---|---|
| **PS1** | RetroArch · Beetle PSX HW | `RA/content/psx/` | `.chd`, `.cue`+`.bin`, `.pbp`, `.m3u` | Copy |
| **PS2** | RetroArch · PS5SX2 core | `RA/content/ps2/` | `.iso`, `.chd`, `.cso`, `.zso` | Copy. BIOS → `RA/system/pcsx2/bios/` (one-time, "System files" page). |
| **PS2** (standalone PS5SX2) | PS5SX2 title | `/data/PCSX2/games/` | same | Copy. Alternative destination selectable per item. |
| **PS3** | RPCS3 experimental builds | — | — | **Not offered.** No reliable PS5 target yet. Library shows it greyed with a note. |
| **PS4** | Native (kstuff) | DPI stream install | fake `.pkg` (CUSA) | **Install** |
| **PSP** | RetroArch · PPSSPP | `RA/content/psp/` | `.iso`, `.cso`, `.chd` | Copy |
| **Xbox 360** | Xenia demo only | — | — | **Not offered.** Greyed with a note. |
| **Original Xbox** | XPSemu title | its content folder (confirm on your console) | `.iso` (xemu format) | Copy, destination entered manually until confirmed |
| **Switch** | ProsperoEden (alpha) | its content folder (confirm on your console) | `.nsp`, `.xci` | Copy, destination manual. Marked "slow/experimental". |
| **N64** | RetroArch · Mupen64Plus-Next | `RA/content/n64/` | `.z64`, `.n64`, `.v64`, `.zip` | Copy |
| **GameCube / Wii** | RetroArch · Dolphin | `RA/content/gc/`, `RA/content/wii/` | `.iso`, `.rvz`, `.gcz` | Copy |
| **NES / SNES / GB / GBC / GBA / DS / 3DS** | RetroArch | `RA/content/{nes,snes,gb,gbc,gba,nds,n3ds}/` | `.zip`, native | Copy |
| **Sega** Genesis, Master System, Game Gear, Sega CD, 32X, Saturn, Dreamcast | RetroArch | `RA/content/{genesis,mastersystem,gamegear,segacd,sega32x,saturn,dreamcast}/` | `.zip`, `.chd`, `.cue`, `.gdi` | Copy. Saturn BIOS → `RA/system/Saturn/`. |
| **Arcade / Neo Geo / PC Engine / Atari / C64 / Amiga / DOS / ScummVM / 3DO / …** | RetroArch | `RA/content/<es-de name>/` | per core | Copy. Full ES-DE name list ships in `systems.toml`. |
| **PS5 game images** | ShadowMount+ | `/data/homebrew/` | `.exfat` `.ffpkg` `.ffpfs` `.ffpfsc` | Copy (secondary) |
| **Homebrew payloads** | etaHEN ELF loader | port 9021 | `.elf`, `.bin` | Send |
| **Anything else** | — | you pick the path in the remote browser | any | Manual copy |

Rules the worker follows for every copy:

- Upload to `<name>.gameshop-partial`, then rename. Emulators never see a half file.
- Multi-file games move as a unit (the `.cue` and all its tracks; a game folder).
- Resume with FTP `REST` when a `.gameshop-partial` of the right size prefix exists.
- Skip when the destination already has a file of the same name and size (configurable).
- After copies into RetroArch, optionally call its WebUI (`/api/library/add`) so the game shows
  in its library immediately; otherwise it appears on the next scan. Optionally create a
  **home-screen forwarder tile** for a favourite (`/api/forwarders`).

**Play-in-place alternative.** RetroArch can browse one SMB share. Game Shop's settings page
has a "share this source over SMB" helper (Windows file sharing on the chosen folder, read-only,
plus the exact server/share/folder to type into RetroArch's SMB page). For small ROMs this
avoids copying altogether; big disc images still copy.

## 5. Manual-first mapping

- **Add source** → pick a folder → pick a system from the matrix (or "mixed"). Nothing is
  inferred without your say; a "Suggest systems" button offers guesses based on extensions and
  you accept or change them per folder.
- **Per item override** → every card has "Send to…" with the default destination preselected
  and a dropdown of all known destinations plus "Browse PS5…".
- **Two-pane manual copy** → a classic left (this PC / any peer) and right (PS5 over FTP) file
  manager for one-off moves, folder creation, deletes. This is also where BIOS files go.
- **Unknown systems** (PS3, Xbox 360 today) stay visible in the library, greyed, so the
  collection view is complete; the day an emulator appears you add one row to `systems.toml`.

## 6. PS5 connection model

- Enter the PS5's IP once, or **Scan** the subnet for `:1337` FTP and `:12800` "etaHEN".
- Health panel: FTP / DPI v2 / DPI v1 / elfldr / RetroArch WebUI, each with a one-line fix hint
  (e.g. "set `DPI_v2=1` in `/data/etaHEN/config.ini`").
- No pairing or encryption; etaHEN's own services are plain FTP/HTTP on your LAN. The agent
  binds to the LAN interface and refuses requests from outside its subnet.

## 7. Technology choices

| Part | Choice | Why |
|---|---|---|
| Agent | **Python 3.13** (installed), FastAPI + uvicorn, stdlib `ftplib`, `zeroconf`, SQLite | Quick to build, same code on every PC; **PyInstaller** makes one `gameshop.exe` for PCs without Python. Tray/service mode later. |
| Web UI | Plain HTML/CSS/JS, no build step | PS5 WebKit is the constraint; keeps the agent a single folder. |
| PS5 side (v1) | None of our own code: etaHEN FTP + DPI + elfldr, RetroArch WebUI | Zero install, survives updates. |
| PS5 side (v2, optional) | Drive **ps5upload-engine** (:19113) for exact free space / launch, or a small C payload on ps5-payload-dev/sdk (needs a Linux/WSL toolchain) | Only if v1's gaps matter. |
| Repo | `D:\Game Shop`, git; `references/` is git-ignored clones used for reading | |

## 8. Phased plan

**Phase 1 — core loop (first usable build)**
- Agent + config, manual sources, indexer with multi-file grouping, SQLite.
- FTP worker with partial-then-rename, resume, skip-if-present; `systems.toml` with the matrix.
- PS4 `.pkg` install via DPI v2 (v1 fallback) from the agent's Range-capable file server.
- Desktop UI: library grid with system filter, "Send to…", PS5 health, job queue, two-pane manual copy.
- Tests: grouping rules, destination resolution, and a fake PS5 (local FTP + HTTP stub) so transfers test without the console.

**Phase 2 — multi-PC and couch**
- mDNS + static peers, merged library, per-peer file serving.
- `/couch` UI tuned on the PS5 browser.
- Payload shelf (resend kstuff / ShadowMount+ / etaHEN in order).
- RetroArch WebUI hooks: library add, forwarder tiles; SMB share helper.

**Phase 3 — polish**
- Windows service / tray, PyInstaller release, auto-start.
- Optional ps5upload-engine integration; ShadowMount+ image route refinements.

## 9. Risks and open questions

- **Emulator folder paths other than RetroArch** (PS5SX2 standalone, XPSemu, ProsperoEden) come from news articles, not from READMEs I could reach; they ship as editable defaults and the health page verifies each path exists over FTP before the first copy.
- **DPI v2 is "WIP"** per etaHEN. DirectPackageInstaller uses it fine; v1 on 9090 is the fallback. Verify on your console early in Phase 1.
- **FTP throughput** through etaHEN's server is unmeasured. ROMs are small; PS2/GC/PS4 images are not. If it is slow, Phase 3 can move big copies to ps5upload's engine.
- **PS5 browser access on 13.60** alongside the Relapse page needs confirming. The couch UI also works from a phone.
- **PS3 and Xbox 360** have no dependable PS5 target today. The library still shows them; nothing is promised.

## 10. Sources

- Relapse exploit: [VideoCardz](https://videocardz.com/newz/ps5-13-60-jailbreak-released-almost-every-ps5-can-now-be-jailbroken), [Tom's Hardware](https://www.tomshardware.com/video-games/playstation/new-ps5-relapse-jailbreak-enables-homebrew-and-switch-emulation-but-is-hamstrung-by-rapidly-changing-firmware-revisions-jailbreak-unlocks-firmware-13-60-but-newer-games-already-demand-firmware-14-00)
- etaHEN services and DPI v1 API: `references/etaHEN/README.md` ([GitHub](https://github.com/LightningMods/etaHEN)); DPI v2 call shape: `references/DirectPackageInstaller` ([GitHub](https://github.com/devcrono/DirectPackageInstaller))
- 11.60 limit, ShadowMount+, stream install: `references/ps5upload` ([GitHub](https://github.com/phantomptr/ps5upload))
- RetroArch PS5 port layout, cores, WebUI API, SMB: `references/PS5_RetroArch` ([GitHub](https://github.com/mihawk-99/PS5_RetroArch)); [RetroVault](https://retrovault.ch/news/retroarch-natif-ps5-mihawk-2026); [RetroLoader](https://www.generationamiga.com/2026/08/16/ps5-retro-gaming-gets-a-new-frontend-with-retroloader/)
- PS5SX2 (PS2): [PC Games Hardware](https://www.pcgameshardware.de/Playstation-5-Konsolen-265878/News/PS2-Emulator-PS5SX2-Jailbreak-PCSX2-1555250/), [Hackaday](https://hackaday.com/2026/10/03/play-playstation-2-and-xbox-1-games-on-your-jailbroken-ps5/)
- ProsperoEden (Switch): [Android Authority](https://androidauthority.com/prosperoeden-switch-emulator-playstation-5-3716603), [Notebookcheck](https://www.notebookcheck.net/You-can-now-play-Nintendo-Switch-games-on-your-PlayStation-5-but-there-s-a-small-catch.1415151.0.html)
- Xbox / 360 / PS3 state: [Windows Central](https://www.windowscentral.com/gaming/xbox/xbox-360-and-og-xbox-games-are-now-running-on-ps5-thanks-to-a-new-emulator), [TweakTown](https://www.tweaktown.com/news/113874/a-jailbroken-ps5-can-now-run-original-xbox-games/index.html), [Windows Report](https://windowsreport.com/?p=1495348)
- Payload SDK: [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk)
