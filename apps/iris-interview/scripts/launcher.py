"""Frozen application entry. Importing this module does not start capture."""
import os
from pathlib import Path
import sys

if getattr(sys, 'frozen', False):
    resources = Path(sys._MEIPASS)
    os.environ.setdefault('IRIS_INTERVIEW_AUDIO_HELPER', str(resources / 'helpers/iris-audio-helper'))
    os.environ.setdefault('IRIS_INTERVIEW_OCR_HELPER', str(resources / 'helpers/iris-ocr-helper'))

from iris_interview.app import main

raise SystemExit(main())
