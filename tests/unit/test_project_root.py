"""项目根目录解析契约测试。"""

from __future__ import annotations

import pytest

from iris.utils import paths


def test_project_root_prefers_environment(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(paths, "_PROJECT_ROOT", None)

    assert paths.get_project_root() == tmp_path.resolve()


def test_project_root_rejects_invalid_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(paths, "_PROJECT_ROOT", None)

    with pytest.raises(RuntimeError, match="不是有效 Iris 项目目录"):
        paths.get_project_root()
