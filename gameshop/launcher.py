"""Entry point for the one-file build: `GameShop.exe` runs the tray app; `GameShop.exe --console`
behaves like `python -m gameshop` (handy for reading logs)."""
from __future__ import annotations

import sys

from gameshop.__main__ import main

if __name__ == "__main__":
    if "--console" in sys.argv:
        sys.argv.remove("--console")
    elif "--tray" not in sys.argv:
        sys.argv.append("--tray")
    main()
