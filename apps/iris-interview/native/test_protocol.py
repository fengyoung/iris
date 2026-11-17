"""No audio capture, device enumeration or permission requests."""
import base64
import json
from pathlib import Path
import struct
import subprocess

helper = Path(__file__).parent / '.build' / 'iris-audio-helper'
run = subprocess.run([str(helper), '--self-test'], check=True, capture_output=True, text=True, timeout=10)
rows = [json.loads(line) for line in run.stdout.splitlines()]
assert all(row['protocol'] == 1 for row in rows)
assert [r['event'] for r in rows] == ['hello', 'pcm', 'pcm', 'stopped']
assert rows[-1]['reason'] == 'self_test'
for row, source in zip(rows[1:3], ['local', 'remote']):
    assert row['source'] == source and row['synthetic'] is True
    assert row['frames'] == 3 and row['channels'] == 1
    assert row['encoding'] == 'f32le' and row['layout'] == 'interleaved'
    data = base64.b64decode(row['data'], validate=True)
    assert len(data) == row['frames'] * row['channels'] * 4
    assert struct.unpack('<3f', data) == (0.0, 0.5, -0.5)
assert rows[2]['timestamp_ns'] - rows[1]['timestamp_ns'] == 10_000_000
bad = subprocess.run([str(helper), '--unknown'], capture_output=True, text=True, timeout=10)
assert bad.returncode == 2
errors = [json.loads(line) for line in bad.stdout.splitlines()]
assert errors[-1]['event'] == 'error' and errors[-1]['code'] == 'invalid_arguments'
assert not any(row['event'] == 'started' for row in errors)
print('PASS: protocol v1, independent tracks, shared timeline, f32le payload, stopped event, invalid command exit; no devices opened')
