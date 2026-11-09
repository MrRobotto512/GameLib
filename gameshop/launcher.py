"""Entry point for the one-file build: `GameShop.exe` runs the tray app; `GameShop.exe --console`
behaves like `python -m gameshop` (handy for reading logs)."""
from __future__ import annotations

import sys

from gameshop.__main__ import main

def _fatal(exc: BaseException) -> None:
    """No console to print to in the windowed build: show the error in a message box."""
    import ctypes
    import traceback
    text = "".join(traceback.format_exception(exc))[-1500:]
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "Game Shop could not start", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    if "--console" in sys.argv:
        sys.argv.remove("--console")
    elif "--tray" not in sys.argv:
        sys.argv.append("--tray")
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:   # noqa: BLE001
        if sys.stderr is None:
            _fatal(exc)
        raise
