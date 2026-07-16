"""测试 macOS Keychain 集成 — config/secrets.py。"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from iris.config.secrets import (
    KeychainError,
    get_secret,
    set_secret,
    delete_secret,
    list_secrets,
)


class TestGetSecret:
    def test_success(self):
        """成功的密钥读取。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="sk-test-key\n", stderr="")
            result = get_secret("DEEPSEEK_API_KEY")
            assert result == "sk-test-key"

    def test_not_found(self):
        """密钥不存在返回 None。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            result = get_secret("UNKNOWN_KEY")
            assert result is None

    def test_timeout_returns_none(self):
        """subprocess.TimeoutExpired 静默返回 None。"""
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            result = get_secret("ANY_KEY")
            assert result is None


class TestSetSecret:
    def test_success(self):
        """成功的密钥写入。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            set_secret("DEEPSEEK_API_KEY", "new-key")
            mock_run.assert_called_once()

    def test_failure_raises(self):
        """写入失败抛出 KeychainError。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="权限不足")
            with pytest.raises(KeychainError, match="写入 Keychain 失败"):
                set_secret("DEEPSEEK_API_KEY", "val")

    def test_timeout_raises(self):
        """subprocess.TimeoutExpired 抛出 KeychainError。"""
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            with pytest.raises(KeychainError, match="Keychain 操作超时"):
                set_secret("ANY_KEY", "val")


class TestDeleteSecret:
    def test_success(self):
        """删除成功返回 True。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = delete_secret("DEEPSEEK_API_KEY")
            assert result is True

    def test_not_found(self):
        """条目不存在返回 False。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = delete_secret("UNKNOWN_KEY")
            assert result is False


class TestListSecrets:
    def test_returns_existing_keys(self):
        """只返回 Keychain 中存在的密钥名。"""
        existing = {"DEEPSEEK_API_KEY": True, "BAILIAN_API_KEY": True, "NONEXIST": False}

        def mock_get_secret(key):
            return "val" if existing.get(key) else None

        with patch("iris.config.secrets.get_secret", side_effect=mock_get_secret):
            result = list_secrets()
            assert "DEEPSEEK_API_KEY" in result
            assert "BAILIAN_API_KEY" in result
            assert "NONEXIST" not in result

    def test_empty_when_none_exist(self):
        """所有密钥都不存在时返回空列表。"""
        with patch("iris.config.secrets.get_secret", return_value=None):
            result = list_secrets()
            assert result == []
