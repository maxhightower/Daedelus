"""Build the backend as a single executable and place it where Tauri expects the sidecar.

    python packaging/build_sidecar.py            # from backend/
Produces studio/src-tauri/binaries/daedelus-server-<rust target triple>[.exe]
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
BIN_DIR = BACKEND.parent / "studio" / "src-tauri" / "binaries"


def target_triple() -> str:
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    return next(l.split(": ")[1] for l in out.splitlines() if l.startswith("host: "))


def main() -> int:
    sep = ";" if sys.platform == "win32" else ":"
    worker = BACKEND / "daedelus" / "adapters" / "blender_worker.py"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--name", "daedelus-server", "--distpath", str(BACKEND / "dist"),
           "--workpath", str(BACKEND / "build" / "pyinstaller"),
           "--specpath", str(BACKEND / "build"),
           "--add-data", f"{worker}{sep}daedelus/adapters",
           "--collect-submodules", "uvicorn", "--collect-submodules", "daedelus",
           "--collect-data", "pypdfium2_raw", "--hidden-import", "pypdfium2",
           str(HERE / "server_entry.py")]
    subprocess.run(cmd, check=True, cwd=BACKEND)
    exe = BACKEND / "dist" / ("daedelus-server.exe" if sys.platform == "win32" else "daedelus-server")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / f"daedelus-server-{target_triple()}{'.exe' if sys.platform == 'win32' else ''}"
    shutil.copy2(exe, dst)
    print(f"sidecar: {dst} ({dst.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
