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
OPERATING_SYSTEM = platform.system()  # Windows, Darwin, Linux, or another platform
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
        f"CampSage setup on {OPERATING_SYSTEM or 'unknown'} "
        f"with Python {sys.version_info[0]}.{sys.version_info[1]}"
    )
    _check_python()
    print(
        "Dependencies with PEP 508 platform markers are filtered by pip for "
        "this operating system. CampSage currently needs the same small set "
        "on Windows, macOS, and Linux."
    )
    _run(
        "Installing Python dependencies for the current operating system",
        [sys.executable, "-m", "pip", "install", "-r", str(HERE / "requirements.txt")],
    )
    if OPERATING_SYSTEM == "Windows":
        print("Windows note: no extra OS package is required for the CampSage web prototype.")
    elif OPERATING_SYSTEM == "Darwin":
        print("macOS note: no extra OS package is required for the CampSage web prototype.")
    elif OPERATING_SYSTEM == "Linux":
        print("Linux note: no extra OS package is required for the CampSage web prototype.")
    else:
        print("Platform note: pip will install only dependencies compatible with this interpreter.")
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
