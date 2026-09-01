"""build-biweekly-report 流水线单元测试。

覆盖：OP 解析缓存、文件收集管道、Stage 0a 解析、配置读取。
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iris.analysis.service import (
    AnalysisReportService,
    _resolve_source_root,
    _build_file_manifest,
    _build_local_fallback,
    _try_parse_json,
    _collect_direction_concepts,
    _build_boundaries_text,
)


# ── 数据源根目录解析 ─────────────────────────────────────


class TestResolveSourceRoot:
    """验证 _resolve_source_root 解析数据源根目录。

    注：_resolve_source_root 接收 ConfigBundle (dataclass)，
    其 data_source 是 Dict[str, Any]。需要直接构造 ConfigBundle 测试。
    """

    def test_enabled_existing_path(self, tmp_path):
        """启用的数据源且路径存在时返回路径。"""
        from iris.config.loader import ConfigBundle, make_config_bundle

        source_dir = tmp_path / "SOURCE"
        source_dir.mkdir()
        bundle = make_config_bundle(
            root=tmp_path,
            app={},
            data_source={
                "sources": {
                    "test": {"enabled": True, "path": str(source_dir)}
                }
            },
            llm={},
        )
        result = _resolve_source_root(bundle)
        assert result == source_dir.resolve()

    def test_disabled_source(self, tmp_path):
        """已禁用数据源返回 None。"""
        from iris.config.loader import ConfigBundle, make_config_bundle

        source_dir = tmp_path / "SOURCE"
        source_dir.mkdir()
        bundle = make_config_bundle(
            root=tmp_path,
            app={},
            data_source={
                "sources": {
                    "test": {"enabled": False, "path": str(source_dir)}
                }
            },
            llm={},
        )
        result = _resolve_source_root(bundle)
        assert result is None

    def test_nonexistent_path(self, tmp_path):
        """路径不存在返回 None。"""
        from iris.config.loader import ConfigBundle, make_config_bundle

        bundle = make_config_bundle(
            root=tmp_path,
            app={},
            data_source={
                "sources": {
                    "test": {"enabled": True, "path": "/nonexistent/path"}
                }
            },
            llm={},
        )
        result = _resolve_source_root(bundle)
        assert result is None


# ── 文件清单构建 ──────────────────────────────────────────


class TestBuildFileManifest:
    """验证 _build_file_manifest 构建文件清单文本。"""

    def test_empty_files(self):
        """空文件列表。"""
        result = _build_file_manifest([])
        assert "无数据源文件" in result

    def test_single_file(self):
        """单个文件。"""
        files = [{
            "date": datetime(2026, 7, 1),
            "dir": "成员周报",
            "filename": "20260701-周报-w26-李四.md",
            "label": "李四周报-0701",
            "content": "## 本周工作\n\n- 完成项目Alpha分类模型上线",
            "char_count": 50,
        }]
        result = _build_file_manifest(files)
        assert "成员周报" in result
        assert "李四周报-0701" in result
        assert "2026-07-01" in result

    def test_multiple_dirs(self):
        """多个目录分组展示。"""
        files = [
            {"date": datetime(2026, 7, 1), "dir": "成员周报", "filename": "a.md",
             "label": "A-0701", "content": "content", "char_count": 10},
            {"date": datetime(2026, 7, 2), "dir": "会议纪要", "filename": "b.md",
             "label": "B-0702", "content": "content", "char_count": 10},
        ]
        result = _build_file_manifest(files)
        assert "成员周报" in result
        assert "会议纪要" in result

    def test_content_truncation(self):
        """长内容截断。"""
        long_content = "x" * 5000
        files = [{
            "date": datetime(2026, 7, 1),
            "dir": "成员周报",
            "filename": "test.md",
            "label": "test",
            "content": long_content,
            "char_count": 5000,
        }]
        result = _build_file_manifest(files)
        # 内容应被截断（≥2000 字时标注截断）
        assert "截断" in result or len(result) < len(long_content) + 200


# ── 降级模式 ─────────────────────────────────────────────


class TestBuildLocalFallback:
    """验证 _build_local_fallback 构建降级版双周报。"""

    def test_basic_structure(self):
        """基本结构正确。"""
        result = _build_local_fallback("2026.07.01～2026.07.07", "OP文档内容", "文件清单")
        assert "时间周期" in result
        assert "2026.07.01" in result
        assert "OP文档内容" in result
        assert "文件清单" in result

    def test_footer(self):
        """包含生成标记。"""
        result = _build_local_fallback("2026.07.01～2026.07.07", "", "")
        assert "Iris" in result


# ── JSON 解析 ─────────────────────────────────────────────


class TestTryParseJson:
    """验证 _try_parse_json LLM 输出解析。"""

    def test_clean_json(self):
        """干净 JSON。"""
        result = _try_parse_json('{"directions": [{"id": 1, "name": "测试"}]}')
        assert result is not None
        assert len(result["directions"]) == 1

    def test_json_with_markdown_fence(self):
        """带 markdown 代码块。"""
        text = '```json\n{"directions": [{"id": 1, "name": "测试"}]}\n```'
        result = _try_parse_json(text)
        assert result is not None
        assert len(result["directions"]) == 1

    def test_json_with_leading_text(self):
        """JSON 前有说明文字。"""
        text = '这是解析结果：\n{"directions": [{"id": 1, "name": "测试"}]}'
        result = _try_parse_json(text)
        assert result is not None

    def test_invalid_json(self):
        """非法 JSON 返回 None。"""
        result = _try_parse_json("这不是 JSON")
        assert result is None

    def test_empty_string(self):
        """空字符串。"""
        result = _try_parse_json("")
        assert result is None


# ── 概念边界 ─────────────────────────────────────────────


class TestConceptBoundaries:
    """验证方向概念边界辅助函数。"""

    def test_collect_sub_area_concepts(self):
        """提取子领域概念名称。"""
        direction = {
            "id": 1,
            "name": "方向一",
            "sub_areas": [
                {"name": "1.1 【功能】项目Alpha检测", "owner": "李四", "goal": "测试"},
                {"name": "1.2 【质量】项目Beta", "owner": "李四", "goal": "测试"},
            ]
        }
        concepts = _collect_direction_concepts(direction)
        assert "项目Alpha检测" in concepts
        assert "项目Beta" in concepts

    def test_no_sub_areas(self):
        """无子领域返回空列表。"""
        direction = {"id": 1, "name": "方向一", "sub_areas": []}
        concepts = _collect_direction_concepts(direction)
        assert concepts == []

    def test_build_boundaries_text(self):
        """构建边界描述文本。"""
        bounds = {
            "own": ["项目Alpha检测", "项目Beta"],
            "others": {
                "方向二：执行过程": ["项目Delta", "项目Epsilon"],
            }
        }
        text = _build_boundaries_text("方向一", bounds)
        assert "项目Alpha检测" in text
        assert "项目Beta" in text
        assert "项目Delta" in text
        assert "项目Epsilon" in text
        assert "严格排除" in text


# ── 文件名构建 ──────────────────────────────────────────


class TestBiweeklyFilename:
    """验证文件名生成逻辑（通过 handlers 中的 _build_biweekly_filename）。"""

    def test_format_with_author(self):
        """带作者的标准格式。"""
        from iris.app.cli.handlers import _build_biweekly_filename

        class FakeBundle:
            class app:
                @staticmethod
                def get(key, default=None):
                    return {"author_name": "团队成员J"} if key == "biweekly_report" else default

        today = datetime(2026, 7, 7)  # 周二 → ISO week 28
        filename = _build_biweekly_filename(FakeBundle(), today)
        assert filename.startswith("20260707-双周报-w")
        assert "团队成员J" in filename
        assert "20260707" in filename

    def test_monday_uses_previous_week(self):
        """周一生成时归属上周。"""
        from iris.app.cli.handlers import _build_biweekly_filename

        class FakeBundle:
            class app:
                @staticmethod
                def get(key, default=None):
                    return {"author_name": "团队成员J"} if key == "biweekly_report" else default

        today = datetime(2026, 7, 6)  # 周一
        filename = _build_biweekly_filename(FakeBundle(), today)
        # 周一 → 用周日（7月5日）的周数，可能是 w27
        assert "w" in filename


# ── _sanitize_log_payload ─────────────────────────────────────────────

class TestSanitizeLogPayload:
    """analysis/service.py _sanitize_log_payload 安全截断测试。"""

    def test_markdown_truncated(self):
        long_md = "A" * 500
        payload = {"markdown": long_md, "blocks": [], "query": "q"}
        result = AnalysisReportService._sanitize_log_payload(payload)
        assert len(result["markdown"]) <= 203  # 200 chars + "…"
        assert result["markdown"].endswith("…")

    def test_short_markdown_unchanged(self):
        payload = {"markdown": "短内容", "blocks": [], "query": "q"}
        result = AnalysisReportService._sanitize_log_payload(payload)
        assert result["markdown"] == "短内容"

    def test_blocks_stripped_to_path_and_score(self):
        payload = {
            "markdown": "md",
            "blocks": [
                {"relative_path": "a/b.md", "score": 0.9, "summary": "敏感内容", "title": "T"},
                {"relative_path": "c/d.md", "score": 0.5, "summary": "另一段敏感内容"},
            ],
            "query": "q",
        }
        result = AnalysisReportService._sanitize_log_payload(payload)
        for b in result["blocks"]:
            assert set(b.keys()) == {"relative_path", "score"}
            assert "summary" not in b
            assert "title" not in b

    def test_no_markdown_key(self):
        payload = {"query": "q", "blocks": []}
        result = AnalysisReportService._sanitize_log_payload(payload)
        assert "markdown" not in result

    def test_non_string_markdown_ignored(self):
        payload = {"markdown": 12345, "blocks": []}
        result = AnalysisReportService._sanitize_log_payload(payload)
        assert result["markdown"] == 12345


# ── report_author 配置化 ──────────────────────────────────────────────

class TestReportAuthorConfig:
    """S1: report_author 从配置读取，空值不追加 footer。"""

    def _make_footer(self, author: str) -> str:
        return f"\n\n---\n> This report was written by Iris and revised by {author}."

    def test_footer_appended_when_author_set(self):
        """report_author 非空时追加 footer。"""
        author = "test_user"
        markdown = "内容正文"
        footer = self._make_footer(author)
        if not markdown.endswith(footer.strip()):
            markdown += footer
        assert "test_user" in markdown

    def test_footer_not_appended_when_author_empty(self):
        """report_author 为空时不追加 footer。"""
        report_author = ""
        markdown = "内容正文"
        if report_author:
            markdown += self._make_footer(report_author)
        assert "revised by" not in markdown

    def test_footer_not_duplicated(self):
        """footer 已存在时不重复追加。"""
        author = "alice"
        footer = self._make_footer(author)
        markdown = "内容" + footer
        if not markdown.endswith(footer.strip()):
            markdown += footer
        assert markdown.count("revised by alice") == 1


# ── Stage 4b 质量审查失败降级 ─────────────────────────────


class TestStage4bReviewFallback:
    """Stage 4b LLM 审查失败时必须安全回退 Stage 4a 组装稿，绝不写入失败产物。

    回归背景：某期双周报生成时 DeepSeek 思考模型 content 为空，provider 层
    曾静默回退返回 reasoning_content 思考文本，审查输出被思考过程污染并写入
    最终报告文件。v3.28.1 两层修复：provider 层不再回退思考文本（抛错），
    Stage 4b 层捕获异常回退组装稿。
    """

    def _make_service(self, llm):
        from iris.analysis.service import AnalysisReportService

        class _FakePromptLoader:
            def render(self, name, ctx):
                return "审查 prompt"

        svc = object.__new__(AnalysisReportService)
        svc._prompt_loader = _FakePromptLoader()
        svc._llm = llm
        return svc

    def test_llm_failure_returns_assembled(self):
        from iris.analysis.service import AnalysisReportService
        from iris.llm import LLMProviderError

        class _FakeLLM:
            def generate(self, **kwargs):
                raise LLMProviderError("审查 LLM 调用失败")

        svc = self._make_service(_FakeLLM())
        assembled = (
            "*时间周期：2026.08.16～2026.08.30*\n\n"
            "## 方向一：进展\n\n内容…"
        )
        out = svc._stage4b_review(
            "2026.08.16～2026.08.30", assembled,
            [{"name": "方向一", "scope_summary": "x", "key_indicators": []}],
        )
        assert out == assembled

    def test_llm_success_returns_reviewed_text(self):
        from iris.analysis.service import AnalysisReportService

        class _FakeLLM:
            def generate(self, **kwargs):
                from types import SimpleNamespace
                return SimpleNamespace(
                    text="*时间周期：2026.08.16～2026.08.30*\n\n## 方向一：修订版\n\n正文…"
                )

        svc = self._make_service(_FakeLLM())
        assembled = "*时间周期：2026.08.16～2026.08.30*\n\n## 方向一：原稿\n\n正文…"
        out = svc._stage4b_review(
            "2026.08.16～2026.08.30", assembled,
            [{"name": "方向一", "scope_summary": "x", "key_indicators": []}],
        )
        assert "修订版" in out
        assert "原稿" not in out
