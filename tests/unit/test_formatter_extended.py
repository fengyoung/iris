"""output/formatter.py 扩展单元测试 — 覆盖 _add_kv 及各 _fmt_* 内部函数未测试分支。"""

from __future__ import annotations

from iris.output.formatter import (
    _add_kv,
    _fmt_diagnose,
    _fmt_status,
    _fmt_agent_spec,
    _fmt_memory_status,
    _fmt_memory_list,
    _fmt_memory_delete,
    _fmt_memory_export,
    _fmt_memory_import,
    _fmt_working,
    _fmt_route_model,
    _fmt_process,
    _fmt_search,
    _fmt_ask,
    _fmt_build_report,
    _fmt_build_mindmap,
    _fmt_scan_source,
    _fmt_build_chunks,
    _fmt_build_vector_index,
    _fmt_discover_wiki,
    _fmt_build_wiki,
    _fmt_build_wiki_nav,
    _fmt_wiki_pipeline,
    _fmt_wiki_lint,
    _fmt_transcribe_meeting,
    _fmt_batch_transcribe,
    _fmt_daily_start,
    _fmt_reminders,
    _fmt_build_asr_prompt,
    format_payload,
)


# ─────────────────────────────────────────────────────────────
# _add_kv（工具函数）
# ─────────────────────────────────────────────────────────────

class TestAddKV:
    def test_adds_line_when_value_present(self):
        lines = []
        _add_kv(lines, "状态", "正常")
        assert len(lines) == 1
        assert "状态：正常" in lines[0]

    def test_skips_none_value(self):
        lines = []
        _add_kv(lines, "状态", None)
        assert lines == []

    def test_skips_but_not_other_values(self):
        lines = ["已有行"]
        _add_kv(lines, "跳过", None)
        _add_kv(lines, "保留", "有值")
        assert len(lines) == 2

    def test_zero_value_rendered(self):
        """0 不是 None，应渲染。"""
        lines = []
        _add_kv(lines, "数量", 0)
        assert len(lines) == 1
        assert "0" in lines[0]

    def test_false_value_rendered(self):
        """False 不是 None，应渲染。"""
        lines = []
        _add_kv(lines, "启用", False)
        assert len(lines) == 1


# ─────────────────────────────────────────────────────────────
# 诊断 & 状态
# ─────────────────────────────────────────────────────────────

class TestFmtDiagnose:
    def test_basic(self):
        result = _fmt_diagnose({"project_root": "/tmp", "data_source_exists": True})
        assert "诊断结果" in result
        assert "/tmp" in result

    def test_with_log_file(self):
        result = _fmt_diagnose({"log_file": "/var/log/iris.log"})
        assert "日志文件" in result


class TestFmtStatus:
    def test_basic(self):
        result = _fmt_status({"data_source_exists": True, "base_model_has_key": True})
        assert "项目状态" in result

    def test_with_suggestion(self):
        result = _fmt_status({"suggested_next_action": "运行 daily-start"})
        assert "建议操作" in result
        assert "daily-start" in result


# ─────────────────────────────────────────────────────────────
# Agent Spec
# ─────────────────────────────────────────────────────────────

class TestFmtAgentSpec:
    def test_with_commands(self):
        payload = {
            "protocol_version": "3.14",
            "commands": {
                "search": {"purpose": "检索知识库", "inputs": ["query"], "outputs": ["hits"]},
                "ask": {"purpose": "问答", "inputs": ["question"], "outputs": []},
            },
        }
        result = _fmt_agent_spec(payload)
        assert "3.14" in result
        assert "search" in result
        assert "ask" in result
        assert "检索知识库" in result

    def test_empty_commands(self):
        result = _fmt_agent_spec({"commands": {}})
        assert "Agent 协议" in result


# ─────────────────────────────────────────────────────────────
# 记忆
# ─────────────────────────────────────────────────────────────

class TestFmtMemoryStatus:
    def test_basic(self):
        result = _fmt_memory_status({
            "profile_updated_at": "2026-07-01",
            "likes_count": 5,
            "dislikes_count": 2,
        })
        assert "记忆状态" in result
        assert "2026-07-01" in result

    def test_with_corrections(self):
        result = _fmt_memory_status({
            "correction_count": 3,
            "sample_corrections": [
                {"concept": "C1", "preferred": "P1"},
                {"concept": "C2", "preferred": "P2"},
            ],
        })
        assert "C1" in result
        assert "P1" in result


class TestFmtMemoryList:
    def test_with_profile(self):
        payload = {
            "profile": {
                "user_preferences": {
                    "likes": ["喜欢1", "喜欢2"],
                    "dislikes": ["不喜欢1"],
                },
            },
            "corrections": {"items": [], "correction_count": 0},
        }
        result = _fmt_memory_list(payload)
        assert "记忆列表" in result
        assert "喜欢1" in result

    def test_with_corrections(self):
        payload = {
            "corrections": {
                "items": [
                    {"concept": "AI", "preferred": "人工智能", "update_count": 3},
                ],
                "correction_count": 1,
            },
        }
        result = _fmt_memory_list(payload)
        assert "纠正规则" in result
        assert "AI" in result
        assert "人工智能" in result

    def test_no_profile_no_corrections(self):
        result = _fmt_memory_list({})
        assert "记忆列表" in result


class TestFmtMemoryDelete:
    def test_deleted(self):
        result = _fmt_memory_delete({"concept": "AI", "deleted": True})
        assert "成功" in result
        assert "AI" in result

    def test_not_found(self):
        result = _fmt_memory_delete({"concept": "AI", "deleted": False})
        assert "未找到" in result


class TestFmtMemoryExport:
    def test_basic(self):
        result = _fmt_memory_export({"output_file": "/tmp/memory.json"})
        assert "/tmp/memory.json" in result


class TestFmtMemoryImport:
    def test_basic(self):
        result = _fmt_memory_import({
            "replace": True,
            "profile_updated": True,
            "corrections_updated": False,
            "correction_count": 10,
        })
        assert "记忆导入" in result


# ─────────────────────────────────────────────────────────────
# 工作上下文
# ─────────────────────────────────────────────────────────────

class TestFmtWorking:
    def test_all_fields(self):
        result = _fmt_working({
            "current_task": "测试任务",
            "pending_items": ["待办1", "待办2"],
            "recent_changes": ["变更1"],
            "notes": "备注内容",
            "updated_at": "2026-07-30",
        })
        assert "测试任务" in result
        assert "待办1" in result
        assert "变更1" in result
        assert "备注内容" in result

    def test_empty(self):
        result = _fmt_working({})
        assert "工作上下文" in result


# ─────────────────────────────────────────────────────────────
# 模型路由
# ─────────────────────────────────────────────────────────────

class TestFmtRouteModel:
    def test_basic(self):
        result = _fmt_route_model({
            "selected_role": "adv_model",
            "fallback_role": "base_model",
            "matched_rule": "multimodal",
        })
        assert "adv_model" in result
        assert "base_model" in result


# ─────────────────────────────────────────────────────────────
# 复杂输入处理
# ─────────────────────────────────────────────────────────────

class TestFmtProcess:
    def test_basic(self):
        result = _fmt_process({"query": "分析这张图", "is_complex": True})
        assert "复杂输入处理" in result

    def test_with_stages(self):
        result = _fmt_process({
            "query": "分析",
            "stage1_prompt": "指令内容" * 50,
            "stage2_output": "多模态输出" * 50,
            "stage3_output": "最终输出" * 50,
        })
        assert "阶段 1" in result
        assert "阶段 2" in result
        assert "阶段 3" in result


# ─────────────────────────────────────────────────────────────
# 检索 & 问答
# ─────────────────────────────────────────────────────────────

class TestFmtSearch:
    def test_with_hits_and_wiki(self):
        payload = {
            "query": "搜索测试",
            "query_plan": {"question_type": "factual", "query_intent": "检索"},
            "total_hits": 5,
            "hits": [
                {"title": "结果1", "relative_path": "path/to/doc.md", "score": 0.9,
                 "matched_terms": ["term1", "term2"], "line_start": 10},
            ],
            "wiki_hits": [
                {"title": "Wiki页1", "relative_path": "01-领域/test.md"},
            ],
            "explanations": ["说明1"],
        }
        result = _fmt_search(payload)
        assert "检索结果" in result
        assert "搜索测试" in result
        assert "结果1" in result
        assert "Wiki页1" in result

    def test_empty_hits(self):
        result = _fmt_search({"query": "无结果", "total_hits": 0, "hits": []})
        assert "检索结果" in result

    def test_hit_without_matched_terms(self):
        result = _fmt_search({
            "query": "q",
            "total_hits": 1,
            "hits": [{"title": "T", "relative_path": "p", "line_start": 1, "score": 0.5}],
        })
        assert "T" in result


class TestFmtAsk:
    def test_with_answer(self):
        result = _fmt_ask({"question": "什么是AI", "mode": "llm", "answer": "人工智能"})
        assert "问答结果" in result
        assert "人工智能" in result

    def test_no_answer(self):
        result = _fmt_ask({"question": "问题", "mode": "search"})
        assert "问答结果" in result


# ─────────────────────────────────────────────────────────────
# 报告 & 思维导图
# ─────────────────────────────────────────────────────────────

class TestFmtBuildReport:
    def test_with_markdown(self):
        result = _fmt_build_report({"query": "主题", "mode": "deep", "markdown": "# 报告内容"})
        assert "分析报告" in result
        assert "# 报告内容" in result


class TestFmtBuildMindmap:
    def test_basic(self):
        result = _fmt_build_mindmap({
            "query": "思维导图主题",
            "mode": "deep",
            "format": "mermaid",
            "markdown": "```mermaid\ngraph TD\n```",
        })
        assert "思维导图" in result
        assert "mermaid" in result

    def test_with_output_files(self):
        result = _fmt_build_mindmap({
            "query": "主题",
            "output_file": "/tmp/mindmap.md",
            "xmind_file": "/tmp/mindmap.xmind",
        })
        assert "/tmp/mindmap.md" in result
        assert "/tmp/mindmap.xmind" in result


# ─────────────────────────────────────────────────────────────
# 扫描 & 切块
# ─────────────────────────────────────────────────────────────

class TestFmtScanSource:
    def test_with_sources_list(self):
        result = _fmt_scan_source({
            "sources": [
                {"source_name": "源1", "scanned_at": "2026-07-01", "document_count": 10},
                {"source_name": "源2", "scanned_at": "2026-07-02", "document_count": 5},
            ],
        })
        assert "源1" in result
        assert "源2" in result

    def test_fallback_to_self(self):
        """sources 不存在时回退到自身。"""
        result = _fmt_scan_source({"source_name": "单源", "document_count": 3})
        assert "单源" in result

    def test_with_summary_path(self):
        result = _fmt_scan_source({
            "sources": [{"source_name": "源", "summary_path": "/tmp/summary.json"}],
        })
        assert "/tmp/summary.json" in result


class TestFmtBuildChunks:
    def test_basic(self):
        result = _fmt_build_chunks({
            "sources": [{
                "source_name": "主源",
                "document_count": 20,
                "chunk_count": 100,
                "build_stats": {"reused_documents": 15, "rebuilt_documents": 5},
            }],
        })
        assert "切块摘要" in result
        assert "主源" in result
        assert "100" in result

    def test_with_summary_path(self):
        result = _fmt_build_chunks({
            "sources": [{"source_name": "源", "build_stats": {}, "summary_path": "/tmp/chunks.json"}],
        })
        assert "/tmp/chunks.json" in result


class TestFmtBuildVectorIndex:
    def test_ok_and_error(self):
        result = _fmt_build_vector_index({
            "results": [
                {"source": "源1", "status": "ok", "indexed": 500},
                {"source": "源2", "status": "error", "reason": "模型不匹配"},
                {"source": "源3", "status": "skipped"},
            ],
        })
        assert "向量索引" in result
        assert "✅" in result
        assert "❌" in result
        assert "500" in result
        assert "模型不匹配" in result


# ─────────────────────────────────────────────────────────────
# Wiki
# ─────────────────────────────────────────────────────────────

class TestFmtDiscoverWiki:
    def test_mixed_types(self):
        payload = {
            "items": [
                {"title": "领域1", "page_type": "domain", "score": 15, "has_wiki": True, "wiki_stale": False},
                {"title": "概念1", "page_type": "concept", "score": 10, "has_wiki": False, "wiki_stale": False},
                {"title": "项目1", "page_type": "project", "score": 12, "has_wiki": True, "wiki_stale": True},
                {"title": "人物1", "page_type": "person", "score": 8, "has_wiki": False, "wiki_stale": False},
            ],
        }
        result = _fmt_discover_wiki(payload)
        assert "领域" in result
        assert "概念" in result
        assert "项目" in result
        assert "人物" in result
        assert "领域1" in result
        assert "⚠️" in result  # stale marker

    def test_export_paths(self):
        result = _fmt_discover_wiki({
            "items": [],
            "export_jsonl": "/tmp/export.jsonl",
            "export_review": "/tmp/review.json",
            "export_review_md": "/tmp/review.md",
        })
        assert "/tmp/export.jsonl" in result


class TestFmtBuildWiki:
    def test_basic(self):
        result = _fmt_build_wiki({
            "title": "测试页面",
            "page_type": "concept",
            "output_path": "/tmp/wiki/概念-测试.md",
        })
        assert "测试页面" in result
        assert "concept" in result

    def test_with_write_result(self):
        result = _fmt_build_wiki({
            "title": "页面",
            "write_result": {"action": "created", "backup_path": "/tmp/backup.md"},
            "markdown": "# 内容\n正文",
        })
        assert "created" in result
        assert "/tmp/backup.md" in result
        assert "# 内容" in result

    def test_write_result_no_backup(self):
        result = _fmt_build_wiki({
            "title": "页面",
            "write_result": {"action": "updated"},
        })
        assert "updated" in result


class TestFmtBuildWikiNav:
    def test_basic(self):
        result = _fmt_build_wiki_nav({"pages_written": 42, "nav_path": "/tmp/nav.md"})
        assert "42" in result
        assert "/tmp/nav.md" in result


class TestFmtWikiPipeline:
    def test_basic(self):
        result = _fmt_wiki_pipeline({
            "candidate_count": 10,
            "export_jsonl": "/tmp/candidates.jsonl",
            "next_step": "审核后执行 build-wiki",
        })
        assert "10" in result
        assert "/tmp/candidates.jsonl" in result
        assert "build-wiki" in result


# ─────────────────────────────────────────────────────────────
# Wiki Lint（最复杂的格式化函数）
# ─────────────────────────────────────────────────────────────

class TestFmtWikiLint:
    def test_minimal(self):
        result = _fmt_wiki_lint({"page_count": 50})
        assert "Wiki 健康检查" in result
        assert "50" in result

    def test_by_type(self):
        result = _fmt_wiki_lint({
            "page_count": 20,
            "by_type": {"domain": 5, "concept": 3, "project": 7, "person": 5},
        })
        assert "domain=5页" in result

    def test_page_quality(self):
        result = _fmt_wiki_lint({
            "page_count": 10,
            "no_frontmatter_count": 2,
            "no_summary_count": 1,
            "zero_outbound_count": 3,
            "stale_count": 1,
            "old_page_count": 4,
        })
        assert "页面质量" in result

    def test_link_quality(self):
        result = _fmt_wiki_lint({
            "page_count": 10,
            "raw_broken_count": 5,
            "broken_count": 2,
            "excluded_broken": 3,
            "orphan_count": 1,
        })
        assert "链接质量" in result

    def test_with_list_items(self):
        """各问题列表正确渲染。"""
        result = _fmt_wiki_lint({
            "page_count": 10,
            "orphan_pages": ["孤立页1", "孤立页2"],
            "broken_links": ["断裂1"],
            "stale_pages": ["草稿1"],
            "old_pages": ["旧页1"],
            "no_frontmatter": ["缺fm1"],
            "no_summary": [],
            "zero_outbound": [],
        })
        assert "孤立页1" in result
        assert "断裂1" in result
        assert "草稿1" in result

    def test_index_quality(self):
        result = _fmt_wiki_lint({
            "page_count": 10,
            "index_quality": {
                "source_documents": 100,
                "chunked_documents": 95,
                "chunk_coverage_pct": 95,
                "total_chunks": 500,
                "vector_index_exists": True,
                "vector_index_size_kb": 1024,
                "last_scanned": "2026-07-30T10:00:00+08:00",
            },
        })
        assert "索引质量" in result
        assert "95%" in result
        assert "✅" in result

    def test_index_missing(self):
        result = _fmt_wiki_lint({
            "page_count": 5,
            "index_quality": {"vector_index_exists": False},
        })
        assert "❌" in result

    def test_content_quality_with_duplicates(self):
        result = _fmt_wiki_lint({
            "page_count": 10,
            "content_quality": {
                "info_density": {
                    "avg_words_per_section": 120,
                    "thin_pages": ["薄页1", "薄页2"],
                },
                "duplicate_count": 2,
                "duplicates": [
                    {"pair": ["页A", "页B"], "similarity": "35%"},
                    {"pair": ["页C", "页D"], "similarity": "32%"},
                ],
            },
        })
        assert "内容质量" in result
        assert "120" in result
        assert "薄页1" in result
        assert "页A" in result
        assert "页B" in result


# ─────────────────────────────────────────────────────────────
# 会议
# ─────────────────────────────────────────────────────────────

class TestFmtTranscribeMeeting:
    def test_basic(self):
        result = _fmt_transcribe_meeting({
            "audio_file": "/tmp/meeting.mp3",
            "transcript_file": "/tmp/transcript.txt",
            "word_count": 5000,
            "wiki_pages_loaded": 3,
            "output_file": "/tmp/minutes.md",
        })
        assert "会议纪要" in result
        assert "/tmp/meeting.mp3" in result
        assert "5000" in result


class TestFmtBatchTranscribe:
    def test_basic(self):
        result = _fmt_batch_transcribe({
            "total": 5,
            "succeeded": 4,
            "failed": 1,
            "results": [
                {"file": "会议1.mp3", "status": "ok"},
                {"file": "会议2.mp3", "status": "error", "reason": "转写失败"},
            ],
        })
        assert "批量会议纪要" in result
        assert "5" in result
        assert "✅" in result
        assert "❌" in result


# ─────────────────────────────────────────────────────────────
# Daily Start & Reminders
# ─────────────────────────────────────────────────────────────

class TestFmtDailyStart:
    def test_basic(self):
        result = _fmt_daily_start({
            "scan": [{"source_name": "s1", "document_count": 10}],
            "chunks": [{"source_name": "s1", "chunk_count": 50}],
        })
        assert "日常启动" in result
        assert "10" in result
        assert "50" in result

    def test_with_reminders(self):
        result = _fmt_daily_start({
            "scan": [{"document_count": 5}],
            "chunks": [{"chunk_count": 20}],
            "reminders": {
                "signal_count": 2,
                "status": "ok",
                "signals": [
                    {"type": "weekly_report_missing", "detail": "成员A 连续2周无周报"},
                    {"type": "category_inactive", "detail": "栏目06 超过30天无内容"},
                ],
            },
        })
        assert "主动提醒" in result
        assert "周报缺失" in result
        assert "栏目断供" in result

    def test_no_reminders(self):
        result = _fmt_daily_start({
            "scan": [{"document_count": 5}],
            "chunks": [{"chunk_count": 20}],
            "reminders": {"signal_count": 0},
        })
        assert "日常启动" in result


class TestFmtReminders:
    def test_skipped(self):
        result = _fmt_reminders({"status": "skipped", "reason": "距上次执行不足24小时"})
        assert "⏭" in result
        assert "24小时" in result

    def test_error(self):
        result = _fmt_reminders({"status": "error", "reason": "采集异常"})
        assert "❌" in result
        assert "采集异常" in result

    def test_no_signals(self):
        result = _fmt_reminders({"status": "ok", "signals": []})
        assert "✅" in result
        assert "无异常信号" in result

    def test_with_signals(self):
        result = _fmt_reminders({
            "status": "ok",
            "signals": [
                {"type": "project_stalled", "detail": "项目X超过60天无更新"},
            ],
        })
        assert "⚠️" in result
        assert "项目停滞" in result

    def test_unknown_signal_type(self):
        """未知信号类型回退到原始 type 值。"""
        result = _fmt_reminders({
            "status": "ok",
            "signals": [{"type": "unknown_type_xyz", "detail": "某未知信号"}],
        })
        assert "unknown_type_xyz" in result


# ─────────────────────────────────────────────────────────────
# ASR Prompt
# ─────────────────────────────────────────────────────────────

class TestFmtBuildAsrPrompt:
    def test_basic(self):
        result = _fmt_build_asr_prompt({
            "page_count": 15,
            "prompt_chars": 8000,
            "sections": ["页1", "页2", "页3"],
        })
        assert "ASR 校正提示词" in result
        assert "15" in result
        assert "8000" in result

    def test_many_sections(self):
        sections = [f"页{i}" for i in range(20)]
        result = _fmt_build_asr_prompt({
            "page_count": 20,
            "prompt_chars": 50000,
            "sections": sections,
        })
        assert "... 共 20 个" in result


# ─────────────────────────────────────────────────────────────
# format_payload 集成
# ─────────────────────────────────────────────────────────────

class TestFormatPayloadIntegration:
    def test_all_commands_no_crash(self):
        """所有注册的命令不因空 payload 抛异常。"""
        commands = [
            "diagnose", "status", "agent-spec",
            "memory-status", "memory-list", "memory-delete", "memory-export", "memory-import",
            "working-set", "working-show", "working-clear",
            "route-model", "check-config", "process",
            "transcribe-meeting", "batch-transcribe", "daily-start",
            "search", "ask", "build-report", "build-mindmap",
            "scan-source", "build-chunks", "build-vector-index",
            "discover-wiki", "discover-wiki-auto",
            "build-wiki", "build-wiki-nav", "wiki-pipeline", "wiki-lint",
            "build-asr-prompt", "reminders",
        ]
        # 少量命令输出不包含 "## " 标题（如 memory-delete / memory-export）
        no_title_commands = {"memory-delete", "memory-export", "check-config", "reminders"}
        for cmd in commands:
            result = format_payload(cmd, {})
            assert isinstance(result, str), f"{cmd} 返回非字符串: {type(result)}"
            if cmd not in no_title_commands:
                assert "## " in result or result == "", \
                    f"{cmd} 未生成预期输出: {result[:50]}"

    def test_unknown_command_empty(self):
        assert format_payload("nonexistent", {"key": "val"}) == ""
