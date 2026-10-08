"""Entry point frozen by PyInstaller into the desktop sidecar (daedelus-server)."""

import sys

from daedelus.cli import main

if __name__ == "__main__":
    sys.exit(main())
