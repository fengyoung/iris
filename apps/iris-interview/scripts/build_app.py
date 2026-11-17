#!/usr/bin/env python3
"""Build helpers, run offline checks, then freeze an isolated local app bundle."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(command, env):
    print('+', ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checks-only', action='store_true', help='Build helpers and test without PyInstaller')
    args = parser.parse_args()
    if sys.version_info < (3, 11) or sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('Requires Python 3.11+ on Apple Silicon macOS 15+. Use iris-caption/.venv/bin/python.')
    required = ['pytest', 'PySide6', 'fitz', 'docx', 'filelock', 'httpx', 'keyring']
    if not args.checks_only:
        required += ['PyInstaller', 'funasr_onnx', 'onnxruntime', 'sounddevice', 'scipy']
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        raise SystemExit('Missing build dependencies: ' + ', '.join(missing))
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT / 'src'), str(ROOT.parent / 'iris-caption/src')])
    env['QT_QPA_PLATFORM'] = 'offscreen'
    run(['sh', ROOT / 'native/build.sh'], env)
    run([ROOT / 'native/.build/iris-audio-helper', '--self-test'], env)
    run([sys.executable, ROOT / 'native/test_protocol.py'], env)
    run(['bash', ROOT / 'native-ocr/build.sh'], env)
    run([sys.executable, '-m', 'pytest', '-q'], env)
    # Synthetic, offscreen; never starts capture, reads Keychain, or calls cloud APIs.
    run([sys.executable, '-m', 'iris_interview.app', '--smoke-test'], env)
    if args.checks_only:
        print('Offline checks passed; packaging skipped.')
        return 0
    # Reserve unique staging output. Existing builds are never deleted or replaced.
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix='build-', dir=dist))
    env['PYINSTALLER_CONFIG_DIR'] = str(attempt / 'pyinstaller-cache')
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
         '--distpath', attempt / 'artifacts', '--workpath', attempt / 'work',
         ROOT / 'IrisInterview.spec'], env)
    bundle = attempt / 'artifacts/知遇.app'
    if not bundle.is_dir():
        raise SystemExit('PyInstaller did not create expected bundle: ' + str(bundle))
    helpers = {p.name for p in bundle.rglob('iris-*-helper') if p.is_file()}
    if not {'iris-audio-helper', 'iris-ocr-helper'} <= helpers:
        raise SystemExit('Bundle is missing native helpers')
    run(['codesign', '--verify', '--deep', '--strict', bundle], env)
    print('Build complete:', bundle)
    print('Personal ad-hoc build, not notarized. No installation or capture performed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
