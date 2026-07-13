"""测试配置安全功能：明文 Key 检测 + 原子写入。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from iris.config import loader as _loader
from iris.config.loader import _check_plaintext_keys


@pytest.fixture(autouse=True)
def _reset_plaintext_warning():
    """每个测试前重置去重标志，确保独立运行。"""
    _loader._plaintext_keys_warned = False
from iris.memory.long_term import _atomic_write_json


# ── _check_plaintext_keys ──────────────────────────────────


def test_check_no_env_file():
    """无 .env 文件 → 静默通过。"""
    _check_plaintext_keys(None)  # 不抛异常
    _check_plaintext_keys(Path("/nonexistent/.env"))  # 不抛异常


def test_check_env_without_keys(capsys):
    """.env 无 Key → 无警告。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("IRIS_WORK_DOCS_DIR=/tmp/docs\n")
        f.write("IRIS_WIKI_ROOT=/tmp/wiki\n")
        env_path = Path(f.name)
    try:
        _check_plaintext_keys(env_path)
        captured = capsys.readouterr()
        assert "安全提醒" not in captured.err
    finally:
        os.unlink(env_path)


def test_check_env_with_api_key(capsys):
    """.env 含 API_KEY → stderr 输出安全提醒。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("DEEPSEEK_API_KEY=sk-abc123\n")
        f.write("IRIS_WORK_DOCS_DIR=/tmp\n")
        env_path = Path(f.name)
    try:
        _check_plaintext_keys(env_path)
        captured = capsys.readouterr()
        assert "安全提醒" in captured.err
        assert ".env" in captured.err
        assert "Keychain" in captured.err
    finally:
        os.unlink(env_path)


def test_check_env_with_multiple_keys(capsys):
    """.env 含多个 Key → 正确计数。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("DEEPSEEK_API_KEY=sk-abc\n")
        f.write("BAILIAN_API_KEY=sk-def\n")
        f.write("TRELLO_TOKEN=xyz789\n")
        env_path = Path(f.name)
    try:
        _check_plaintext_keys(env_path)
        captured = capsys.readouterr()
        assert "3 个明文 API Key" in captured.err
    finally:
        os.unlink(env_path)


def test_check_env_ignores_comments(capsys):
    """.env 注释行中的 KEY=value 不计入。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("# DEEPSEEK_API_KEY=this-is-a-comment-and-should-be-ignored\n")
        f.write("IRIS_WORK_DOCS_DIR=/tmp\n")
        env_path = Path(f.name)
    try:
        _check_plaintext_keys(env_path)
        captured = capsys.readouterr()
        assert "安全提醒" not in captured.err
    finally:
        os.unlink(env_path)


def test_check_env_empty_lines(capsys):
    """空行不影响检测。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("\n\n")
        f.write("DEEPSEEK_API_KEY=sk-xyz\n")
        f.write("\n")
        env_path = Path(f.name)
    try:
        _check_plaintext_keys(env_path)
        captured = capsys.readouterr()
        assert "1 个明文 API Key" in captured.err
    finally:
        os.unlink(env_path)


# ── _atomic_write_json ────────────────────────────────────


def test_atomic_write_creates_file():
    """原子写入创建文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sub" / "test.json"
        _atomic_write_json(path, {"key": "value"})
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == {"key": "value"}


def test_atomic_write_overwrites():
    """原子写入覆盖已有文件，内容正确。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.json"
        path.write_text('{"old": true}')
        _atomic_write_json(path, {"new": "data"})
        data = json.loads(path.read_text())
        assert data == {"new": "data"}
        assert "old" not in data


def test_atomic_write_does_not_leave_temp():
    """写入完成后不应残留临时文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "result.json"
        _atomic_write_json(path, {"x": 1})
        # 目录下应只有目标文件，无 .tmp- 文件
        siblings = list(Path(tmp).iterdir())
        assert len(siblings) == 1
        assert siblings[0].name == "result.json"


def test_atomic_write_preserves_content_integrity():
    """写入内容完整且可读（含中文/特殊字符）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "complex.json"
        payload = {
            "名字": "团队成员J",
            "特点": ["咖啡", "滑雪", "深入讨论"],
            "嵌套": {"key": "值", "arr": [1, None, True]},
        }
        _atomic_write_json(path, payload)
        data = json.loads(path.read_text())
        assert data == payload


def test_atomic_write_creates_parent_dirs():
    """父目录不存在时自动创建。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a" / "b" / "c" / "data.json"
        _atomic_write_json(path, {"hello": "world"})
        assert path.exists()
