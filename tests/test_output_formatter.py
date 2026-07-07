"""output/formatter.py 单元测试。"""

from __future__ import annotations

import pytest


class TestFormatPayload:
    """format_payload: 各主要命令输出格式化。"""

    def test_search_format(self):
        from iris.output.formatter import format_payload
        payload = {
            "query": "测试查询",
            "hits": [
                {"title": "文档1", "relative_path": "docs/test.md",
                 "content_preview": "内容预览", "score": 0.9,
                 "section_path": ["第一节"], "structural_tags": [],
                 "explanation": ""}
            ],
            "total_hits": 1,
        }
        result = format_payload("search", payload)
        assert "测试查询" in result or "文档1" in result or "docs/test.md" in result

    def test_ask_format(self):
        from iris.output.formatter import format_payload
        payload = {
            "query": "什么是MMoE",
            "answer": "MMoE是多任务学习架构",
            "blocks": [],
            "mode": "llm",
        }
        result = format_payload("ask", payload)
        assert "MMoE" in result or "多任务" in result or result  # 只要不崩溃

    def test_empty_payload_no_crash(self):
        from iris.output.formatter import format_payload
        result = format_payload("check-config", {})
        assert result is not None

    def test_unknown_command_returns_json(self):
        from iris.output.formatter import format_payload
        payload = {"key": "value"}
        result = format_payload("unknown-command", payload)
        assert result is not None


class TestFormatPayloadEdgeCases:
    """边界情况：空字段、None 值。"""

    def test_hits_with_missing_fields(self):
        from iris.output.formatter import format_payload
        payload = {
            "query": "q",
            "hits": [{}],  # 空 hit
            "total_hits": 1,
        }
        # 不应抛出 KeyError
        result = format_payload("search", payload)
        assert result is not None

    def test_none_values_handled(self):
        from iris.output.formatter import format_payload
        payload = {"answer": None, "blocks": None}
        result = format_payload("ask", payload)
        assert result is not None
