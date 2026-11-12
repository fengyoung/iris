"""Locate the approved Caption identity in source checkouts and frozen bundles."""
from pathlib import Path
import sys


def icon_path() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    return root / 'assets' / 'logo' / 'iris-caption-icon.png'
