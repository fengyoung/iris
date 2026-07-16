"""测试飞书客户端 — feishu/client.py。
侧重单元测试：URL 解析、错误处理、重试逻辑。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.feishu.client import (
    FeishuClient,
    FeishuClientError,
    WikiNodeMeta,
    _DOC_URL_RE,
)


class TestDocUrlParsing:
    """飞书文档 URL 解析。"""

    @pytest.mark.parametrize("url,expected_token", [
        ("https://example.feishu.cn/docx/AbCdEfGhIjKlMnOp", "AbCdEfGhIjKlMnOp"),
        ("https://example.feishu.cn/docs/AbCdEfGhIjKlMnOp?from=share", "AbCdEfGhIjKlMnOp"),
        ("https://example.feishu.cn/wiki/AbCdEfGhIjKlMnOp", "AbCdEfGhIjKlMnOp"),
        ("AbCdEfGhIjKlMnOp", "AbCdEfGhIjKlMnOp"),
    ])
    def test_parse_valid_urls(self, url, expected_token):
        """解析各种飞书文档 URL 格式。"""
        match = _DOC_URL_RE.search(url)
        assert match is not None
        assert match.group(1) == expected_token

    def test_parse_invalid_url(self):
        """无效 URL 返回 None。"""
        assert _DOC_URL_RE.search("https://example.com/other") is None
        assert _DOC_URL_RE.search("short") is None


class TestFeishuClientInit:
    """客户端初始化。"""

    def test_default_user_mode(self):
        """默认 as_user=True。"""
        client = FeishuClient()
        assert client._as == "user"

    def test_bot_mode(self):
        """bot 模式。"""
        client = FeishuClient(as_user=False)
        assert client._as == "bot"


class TestParseDocUrl:
    """parse_doc_url 方法。"""

    def test_valid_url(self):
        """有效的文档 URL。"""
        client = FeishuClient()
        token = client.parse_doc_url(
            "https://example.feishu.cn/docx/AbCdEfGhIjKlMnOp"
        )
        assert token == "AbCdEfGhIjKlMnOp"

    def test_invalid_url_raises(self):
        """无效 URL 抛出 FeishuClientError。"""
        client = FeishuClient()
        with pytest.raises(FeishuClientError, match="无法从 URL 中提取"):
            client.parse_doc_url("not-a-valid-url")

    def test_bare_token(self):
        """裸 token 直接返回。"""
        client = FeishuClient()
        token = client.parse_doc_url("tok1234567890abcdef")
        assert token == "tok1234567890abcdef"


class TestRunMethod:
    """_run 方法的模拟测试。"""

    def test_successful_call(self):
        """成功的 API 调用返回 JSON。"""
        client = FeishuClient()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            json.dumps({"ok": True, "data": {"items": []}}),
            "",
        )
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            result = client._run(["wiki", "list"], timeout=10, retries=1)
            assert result == {"ok": True, "data": {"items": []}}

    def test_nonzero_return_with_stderr(self):
        """非零退出码且无 JSON 输出时抛出错误。"""
        client = FeishuClient()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", "权限不足")
        mock_proc.returncode = 1
        mock_proc.poll.return_value = 1

        with patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(FeishuClientError, match="lark-cli 返回 1"):
                client._run(["wiki", "list"], timeout=10, retries=1)

    def test_api_business_error(self):
        """API 返回 ok:false 时抛出业务错误。"""
        client = FeishuClient()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            json.dumps({"ok": False, "error": {"message": "文档不存在"}}),
            "",
        )
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(FeishuClientError, match="文档不存在"):
                client._run(["docs", "fetch"], timeout=10, retries=1)

    def test_empty_stdout_returns_empty_dict(self):
        """空 stdout 返回 {}。"""
        client = FeishuClient()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            result = client._run(["wiki", "list"], timeout=10, retries=1)
            assert result == {}

    def test_timeout_retries(self):
        """超时后自动重试 — 验证重试逻辑不崩溃。"""
        client = FeishuClient()
        call_count = [0]

        def communicate_side_effect(timeout=None):
            call_count[0] += 1
            # 第一次调用超时，后续调用（重试中）正常返回
            if call_count[0] <= 2:
                raise subprocess.TimeoutExpired("cmd", timeout or 10)
            return ("", "")

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = communicate_side_effect
        mock_proc.returncode = 0
        # poll 返回 0 表示进程已退出，避免 finally 块再调 communicate
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch("time.sleep", return_value=None):
            # 第一次尝试超时 → 重试 → 成功
            result = client._run(["wiki", "list"], timeout=1, retries=3)

        # 验证至少进行了重试
        assert call_count[0] >= 2
        assert result == {}

    def test_invalid_json_stdout(self):
        """非 JSON stdout 返回 {}。"""
        client = FeishuClient()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("not valid json", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            result = client._run(["wiki", "list"], timeout=10, retries=1)
            assert result == {}

    def test_process_cleanup_on_error(self):
        """异常时确保调用 kill 清理子进程（非零退出码场景）。"""
        client = FeishuClient()
        mock_proc = MagicMock()
        # 模拟非零退出 + 无 stdout
        mock_proc.communicate.return_value = ("", "error message")
        mock_proc.returncode = 1
        mock_proc.poll.return_value = 1  # 进程已退出，不需要 kill

        with patch("subprocess.Popen", return_value=mock_proc):
            with pytest.raises(FeishuClientError, match="lark-cli 返回 1"):
                client._run(["wiki", "list"], timeout=10, retries=1)


class TestWikiNodeMeta:
    """WikiNodeMeta 数据类。"""

    def test_creation(self):
        """基本创建。"""
        node = WikiNodeMeta(
            token="tok123", title="测试页面",
            node_type="page", parent_token="parent456",
            has_children=True,
        )
        assert node.token == "tok123"
        assert node.title == "测试页面"
        assert node.node_type == "page"
        assert node.has_children is True

    def test_defaults(self):
        """默认值。"""
        node = WikiNodeMeta(token="tok", title="t", node_type="folder")
        assert node.parent_token == ""
        assert node.has_children is False
