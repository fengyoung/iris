#!/usr/bin/env python3
"""Verify frozen app using synthetic self-tests, optionally create distributable ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def checked(command, env):
    result = subprocess.run(list(map(str, command)), env=env, capture_output=True,
                            text=True, timeout=180, check=True)
    return {'stdout': result.stdout[-12000:], 'stderr': result.stderr[-4000:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--zip', action='store_true')
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    if not bundle.is_dir() or bundle.suffix != '.app':
        raise SystemExit('Expected an existing .app bundle')
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    report = {'bundle': str(bundle), 'version': info['CFBundleShortVersionString'],
              'minimum_macos': info.get('LSMinimumSystemVersion'),
              'capture_performed': False, 'model_downloaded': False}
    report['codesign'] = checked(['codesign', '--verify', '--deep', '--strict', bundle], env)
    executable = bundle / 'Contents/MacOS' / info['CFBundleExecutable']
    report['frozen_smoke'] = checked([executable, '--smoke-test'], env)
    for name in ['iris-audio-helper', 'iris-ocr-helper']:
        paths = [p for p in bundle.rglob(name) if p.is_file()]
        if not paths:
            raise SystemExit('Missing bundled helper: ' + name)
        report[name] = checked([paths[0], '--self-test'], env)
    expected = ROOT / 'src/iris_interview/assets/Iris-interview.icns'
    icon_name = info['CFBundleIconFile']
    icon = bundle / 'Contents/Resources' / icon_name
    if not icon.exists():
        icon = icon.with_suffix('.icns')
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if digest(icon) != digest(expected):
        raise SystemExit('Default inverse icon differs from approved application icon')
    report['default_inverse_icon_sha256'] = digest(icon)
    trusted_ca_names = {'cacert.pem', 'roots.pem'}
    suspicious = [str(p.relative_to(bundle)) for p in bundle.rglob('*')
                  if p.is_file() and ((p.suffix.lower() in {'.onnx', '.pt', '.pth', '.pem', '.key'}
                                       and p.name not in trusted_ca_names) or p.name == '.env')]
    # Public CA trust stores are dependency data, not user credentials.
    for pem in bundle.rglob('*.pem'):
        if pem.is_file() and b'PRIVATE KEY' in pem.read_bytes():
            suspicious.append(str(pem.relative_to(bundle)))
    if suspicious:
        raise SystemExit('Unexpected model/credential-like files: ' + str(suspicious))
    report['no_model_or_credential_files'] = True
    if args.zip:
        archive = bundle.parent / f'Iris-interview-{report["version"]}-macos-arm64.zip'
        if archive.exists():
            raise SystemExit('Refusing to overwrite existing ZIP: ' + str(archive))
        checked(['ditto', '-c', '-k', '--keepParent', bundle, archive], env)
        report['zip'] = str(archive)
        report['zip_sha256'] = digest(archive)
    report_path = bundle.parent / 'verification.json'
    if report_path.exists():
        raise SystemExit('Refusing to overwrite verification: ' + str(report_path))
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
