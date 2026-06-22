#!/usr/bin/env python3
"""Controlus for Windows - entry point."""

import sys
from pathlib import Path

# Make `controlus` importable when run directly or from a frozen build.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from controlus.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
