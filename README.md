# Game Shop

A storefront for your own game backups that delivers to a jailbroken PS5 (firmware 13.60) over the
home network. Consoles are departments, every game is a product with a 3D case (retail box by default, or the cartridge / disc with the *Box / Cart* chip), updates and DLC sit on their game's page instead of cluttering the shelves, and **Download**
copies it to the folder its PS5 emulator reads. See [DESIGN.md](DESIGN.md) for the whole picture.

Built so far (Phases 1 to 3): manual folder → console mapping, FTP copies into RetroArch and other
emulator folders, PS4 package installs through etaHEN's Direct Package Installer, cover art,
screenshots and title screens from libretro-thumbnails, game facts (year, developer, publisher,
genre, players) from libretro-database, retail box fronts from GameTDB (Switch, DS, 3DS, Wii, GameCube, PS3) and serial-keyed PS1 / PS2 covers, Switch facts from titledb by title ID, a Downloads queue, a two-pane manual copy, a TV mode for
the PS5 browser, **several PCs as one store** (found by mDNS or added by name, with this PC as the
gateway that streams their games to the PS5), a **payload chain** sender, RetroArch **home-screen
tiles**, an SMB "play in place" helper, a **tray app**, a one-file **GameShop.exe** and autostart.

## Run it

**Easiest:** build or download `GameShop.exe` (see *One-file build* below), double-click it, and use
the tray icon (gold diamond) to open the store. Everything it keeps lives in `%LOCALAPPDATA%\GameShop`.

**From source:** Windows with Python 3.11+ (3.13 tested). On the PS5: the jailbreak chain you already
use (kstuff, ShadowMount+, etaHEN) with etaHEN's FTP on, and Mihawk's RetroArch port for retro
systems.

```bash
python -m venv .venv
```
```bash
.venv\Scripts\pip install -r requirements.txt
```
```bash
.venv\Scripts\python -m gameshop --open
```

The store opens at `http://127.0.0.1:8750/`. On the PS5's browser (or a phone) use the LAN address
the console prints at start-up, for example `http://192.168.1.20:8750/`; the PS5 browser switches
to TV mode by itself.

Nobody wants to type an address with a controller, so Settings has **Put a Game Shop tile on the
PS5**: it copies a tiny PS5 app over FTP to `/data/homebrew/PPSA87500/`, with this PC's store
address in `url.txt` beside it. Shadow Mount+ adds it to the home screen on its next scan (every
15 s, but not while a game or app is open), and opening the tile opens the store in the PS5 browser.
Press it again if this PC's address changes. **Open on the PS5 now** sends a payload to the ELF
loader that opens the browser at the store straight away.

Both are built in WSL from `ps5/` (`PS5_PAYLOAD_SDK=/opt/ps5-payload-sdk make`) and committed, so
the Windows build needs no SDK: the payload under `gameshop/payloads/`, the tile app under
`gameshop/ps5app/PPSA87500/`. The tile app is linked and fake-signed with the native-app kit from
[Forwarder Manager](https://github.com/heydemoura/ps5-forwarder-manager) (GPL-3.0-or-later, which
also supplies the clean-room `sce_module/libc.prx`); set it up once with FLEX's
`ps5-client/tools/setup-app-kit.sh`.

Allow the port through Windows Firewall once (needed for the PS5 browser and for PS4 installs,
where the PS5 downloads the package from this PC). From an **administrator** PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File tools\firewall.ps1
```

## First-time setup, in the store

1. **Settings → PS5**: enter the console's address (or **Scan network**). The health list shows
   FTP, Direct Package Installer, ELF loader and RetroArch's WebUI, each with a fix hint.
2. **Settings → Sources**: add a folder of backups and say which console is in it. Repeat per
   console. "Mixed" tells the console by file type and, where several share one, by what is
   inside the file (an ISO is read far enough to tell PS2 from PSX, PSP, PS3, GameCube, Wii
   and Xbox). Text files, files that only have a game's extension, unpacked discs and
   emulator source trees are left out. Anything that still shows up wrongly has a **Hide
   this** button on its page.
3. **Settings → Destinations**: blank rows use the RetroArch layout
   (`/data/homebrew/PPSA99169/content/<system>/`). Switch, original Xbox and standalone PS5SX2
   have no default and need a folder.
4. Open a console, open a game, press **Download**. **Downloads** shows the queue; the product page
   shows "On your PS5" once the file is there with the right size.
5. **Other PCs**: run Game Shop on each PC that holds backups. On the same LAN they find each other;
   for another LAN or Tailscale, add the PC by name under **Settings → Other PCs**. Their games show
   in the store with a badge, and a Download streams the file through this PC to the PS5, so the
   PS5 only ever talks to this one.
6. **Payloads**: add kstuff, ShadowMount+ and etaHEN in order under **Settings → Payloads** with a
   wait between them; **Load the chain** sends them after a reboot.

## One-file build, tray and autostart

```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

makes `dist\GameShop.exe` (PyInstaller, about 25 MB). Double-clicking it runs the tray app;
`GameShop.exe --console` keeps a console window for logs, and `--port`, `--data`, `--open` work as
with `python -m gameshop`. From source, `python -m gameshop --tray` is the same tray app.

**Updating:** the tray menu has *Check for updates*. It asks GitHub for the latest release, and when
there is a newer `GameShop.exe` it downloads it next to the running one, swaps the files and
restarts with the same arguments. The exe also checks once, quietly, about 20 seconds after it
starts and shows a notification when something newer exists. From source, update with `git pull`.

```powershell
powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1
```

registers a logon task for the current user (no admin needed) that starts the exe if it exists,
otherwise the venv's `pythonw -m gameshop --tray`. `-Remove` takes it away again.

## What goes where

| Your system | Route | Default PS5 folder |
|---|---|---|
| PS1, PS2, PSP, N64, SNES, NES, GB/GBC/GBA, DS, 3DS, GameCube, Wii, Genesis, Master System, Game Gear, Sega CD, 32X, Saturn, Dreamcast, arcade, Neo Geo, PC Engine, Atari, C64, Amiga, DOS, ScummVM | FTP copy | `<RetroArch>/content/<system>/` |
| PS4 fake packages | etaHEN Direct Package Installer (the PS5 pulls the file from this PC) | installed by the console |
| PS5 game images (`.exfat`, `.ffpkg`, `.ffpfs`, `.ffpfsc`) | FTP copy | `/data/homebrew/` (ShadowMount+) |
| Switch (ProsperoEden), original Xbox (XPSemu) | FTP copy | set in Settings |
| PS3, Xbox 360 | none yet | listed, downloads off |
| `.elf` payloads | ELF loader, port 9021 | — |

The table lives in `gameshop/systems.toml`; add a row when a new emulator appears.

## Development

```bash
.venv\Scripts\python -m pytest -q
```

Tests run against a fake PS5 (an FTP server and a DPI stub on localhost), so no console is needed.
Two agents are started in `tests/test_peers.py` to cover the merged library and the gateway.
`gameshop/` is the agent (FastAPI + SQLite + a transfer thread); `gameshop/web/` is the storefront,
plain HTML/CSS/JS so the PS5's WebKit can run it. `references/` holds read-only clones of the
projects this builds on (etaHEN, ps5upload, PS5_RetroArch, ProsperoStore, ps5-payload-dev).

Nothing here jailbreaks a console, and nothing talks to the internet except optional cover-art
downloads from GitHub.
