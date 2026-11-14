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

**Easiest:** download `GameShopSetup.exe` from the
[latest release](https://github.com/MrRobotto512/GameLib/releases/latest) and run it. It installs
Game Shop into `C:\Program Files\Game Shop`, adds it to the Start menu, starts it when you sign in
(a choice in the setup), allows it through Windows Firewall, and can be removed from
*Settings → Apps*. Use the tray icon (gold diamond) to open the store. Everything it keeps lives in
`%LOCALAPPDATA%\GameShop`, which neither updates nor uninstalling touch. `GameShop.exe` on its own
still runs from any folder if you'd rather not install it.

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
to TV mode by itself and opens on the shelf.

**The shelf** (`#/shelf`, the *Shelf* tab) shows each console as a full-screen 3D shelf of boxes, the
selected one facing you and the rest standing spine-out on either side, in the style of
[Boxshelf](https://github.com/sassiestrhihno/boxshelf-source). ← → browse, ↑ ↓ (L1 / R1, or Q / E)
change console, Enter opens the game's page, F flips the box, Esc goes back to the store. The mouse
wheel browses too, and clicking a box goes to it (or flips it, once it's selected).

**Back covers.** Settings → **Download back covers** fetches the
[LaunchBox Games Database](https://gamesdb.launchbox-app.com/) once (a 110 MB download, boiled down to
a small index of its box scans in `%LOCALAPPDATA%\GameShop\launchbox`), then the back of every game
in the library and the front of the same box. A flipped box on the shelf (web and PS5 app) shows
the back. The two sides come from one region's box wherever one has both, and the back is served in
its front's shape, so they are the same edition, the same size and equally sharp; the scanned front
replaces the smaller cover the store had. The selected box is drawn from a full-size picture (up to
1200 px wide). Press the button again after adding games; the database itself is fetched again
after two months.

**Game Shop on the PS5 home screen.** Settings has **Put a Game Shop tile on the PS5**: it copies
the Game Shop PS5 app over FTP to `/data/homebrew/PPSA87500/`, with this PC's store address in
`url.txt` beside it. Shadow Mount+ adds it to the home screen on its next scan (every 15 s, but not
while a game or app is open). The tile opens the store as an app of its own, full screen and driven
by the controller like FLEX: the same 3D shelf per console (Left / Right browse, L1 / R1 or Up / Down
change console, the first shelf is *Recently added*; Square flips the box), a game page where Cross
sends the game to the PS5, and *Downloads* with each copy's progress (Cross cancels, or retries a
failed one). Triangle reloads, holding Circle closes it.
It asks this PC for covers as small JPEGs (`/art/<id>?w=300`) and keeps them in `/data/gameshop/`,
along with its log. Press the button again after updating Game Shop or if this PC's address changes.
**Open on the PS5 now** sends a payload to the ELF loader that opens the web store in the PS5
browser straight away.

Both are built in WSL from `ps5/` (`PS5_PAYLOAD_SDK=/opt/ps5-payload-sdk make`) and committed, so
the Windows build needs no SDK: the payload under `gameshop/payloads/`, the app under
`gameshop/ps5app/PPSA87500/`. The app is drawn with the SDK's SDL2 ports, and its startup, heap and
HTTP code come from FLEX's PS5 app. It is linked and fake-signed with the native-app kit from
[Forwarder Manager](https://github.com/heydemoura/ps5-forwarder-manager) (GPL-3.0-or-later, which
also supplies the clean-room `sce_module/libc.prx`); set it up once with FLEX's
`ps5-client/tools/setup-app-kit.sh`. It uses cJSON (MIT) and the DejaVu fonts.

The setup allows Game Shop through Windows Firewall (needed for the PS5 and for PS4 installs,
where the PS5 downloads the package from this PC). Running from source or a loose `GameShop.exe`,
allow the port once from an **administrator** PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File tools\firewall.ps1
```

## First-time setup, in the store

1. **Settings → PS5**: enter the console's address (or **Scan network**). The health list shows
   FTP, Direct Package Installer, ELF loader and RetroArch's WebUI, each with a fix hint. The
   Direct Package Installer (needed for PS4 packages) has a **Turn on** button: it sets `DPI_v2=1`
   in etaHEN's config on the PS5 over FTP (`config.ini`, and `config-multifw-experimental.ini` for
   the multi-firmware builds), which etaHEN picks up the next time it loads.
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

## Setup, one-file build, tray and autostart

```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

makes `dist\GameShop.exe` (PyInstaller, about 30 MB) and, when
[Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed, `dist\GameShopSetup.exe` from
`tools\installer.iss`. Double-clicking the exe runs the tray app; `GameShop.exe --console` keeps a
console window for logs, and `--port`, `--data`, `--open` work as with `python -m gameshop`. From
source, `python -m gameshop --tray` is the same tray app.

**Updating:** the tray menu has *Check for updates*. It asks GitHub for the latest release and, when
it is newer, downloads its `GameShopSetup.exe` and runs it: Windows asks to allow it, the setup
closes Game Shop, installs over it and starts the new version. A loose `GameShop.exe` (in
Downloads, say) updates the same way, so it ends up installed in Program Files. The exe also checks
once, quietly, about 20 seconds after it starts and shows a notification when something newer
exists. From source, update with `git pull`.

The setup's *start when I sign in* uses the `Run` key and replaces the logon task below.

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

## License

Game Shop is free software under the [GNU GPL v3.0](LICENSE). The shelf (`gameshop/web/shelf.js`,
`shelf.css`, and its layout in the PS5 app's `ps5/app/main.c`) is adapted from
[Boxshelf](https://github.com/sassiestrhihno/boxshelf-source) by sassiestrhihno (GPL-3.0). The PS5
app is built with the GPL-3.0 native-app kit from Forwarder Manager; cJSON is MIT, the DejaVu fonts
have their own free license.
