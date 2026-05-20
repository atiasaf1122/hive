"""PyInstaller entry point for the HIVE backend sidecar.

Inside the packaged .msi this becomes `hive-backend.exe`. It boots the
same FastAPI app that `uvicorn backend.main:app` runs in dev, but with a
deliberately conservative config (host 127.0.0.1, port 8765, no reload).

In production the data dir is `%APPDATA%\\HIVE\\` on Windows,
`~/Library/Application Support/HIVE/` on macOS, `~/.local/share/HIVE/`
on Linux. Setting `HIVE_DIR` here keeps everything in one place per OS
convention; the existing `backend.persistence.db.HIVE_DIR` reads the
env var so no code change is needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _data_dir() -> Path:
    """OS-conventional per-user data directory for HIVE."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "HIVE"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "HIVE"
    return Path.home() / ".local" / "share" / "HIVE"


def main() -> None:
    if "HIVE_DIR" not in os.environ:
        d = _data_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.environ["HIVE_DIR"] = str(d)

    # Defer the heavy imports until after HIVE_DIR is set.
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=int(os.environ.get("HIVE_PORT", "8765")),
        log_level=os.environ.get("HIVE_LOG_LEVEL", "info"),
        reload=False,
    )


if __name__ == "__main__":
    main()
