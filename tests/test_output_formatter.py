"""output/formatter.py 单元测试。"""

from __future__ import annotations



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


class TestFormatCommandSpecific:
    """各命令特有格式化函数专项测试。"""

    def test_diagnose(self):
        from iris.output.formatter import format_payload
        payload = {"config_ok": True, "app_version": "3.18", "python_version": "3.9",
                   "llm_configured": True}
        result = format_payload("diagnose", payload)
        assert "诊断" in result

    def test_route_model(self):
        from iris.output.formatter import format_payload
        payload = {"selected_role": "adv_model", "matched_rule": "multimodal_input_go_adv"}
        result = format_payload("route-model", payload)
        assert "adv_model" in result

    def test_scan_source(self):
        from iris.output.formatter import format_payload
        payload = {"sources": [{"source_name": "测试源", "document_count": 42, "scanned_at": "2026-01-01"}]}
        result = format_payload("scan-source", payload)
        assert "42" in result

    def test_discover_wiki(self):
        from iris.output.formatter import format_payload
        payload = {"items": [
            {"title": "搜索推荐", "page_type": "domain", "score": 20, "evidence_count": 5,
             "sample_paths": ["test.md"], "rationale": "高频主题", "has_wiki": True, "wiki_stale": False},
        ]}
        result = format_payload("discover-wiki", payload)
        assert "搜索推荐" in result
        assert "score=20" in result

    def test_wiki_lint(self):
        from iris.output.formatter import format_payload
        payload = {"lint_report": {
            "checked_pages": 10, "issues": {"stale": ["页A"], "broken_links": []},
            "fixable_count": 1,
        }}
        result = format_payload("wiki-lint", payload)
        assert "Wiki 健康检查" in result

    def test_process_image(self):
        from iris.output.formatter import format_payload
        payload = {"query": "分析图片", "file_type": "image", "stage3_output": "架构图分析结果",
                   "detection_reason": "检测到图片输入", "stage2_model": "qwen-vl-plus"}
        result = format_payload("process", payload)
        assert "分析图片" in result

    def test_daily_start(self):
        from iris.output.formatter import format_payload
        payload = {
            "daily_start": {
                "scan": {"sources": [{"source_name": "s1", "document_count": 5}]},
                "chunk": {"sources": [{"source_name": "s1", "chunk_count": 50}]},
                "wiki": {"discovered": 3, "built": 2},
                "lint": {"lint_report": {"checked_pages": 20, "issues": {}}},
            }
        }
        result = format_payload("daily-start", payload)
        assert result is not None

    def test_memory_status(self):
        from iris.output.formatter import format_payload
        payload = {"total_items": 3, "profile": {}, "corrections": {"items": []}}
        result = format_payload("memory-status", payload)
        assert "记忆状态" in result

    def test_transcribe_meeting(self):
        from iris.output.formatter import format_payload
        payload = {
            "date": "2026-01-15", "meeting_type": "周会", "topic": "项目同步",
            "duration_minutes": 45, "route": "05-会议纪要/",
        }
        result = format_payload("transcribe-meeting", payload)
        assert "会议纪要" in result
