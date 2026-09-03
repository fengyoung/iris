"""深度评估模块测试（从 iris2 迁移）。"""

import pytest
from iris.evaluation.deep_eval import (
    parse_references,
    extract_page_title,
    ReferenceEntry,
    SourceLocator,
    AccuracyVerifier,
    AccuracyVerdict,
    DeepEvalResult,
)


class TestParseReferences:
    """验证 Wiki 参考来源解析。"""

    def test_bracket_format_with_line(self):
        content = """## 摘要
test

## 参考来源
1. [SOURCE/path/to/doc.md:42] 该方案将准确率提升到 95%
2. [SOURCE/other.md:10] 项目Beta 项目启动
"""
        entries = parse_references(content)
        assert len(entries) == 2
        assert entries[0].source_path == "SOURCE/path/to/doc.md"
        assert entries[0].line_number == 42
        assert "95%" in entries[0].description
        assert entries[1].source_path == "SOURCE/other.md"
        assert entries[1].line_number == 10

    def test_bracket_format_without_line(self):
        content = """## 参考来源
1. [SOURCE/doc.md] 概述文档
"""
        entries = parse_references(content)
        assert len(entries) == 1
        assert entries[0].source_path == "SOURCE/doc.md"
        assert entries[0].line_number is None

    def test_numbered_path_with_line(self):
        content = """## 参考来源
1. SOURCE/doc.md:42（关键数据）准确率数据来源
2. SOURCE/other.md:10 项目启动记录
"""
        entries = parse_references(content)
        assert len(entries) >= 1
        assert entries[0].source_path.endswith("doc.md")

    def test_no_reference_section(self):
        content = """## 摘要
no references here

## 正文
some content
"""
        entries = parse_references(content)
        assert len(entries) == 0

    def test_empty_content(self):
        entries = parse_references("")
        assert len(entries) == 0


class TestExtractPageTitle:
    def test_from_frontmatter(self):
        content = """---
title: 项目Beta 项目
type: project
---
正文内容
"""
        assert extract_page_title(content, "file.md") == "项目Beta 项目"

    def test_fallback_to_filename(self):
        content = """# 没有 frontmatter 的内容"""
        assert extract_page_title(content, "项目Beta.md") == "项目Beta"


class TestSourceLocator:
    def test_load_and_lookup(self, tmp_path):
        """验证 chunk 索引的加载和查找。"""
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({
            "chunks": [
                {
                    "relative_path": "SOURCE/test/doc.md",
                    "line_start": 1,
                    "line_end": 10,
                    "content": "这是测试文档的第一段内容。",
                },
                {
                    "relative_path": "SOURCE/test/doc.md",
                    "line_start": 11,
                    "line_end": 20,
                    "content": "这是测试文档的第二段内容，提到了项目Beta项目。",
                },
                {
                    "relative_path": "SOURCE/other.md",
                    "line_start": 1,
                    "line_end": 5,
                    "content": "另一份文档。",
                },
            ],
        }, ensure_ascii=False), encoding="utf-8")

        locator = SourceLocator([str(summary_path)])
        locator.load()

        # 按路径查找
        content = locator.lookup("SOURCE/test/doc.md")
        assert "第一段" in content

        # 按行号查找
        content = locator.lookup("SOURCE/test/doc.md", line_number=15)
        assert "项目Beta" in content

        # 不存在的文件
        assert locator.lookup("SOURCE/nonexistent.md") is None

    def test_lookup_relevant_prefers_evidence_over_frontmatter(self, tmp_path):
        """回归：相关性定位应命中含稀有词的证据 chunk，而非泛化词的 frontmatter。"""
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({
            "chunks": [
                {
                    "relative_path": "SOURCE/org.md",
                    "line_start": 1, "line_end": 8,
                    # frontmatter：命中大量泛化词（组织/部门/数据），但无人名
                    "content": "---\ntitle: 组织架构\ntype: 部门管理\ndate: 2026\n---",
                },
                {
                    "relative_path": "SOURCE/org.md",
                    "line_start": 9, "line_end": 20,
                    "content": "# 组织架构\n数据智能部 组织结构 文档 说明",
                },
                {
                    "relative_path": "SOURCE/org.md",
                    "line_start": 21, "line_end": 40,
                    # 证据 chunk：含稀有人名
                    "content": "数据标注组 Leader 王柳坤，成员 刘佳蓉 归属此组，直报冯扬。",
                },
            ],
        }, ensure_ascii=False), encoding="utf-8")

        locator = SourceLocator([str(summary_path)])
        locator.load()

        desc = "该文档列出组织结构，包含刘佳蓉的部门归属及上级信息"
        rel = locator.lookup_relevant("SOURCE/org.md", desc)
        assert rel is not None
        # 关键：证据内容被取到
        assert "刘佳蓉" in rel
        # frontmatter 元数据不应作为证据主体出现
        assert not rel.lstrip().startswith("---")

    def test_lookup_relevant_missing_doc(self, tmp_path):
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({"chunks": []}), encoding="utf-8")
        locator = SourceLocator([str(summary_path)])
        locator.load()
        assert locator.lookup_relevant("SOURCE/nope.md", "任意描述") is None

    def test_find_sibling_sources(self, tmp_path):
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({
            "chunks": [
                {"relative_path": "SOURCE/2025/doc_a.md", "line_start": 1, "line_end": 5,
                 "content": "文档A内容" * 50},
                {"relative_path": "SOURCE/2025/doc_b.md", "line_start": 1, "line_end": 5,
                 "content": "文档B内容" * 30},
                {"relative_path": "SOURCE/2025/doc_c.md", "line_start": 1, "line_end": 5,
                 "content": "文档C内容" * 10},
                {"relative_path": "SOURCE/other/doc_d.md", "line_start": 1, "line_end": 5,
                 "content": "文档D内容"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        locator = SourceLocator([str(summary_path)])
        locator.load()

        siblings = locator.find_sibling_sources("SOURCE/2025/doc_a.md", max_count=3)
        assert len(siblings) >= 1
        assert all("2025" in s for s in siblings)

    def test_search_by_keywords(self, tmp_path):
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({
            "chunks": [
                {"relative_path": "SOURCE/项目Beta/设计文档.md", "line_start": 1, "line_end": 5,
                 "content": "项目Beta 设计"},
                {"relative_path": "SOURCE/其他/不相关.md", "line_start": 1, "line_end": 5,
                 "content": "无关内容"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        locator = SourceLocator([str(summary_path)])
        locator.load()

        results = locator.search_sources_by_keywords(["项目Beta"], max_results=3)
        assert len(results) == 1
        assert "项目Beta" in results[0]


class TestDeepEvalResult:
    def test_result_creation(self):
        result = DeepEvalResult(
            evaluated_at="2026-06-29T10:00:00",
            total_pages=5,
            total_references=20,
            consistent_count=15,
            inconsistent_count=3,
            unverifiable_count=1,
            source_missing_count=1,
            overall_accuracy_rate=0.833,
            pages_with_gaps=2,
            total_gaps=4,
            overall_comprehensiveness_note="测试",
        )
        assert result.overall_accuracy_rate == 0.833
        assert result.total_references == 20

    def test_json_export(self):
        from iris.evaluation import deep_eval_result_to_json

        result = DeepEvalResult(
            evaluated_at="2026-06-29",
            total_pages=1, total_references=3,
            consistent_count=2, inconsistent_count=1,
            unverifiable_count=0, source_missing_count=0,
            overall_accuracy_rate=0.667,
            pages_with_gaps=0, total_gaps=0,
            overall_comprehensiveness_note="",
        )
        d = deep_eval_result_to_json(result)
        assert d["total_pages"] == 1
        assert d["accuracy"]["overall_rate"] == 0.667


# ── AccuracyVerifier（mock LLM + locator，无网络） ─────────────────


class _FakeLLMResult:
    def __init__(self, text: str):
        self.text = text


class _FakeLLM:
    """LLMService 替身：返回预置文本或抛异常。"""

    def __init__(self, text: str = "", *, error: Exception = None):
        self._text = text
        self._error = error
        self.calls = 0

    def generate(self, prompt, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return _FakeLLMResult(self._text)


class _FakeLocator:
    """SourceLocator 替身：按路径返回预置内容。"""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def lookup_with_context(self, path, line_number=None, **kwargs):
        return self._mapping.get(path)


def _entry(description="Wiki 描述内容", path="src/a.md", line=10):
    return ReferenceEntry(raw="raw", source_path=path, line_number=line, description=description)


class TestAccuracyVerifierVerifyOne:
    def test_source_missing(self):
        from iris.llm import LLMProviderError  # noqa: F401 - 保证导入路径可用

        verifier = AccuracyVerifier(_FakeLLM("consistent|ok"), _FakeLocator({}))
        verdict = verifier._verify_one(_entry())
        assert verdict.verdict == "source_missing"

    def test_empty_description_unverifiable(self):
        verifier = AccuracyVerifier(
            _FakeLLM("consistent|ok"),
            _FakeLocator({"src/a.md": "源文档内容"}),
        )
        verdict = verifier._verify_one(_entry(description="   "))
        assert verdict.verdict == "unverifiable"
        assert "描述为空" in verdict.detail

    def test_consistent_parsed_from_pipe(self):
        llm = _FakeLLM("consistent|描述与源文档一致")
        verifier = AccuracyVerifier(llm, _FakeLocator({"src/a.md": "源文档内容"}))
        verdict = verifier._verify_one(_entry())
        assert verdict.verdict == "consistent"
        assert verdict.detail == "描述与源文档一致"
        assert llm.calls == 1

    def test_inconsistent_parsed_from_pipe(self):
        verifier = AccuracyVerifier(
            _FakeLLM("inconsistent|描述与源文档不符"),
            _FakeLocator({"src/a.md": "源文档内容"}),
        )
        verdict = verifier._verify_one(_entry())
        assert verdict.verdict == "inconsistent"
        assert verdict.detail == "描述与源文档不符"

    def test_unverifiable_keyword_fallback(self):
        # 无竖线分隔时走关键词匹配（unverifiable 无子串歧义）
        verifier = AccuracyVerifier(
            _FakeLLM("模型认为 unverifiable"),
            _FakeLocator({"src/a.md": "源文档内容"}),
        )
        verdict = verifier._verify_one(_entry())
        assert verdict.verdict == "unverifiable"

    def test_unknown_verdict_normalized_to_unverifiable(self):
        verifier = AccuracyVerifier(
            _FakeLLM("garbage|无法判断"),
            _FakeLocator({"src/a.md": "源文档内容"}),
        )
        verdict = verifier._verify_one(_entry())
        assert verdict.verdict == "unverifiable"

    def test_llm_error_yields_unverifiable(self):
        from iris.llm import LLMProviderError

        verifier = AccuracyVerifier(
            _FakeLLM(error=LLMProviderError("offline")),
            _FakeLocator({"src/a.md": "源文档内容"}),
        )
        verdict = verifier._verify_one(_entry())
        assert verdict.verdict == "unverifiable"
        assert "LLM 调用失败" in verdict.detail


class TestHasRelevantWikiContent:
    def test_short_content_false(self):
        verifier = AccuracyVerifier(_FakeLLM(), _FakeLocator({}))
        assert verifier._has_relevant_wiki_content("短", _entry()) is False

    def test_substantial_content_with_description_true(self):
        verifier = AccuracyVerifier(_FakeLLM(), _FakeLocator({}))
        long_content = "字" * 300
        assert verifier._has_relevant_wiki_content(long_content, _entry()) is True

    def test_substantial_content_without_description_false(self):
        verifier = AccuracyVerifier(_FakeLLM(), _FakeLocator({}))
        long_content = "字" * 300
        assert verifier._has_relevant_wiki_content(long_content, _entry(description="")) is False


class TestAccuracyVerifierVerify:
    def test_verify_aggregates_verdicts(self):
        llm = _FakeLLM("consistent|一致")
        verifier = AccuracyVerifier(llm, _FakeLocator({"src/a.md": "源内容"}))
        verdicts = verifier.verify([_entry(), _entry(path="src/a.md")])
        assert len(verdicts) == 2
        assert all(v.verdict == "consistent" for v in verdicts)


# ── SourceLocator 路径归一化 + fallback warning ────────────────────


class TestSourceLocatorPathNormalization:
    """验证 _normalize_path 对 ./ 前缀和路径格式的归一化。"""

    def _make_locator(self, tmp_path):
        import json
        summary = {
            "chunks": [
                {"relative_path": "SOURCE/dir/doc.md", "line_start": 1, "line_end": 10,
                 "content": "正文内容"},
            ]
        }
        p = tmp_path / "cs.json"
        p.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        from iris.evaluation._source_locator import SourceLocator
        locator = SourceLocator([str(p)])
        locator.load()
        return locator

    def test_dotslash_prefix_normalized(self, tmp_path):
        locator = self._make_locator(tmp_path)
        # "./SOURCE/dir/doc.md" 应等同于 "SOURCE/dir/doc.md"
        content = locator.lookup("./SOURCE/dir/doc.md")
        assert content is not None
        assert "正文内容" in content

    def test_leading_slash_normalized(self, tmp_path):
        locator = self._make_locator(tmp_path)
        content = locator.lookup("/SOURCE/dir/doc.md")
        assert content is not None
        assert "正文内容" in content

    def test_backslash_normalized(self, tmp_path):
        locator = self._make_locator(tmp_path)
        content = locator.lookup("SOURCE\\dir\\doc.md")
        assert content is not None
        assert "正文内容" in content


class TestSourceLocatorFallbackWarning:
    """验证行号无精确匹配时回退到末尾 chunk 并发出 warning。"""

    def _make_locator(self, tmp_path):
        import json
        summary = {
            "chunks": [
                {"relative_path": "SOURCE/doc.md", "line_start": 1, "line_end": 10,
                 "content": "第一段"},
                {"relative_path": "SOURCE/doc.md", "line_start": 11, "line_end": 20,
                 "content": "最后一段"},
            ]
        }
        p = tmp_path / "cs.json"
        p.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        from iris.evaluation._source_locator import SourceLocator
        locator = SourceLocator([str(p)])
        locator.load()
        return locator

    def test_line_not_found_returns_last_chunk(self, tmp_path):
        locator = self._make_locator(tmp_path)
        # 行号 999 超出所有 chunk 范围，应回退到最后一个 chunk
        content = locator.lookup("SOURCE/doc.md", line_number=999)
        assert content == "最后一段"

    def test_line_not_found_logs_warning(self, tmp_path, caplog):
        import logging
        locator = self._make_locator(tmp_path)
        with caplog.at_level(logging.WARNING, logger="iris.evaluation._source_locator"):
            locator.lookup("SOURCE/doc.md", line_number=999)
        assert any("无精确匹配" in r.message for r in caplog.records)

    def test_line_not_found_with_context_logs_warning(self, tmp_path, caplog):
        import logging
        locator = self._make_locator(tmp_path)
        with caplog.at_level(logging.WARNING, logger="iris.evaluation._source_locator"):
            locator.lookup_with_context("SOURCE/doc.md", line_number=999)
        assert any("无精确匹配" in r.message for r in caplog.records)


# ── Prompt 模板填充回归 ────────────────────────────────────────────


class TestPromptTemplateFilling:
    """回归：评估 Prompt 模板必须能被 .format() 正确填充。

    历史缺陷：模板文件使用双花括号 {{x}}，.format() 只把其转义成字面 {x}
    而不替换，导致 LLM 收到的是占位符文本本身，准确性校验全部"无法验证"。
    """

    def test_accuracy_prompt_fills_placeholders(self):
        from iris.evaluation.deep_eval import _get_accuracy_prompt
        prompt = _get_accuracy_prompt().format(
            source_content="SRC_CONTENT_X", wiki_description="DESC_Y"
        )
        assert "{source_content}" not in prompt
        assert "{wiki_description}" not in prompt
        assert "SRC_CONTENT_X" in prompt
        assert "DESC_Y" in prompt

    def test_page_accuracy_prompt_fills_placeholders(self):
        from iris.evaluation.deep_eval import _get_page_accuracy_prompt
        prompt = _get_page_accuracy_prompt().format(
            source_content="SRC_X", wiki_title="TITLE_Y",
            wiki_content_snippet="SNIPPET_Z",
        )
        assert "{source_content}" not in prompt
        assert "{wiki_title}" not in prompt
        assert "{wiki_content_snippet}" not in prompt
        assert "SRC_X" in prompt and "TITLE_Y" in prompt

    def test_comprehensiveness_prompt_fills_placeholders(self):
        from iris.evaluation.deep_eval import _get_comprehensiveness_prompt
        prompt = _get_comprehensiveness_prompt().format(
            wiki_title="TITLE_A", wiki_content_snippet="SNIP_B",
            candidate_source="CAND_C",
        )
        assert "{wiki_title}" not in prompt
        assert "{candidate_source}" not in prompt
        assert "TITLE_A" in prompt and "CAND_C" in prompt
