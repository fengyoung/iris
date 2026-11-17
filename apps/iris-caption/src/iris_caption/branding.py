"""Locate the approved Caption identity in source checkouts and frozen bundles."""
from pathlib import Path
import sys


def icon_path(*, dock: bool = False) -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    filename = 'iris-caption-dock.png' if dock else 'iris-caption-icon.png'
    return root / 'assets' / 'logo' / filename
