import json
from pathlib import Path
import sys

import pytest
from iris_caption import __version__
from iris_caption.version import build_info, version_text


def test_single_source_package_metadata():
    import tomllib
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text())
    assert 'version' not in project['project']
    assert 'version' in project['project']['dynamic']
    assert project['tool']['setuptools']['dynamic']['version']['attr'] == 'iris_caption.version.__version__'
    assert build_info()['version'] == __version__


def test_frozen_build_information_and_mismatch(tmp_path, monkeypatch):
    assets = tmp_path / 'assets'
    assets.mkdir()
    path = assets / 'build-info.json'
    payload = dict(version=__version__,build_number=12,built_at='2026-11-10T14:00:00+00:00',
                   channel='preview',architecture='arm64',source_revision='abc123')
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path),raising=False)
    assert build_info() == payload
    assert 'Build 12' in version_text()
    payload['version'] = '9.0.0'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        build_info()
