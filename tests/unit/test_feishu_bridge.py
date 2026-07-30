"""feed/_feishu_bridge.py 测试 — 覆盖 raw_to_message, get_display_name, _run_lark_cli 纯逻辑。"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch, MagicMock

from iris.feed._feishu_bridge import (
    FeishuBridge,
    _run_lark_cli,
    IRIS_BOT_USER_ID,
)


class TestRawToMessage:
    def test_basic_text_message(self):
        raw = {
            "message_id": "msg_001",
            "chat_id": "oc_chat123",
            "chat_name": "测试群",
            "chat_type": "group",
            "sender": {"id": "ou_user1", "name": "张三"},
            "content": "这是一条测试消息",
            "msg_type": "text",
            "create_time": "2026-07-30 10:30",
        }
        msg = FeishuBridge.raw_to_message(raw)
        assert msg.msg_id == "msg_001"
        assert msg.sender_name == "张三"
        assert msg.content == "这是一条测试消息"
        assert msg.has_doc_link is False
        assert msg.doc_links == []

    def test_message_with_doc_link(self):
        raw = {
            "message_id": "msg_002",
            "chat_id": "oc_chat456",
            "chat_name": "",
            "chat_type": "group",
            "sender": {},
            "content": "请查看文档 https://xxx.feishu.cn/docx/ABC123 了解更多",
            "msg_type": "text",
            "create_time": "2026-07-30 10:00",
        }
        msg = FeishuBridge.raw_to_message(raw)
        assert msg.has_doc_link is True
        assert len(msg.doc_links) == 1
        assert "ABC123" in msg.doc_links[0]

    def test_message_with_wiki_and_sheet_links(self):
        raw = {
            "message_id": "msg_003",
            "chat_id": "oc_chat789",
            "chat_name": "项目群",
            "chat_type": "group",
            "sender": {"id": "ou_user2", "name": "李四"},
            "content": "见 wiki https://xxx.feishu.cn/wiki/WIKI123 和表格 https://xxx.feishu.cn/sheet/SHEET456",
            "msg_type": "text",
            "create_time": "",
        }
        msg = FeishuBridge.raw_to_message(raw)
        assert msg.has_doc_link is True
        assert len(msg.doc_links) == 2

    def test_no_sender(self):
        raw = {
            "message_id": "msg_004",
            "chat_id": "oc_chat000",
            "chat_name": "",
            "chat_type": "private",
            "content": "私聊消息",
            "msg_type": "text",
        }
        msg = FeishuBridge.raw_to_message(raw)
        assert msg.sender_id == ""
        assert msg.sender_name == ""

    def test_iso_format_create_time(self):
        raw = {
            "message_id": "msg_005",
            "chat_id": "oc_chat",
            "chat_name": "",
            "chat_type": "group",
            "sender": {},
            "content": "content",
            "msg_type": "text",
            "create_time": "2026-07-30T10:30:00+08:00",
        }
        msg = FeishuBridge.raw_to_message(raw)
        assert msg.send_time is not None


class TestGetDisplayName:
    def test_has_name(self):
        raw = {"name": "产品讨论群", "chat_id": "oc_123"}
        assert FeishuBridge.get_display_name(raw) == "产品讨论群"

    def test_no_name_fallback_to_chat_id(self):
        raw = {"chat_id": "oc_abcdefghijklmnopqrstuvwxyz"}
        name = FeishuBridge.get_display_name(raw)
        assert "oc_abcde" in name  # 前 16 字符
        assert "…" in name

    def test_empty_dict(self):
        name = FeishuBridge.get_display_name({})
        assert "…" in name


class TestRunLarkCli:
    def test_successful_call(self):
        with patch("iris.feed._feishu_bridge.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = json.dumps({"ok": True, "data": {"chats": []}})
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            result = _run_lark_cli(["im", "+chat-list"])
            assert result["ok"] is True

    def test_non_zero_exit(self):
        with patch("iris.feed._feishu_bridge.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = "权限不足"
            mock_run.return_value = mock_result

            result = _run_lark_cli(["im", "+chat-list"])
            assert result["ok"] is False

    def test_timeout(self):
        with patch("iris.feed._feishu_bridge.subprocess.run") as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)

            result = _run_lark_cli(["im", "+chat-list"])
            assert result["ok"] is False
            assert "超时" in result["error"]["message"]

    def test_invalid_json(self):
        with patch("iris.feed._feishu_bridge.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "not valid json"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            result = _run_lark_cli(["im", "+chat-list"])
            assert result["ok"] is False


class TestIrisBotUserId:
    def test_constant_exists(self):
        assert IRIS_BOT_USER_ID.startswith("ou_")
        assert len(IRIS_BOT_USER_ID) > 10
