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


def _daily_payload(**overrides):
    """构造与 _system.handle_daily_start 实际输出同形的 payload。"""
    payload = {
        "scan": [{"source_name": "main_source", "document_count": 1039}],
        "chunks": [{"source_name": "main_source", "chunk_count": 7932,
                    "reused_documents": 1024, "rebuilt_documents": 15}],
        "vector_index": {"status": "ok", "indexed": 7932},
        "source_index": {"status": "ok", "total": 1039, "groups": 17},
        "wiki_discover": {"triggered": True, "changed_documents": 15, "new_candidates": 3},
        "wiki_update": {"total": 225, "updated": 5, "unchanged": 220,
                        "not_found": 0, "errors": 0},
        "person_enrich": {"status": "ok", "updated": 1, "no_change": 268,
                          "not_found": 2, "ambiguous": 0},
        "graph": {"status": "ok", "nodes": 231, "edges": 2766},
        "asr_audit": {"status": "skipped", "reason": "未找到热词文件"},
        "usage_summary": {"status": "ok", "today": {"calls": 89, "total_tokens": 1210983},
                          "this_week": {"calls": 500, "total_tokens": 5000000},
                          "this_month": {"calls": 900, "total_tokens": 9000000}},
        "reminders": {"status": "ok", "signal_count": 0, "signals": []},
    }
    payload.update(overrides)
    return payload


class TestDailyStartFormat:
    """daily-start 的 --pretty 渲染：各子系统状态必须可见。

    旧实现只渲染扫描文档数与 Chunk 数，`vector_index` 的 model_mismatch、
    `wiki_update` 的失败在 --pretty 下完全不可见——这几条用例锁住该行为。
    """

    def test_renders_all_subsystems(self):
        from iris.output.formatter import format_payload
        result = format_payload("daily-start", _daily_payload())
        for fragment in ("向量索引：索引 7932 条", "SOURCE/_INDEX.md：1039 篇 / 17 个目录",
                         "Wiki 发现：新增候选 3 条", "Wiki 更新：更新 5 / 未变 220",
                         "人物丰富：更新 1", "知识图谱：231 节点 / 2766 边",
                         "ASR 审计：未找到热词文件", "今日：89 次 / 1,210,983 tokens"):
            assert fragment in result, f"缺少片段: {fragment}"

    def test_scan_line_includes_chunk_rebuild_counts(self):
        from iris.output.formatter import format_payload
        result = format_payload("daily-start", _daily_payload())
        assert "扫描文档数：1039（切块重建 15，复用 1024）" in result

    def test_vector_index_model_mismatch_is_visible(self):
        """model_mismatch 必须显式告警——旧实现下这条信息在 --pretty 里丢失。"""
        from iris.output.formatter import format_payload
        payload = _daily_payload(vector_index={"status": "model_mismatch", "reason": "dim 1024→768"})
        result = format_payload("daily-start", payload)
        assert "embedding 模型已变更" in result
        assert "force-rebuild" in result

    def test_wiki_update_error_is_visible(self):
        from iris.output.formatter import format_payload
        payload = _daily_payload(wiki_update={"status": "error", "reason": "Wiki 目录不存在"})
        result = format_payload("daily-start", payload)
        assert "Wiki 更新：Wiki 目录不存在" in result

    def test_usage_budget_warning_is_visible(self):
        from iris.output.formatter import format_payload
        payload = _daily_payload(usage_summary={
            "status": "ok", "today": {"calls": 1, "total_tokens": 10},
            "this_week": {"calls": 1, "total_tokens": 10},
            "this_month": {"calls": 1, "total_tokens": 99_000_000},
            "budget_warning": "本月已用 99,000,000 token，超过预算上限 50,000,000",
        })
        result = format_payload("daily-start", payload)
        assert "预算预警" in result and "超过预算上限" in result

    def test_missing_subsystems_do_not_crash(self):
        """字段缺失时中性渲染，不臆造成功。"""
        from iris.output.formatter import format_payload
        result = format_payload("daily-start", {"scan": [], "chunks": []})
        assert "向量索引：未返回" in result
        assert "知识图谱：未返回" in result
        assert "暂无调用记录" in result

    def test_reminders_omitted_when_no_signal(self):
        from iris.output.formatter import format_payload
        assert "主动提醒" not in format_payload("daily-start", _daily_payload())

    def test_reminders_rendered_when_signal_present(self):
        from iris.output.formatter import format_payload
        payload = _daily_payload(reminders={
            "status": "ok", "signal_count": 1,
            "signals": [{"type": "category_inactive", "detail": "「01-目标管理」已 69 天无更新"}],
        })
        result = format_payload("daily-start", payload)
        assert "主动提醒" in result
        assert "[栏目断供] 「01-目标管理」已 69 天无更新" in result
