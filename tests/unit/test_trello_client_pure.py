"""Trello 客户端纯函数 — 单元测试（IP/DNS/URL 逻辑）。"""

from __future__ import annotations

from unittest.mock import patch

from iris.trello.client import (
    _is_ipv4,
    _resolve_via_dns,
    TRELLO_API_BASE,
    _CUSTOM_DNS,
)


class TestIsIpv4:
    def test_valid_ip(self):
        assert _is_ipv4("192.168.1.1")

    def test_another_valid_ip(self):
        assert _is_ipv4("10.0.0.1")

    def test_invalid_ip_with_letters(self):
        assert not _is_ipv4("abc.def.ghi.jkl")

    def test_empty_string(self):
        assert not _is_ipv4("")

    def test_hostname_not_ip(self):
        assert not _is_ipv4("api.trello.com")

    def test_ipv6_not_ipv4(self):
        assert not _is_ipv4("::1")


class TestResolveViaDns:
    def test_returns_ip_from_dig(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "142.250.80.14\n"
            mock_run.return_value.returncode = 0
            # 清除缓存以确保 fresh call
            from iris.trello.client import _dns_cache, _dns_lock
            with _dns_lock:
                _dns_cache.pop("api.trello.com", None)
            result = _resolve_via_dns("api.trello.com")
            assert _is_ipv4(result)

    def test_returns_hostname_on_dig_failure(self):
        with patch("subprocess.run", side_effect=OSError("dig not found")):
            from iris.trello.client import _dns_cache, _dns_lock
            with _dns_lock:
                _dns_cache.pop("api.trello.com", None)
            result = _resolve_via_dns("api.trello.com")
            assert result == "api.trello.com"

    def test_uses_custom_dns_server(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "1.2.3.4\n"
            mock_run.return_value.returncode = 0
            _resolve_via_dns("unique-test.example.com", dns_server="1.1.1.1")
            args = mock_run.call_args[0][0]
            assert "@1.1.1.1" in args

    def test_caches_result(self):
        """同主机名在 TTL 内应返回缓存结果。"""
        from iris.trello.client import _dns_cache, _dns_lock
        with _dns_lock:
            _dns_cache.clear()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "5.6.7.8\n"
            mock_run.return_value.returncode = 0
            result1 = _resolve_via_dns("cache-test.example.com")
            result2 = _resolve_via_dns("cache-test.example.com")
            assert result1 == result2
            assert mock_run.call_count == 1  # 第二次走缓存


class TestTrelloApiBase:
    def test_base_url_is_correct(self):
        assert TRELLO_API_BASE == "https://api.trello.com/1"

    def test_custom_dns_is_google(self):
        assert _CUSTOM_DNS == "8.8.8.8"
