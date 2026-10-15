"""One-time setup for the CampSage local prototype.

CampSage is a small browser application, so setup installs the FastAPI backend,
Gemini client, encryption, PDF extraction and test dependencies from the pinned
requirements file. It does not install desktop-assistant, voice, avatar,
Playwright or PC-automation packages from the Belal55 reference project.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
MIN_PYTHON = (3, 11)
TESTED_MAX_PYTHON = (3, 13)


def _run(label: str, args: list[str]) -> None:
    print(f"\n▶ {label}")
    subprocess.run(args, cwd=HERE, check=True)


def _check_python() -> None:
    version = sys.version_info[:2]
    if version < MIN_PYTHON:
        print(
            f"\nCampSage needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer. "
            f"Python {version[0]}.{version[1]} was detected."
        )
        raise SystemExit(1)
    if version > TESTED_MAX_PYTHON:
        print(
            f"\nNote: Python {version[0]}.{version[1]} is newer than the tested "
            f"range ending at {TESTED_MAX_PYTHON[0]}.{TESTED_MAX_PYTHON[1]}. Continuing."
        )


def main() -> None:
    print(
        f"CampSage setup on {platform.system() or 'unknown'} "
        f"with Python {sys.version_info[0]}.{sys.version_info[1]}"
    )
    _check_python()
    _run(
        "Installing Python dependencies",
        [sys.executable, "-m", "pip", "install", "-r", str(HERE / "requirements.txt")],
    )
    print(
        "\nSetup complete. Next steps:\n"
        "  1) Copy .env.example to .env.\n"
        "  2) Set MODEL_ID to the exact working Gemma model ID.\n"
        "  3) Run: python -m campsage.ingest\n"
        "  4) Start the app: uvicorn campsage.app:app --reload\n"
        "  5) Open http://127.0.0.1:8000 in a browser."
    )


if __name__ == "__main__":
    main()
