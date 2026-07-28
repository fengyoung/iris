"""term_extractor 单元测试 — 无需 LLM 调用。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iris.wiki.context_loader import WikiPageInfo
from iris.wiki.asr import (
    AsrPromptVersion,
    AsrTerm,
    TermExtractor,
    bump_version,
    compute_fingerprint,
    determine_new_version,
    load_version,
    render_asr_prompt,
    save_version,
)


# ═══════════════════════════════════════════════════════════════
# 测试辅助：构造模拟 WikiPageInfo
# ═══════════════════════════════════════════════════════════════

def _make_page(stem: str, page_type: str, summary: str, body: str) -> WikiPageInfo:
    """构造模拟 WikiPageInfo，路径基于 stem 和 page_type。"""
    dir_map = {
        "person": "04-人物",
        "concept": "02-概念",
        "project": "03-项目",
        "domain": "01-领域",
    }
    dir_name = dir_map.get(page_type, "01-领域")
    path = Path(f"/tmp/wiki/{dir_name}/{stem}.md")
    return WikiPageInfo(
        path=path,
        title=stem.rsplit("-", 1)[-1] if "-" in stem else stem,
        page_type=page_type,
        status="stable",
        summary=summary,
        body=body,
        relative_path=f"{dir_name}/{stem}.md",
    )


# ═══════════════════════════════════════════════════════════════
# AsrTerm / AsrPromptVersion
# ═══════════════════════════════════════════════════════════════

class TestAsrTerm:
    def test_default_mis_asr_empty(self):
        t = AsrTerm(term="测试", category="concept", context="")
        assert t.mis_asr == []

    def test_with_mis_asr(self):
        t = AsrTerm(term="张三", category="person", context="工程师",
                     mis_asr=["张珊", "章三"])
        assert len(t.mis_asr) == 2


class TestAsrPromptVersion:
    def test_fields(self):
        v = AsrPromptVersion(
            version="1.2.3", generated_at="2026-01-01T00:00:00Z",
            wiki_page_count=10, term_count=30, fingerprint="abcdef01"
        )
        assert v.version == "1.2.3"
        assert v.wiki_page_count == 10
        assert v.term_count == 30


# ═══════════════════════════════════════════════════════════════
# bump_version
# ═══════════════════════════════════════════════════════════════

class TestBumpVersion:
    def test_major(self):
        assert bump_version("1.0.0", "major") == "2.0.0"
        assert bump_version("0.1.5", "major") == "1.0.0"

    def test_minor(self):
        assert bump_version("1.0.0", "minor") == "1.1.0"
        assert bump_version("2.5.3", "minor") == "2.6.0"

    def test_patch(self):
        assert bump_version("1.0.0", "patch") == "1.0.1"
        assert bump_version("3.2.9", "patch") == "3.2.10"

    def test_auto_same_as_patch(self):
        assert bump_version("1.0.0", "auto") == "1.0.1"

    def test_invalid_version(self):
        assert bump_version("abc", "patch") == "0.0.1"

    def test_first_generation(self):
        assert bump_version("0.0.0", "patch") == "0.0.1"


# ═══════════════════════════════════════════════════════════════
# compute_fingerprint
# ═══════════════════════════════════════════════════════════════

class TestComputeFingerprint:
    def test_stable(self):
        pages = [
            _make_page("人物-张三", "person", "工程师", "## 角色\n负责推荐"),
            _make_page("概念-BM25", "concept", "检索算法", "## 定义\n排序函数"),
        ]
        fp1 = compute_fingerprint(pages)
        fp2 = compute_fingerprint(pages)
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_different_on_change(self):
        pages1 = [_make_page("人物-张三", "person", "工程师", "body1")]
        pages2 = [_make_page("人物-张三", "person", "工程师", "body2")]
        assert compute_fingerprint(pages1) != compute_fingerprint(pages2)

    def test_different_on_page_count(self):
        pages1 = [_make_page("人物-张三", "person", "x", "y")]
        pages2 = [
            _make_page("人物-张三", "person", "x", "y"),
            _make_page("人物-李四", "person", "x", "y"),
        ]
        assert compute_fingerprint(pages1) != compute_fingerprint(pages2)


# ═══════════════════════════════════════════════════════════════
# TermExtractor — 术语提取（阶段 1，无 LLM）
# ═══════════════════════════════════════════════════════════════

class TestExtractTerms:
    def test_extract_person(self):
        pages = [_make_page("人物-张三", "person", "算法工程师",
                            "## 摘要\n算法。\n## 角色\nAlpha项目负责人。")]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        assert len(terms) == 1
        assert terms[0].term == "张三"
        assert terms[0].category == "person"
        assert "Alpha" in terms[0].context
        assert terms[0].mis_asr == []

    def test_extract_concept(self):
        pages = [_make_page("概念-BM25", "concept", "检索排序算法",
                            "## 摘要\n检索排序。\n## 定义\n概率检索模型。")]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        assert len(terms) == 1
        assert terms[0].term == "BM25"
        assert terms[0].category == "concept"
        # 优先取 ## 定义 段落首句，而非 summary
        assert terms[0].context == "概率检索模型。"

    def test_extract_project(self):
        pages = [_make_page("项目-Alpha", "project", "核心推荐系统",
                            "## 摘要\n推荐系统。")]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        assert len(terms) == 1
        assert terms[0].term == "Alpha"
        assert terms[0].category == "project"
        assert "推荐" in terms[0].context

    def test_extract_domain_terms(self):
        body = "## 摘要\n机器学习平台。\n## 技术栈\n使用 TensorFlow 和 PyTorch。"
        pages = [_make_page("领域-机器学习平台", "domain", "ML平台", body)]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        # domain 页可能提取多个术语（TensorFlow, PyTorch）
        domain_terms = [t for t in terms if t.category == "domain_term"]
        assert len(domain_terms) >= 1

    def test_deduplicate_across_types(self):
        """person 和 project 有同名术语时，保留 person（先处理）。"""
        pages = [
            _make_page("人物-Alpha", "person", "工程师",
                       "## 角色\n负责人。"),
            _make_page("项目-Alpha", "project", "核心项目",
                       "## 摘要\n项目。"),
        ]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        # 去重后只保留一个人物条目
        alpha_terms = [t for t in terms if t.term == "Alpha"]
        assert len(alpha_terms) == 1
        assert alpha_terms[0].category == "person"

    def test_empty_pages(self):
        extractor = TermExtractor([])
        terms = extractor.extract_terms()
        assert terms == []

    def test_context_fallback_to_summary(self):
        """人物页没有 ## 角色 时回退到 summary。"""
        pages = [_make_page("人物-王五", "person", "测试工程师",
                            "## 摘要\n测试。\n## 专长\n自动化测试。")]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        assert len(terms) == 1
        assert terms[0].term == "王五"
        assert "测试" in terms[0].context

    def test_concept_definition_fallback(self):
        """概念页优先用 ## 定义 段首句作为 context。"""
        pages = [_make_page("概念-Embedding", "concept", "向量嵌入",
                            "## 定义\n将离散对象映射为连续向量表示的技术。\n\n## 使用场景\n...")]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        assert len(terms) == 1
        assert "映射" in terms[0].context


# ═══════════════════════════════════════════════════════════════
# TermExtractor — LLM prompt 构建与响应解析（阶段 2）
# ═══════════════════════════════════════════════════════════════

class TestBuildMisreadingsPrompt:
    def test_prompt_contains_all_terms(self):
        pages = [
            _make_page("人物-张三", "person", "工程师", "body"),
            _make_page("概念-BM25", "concept", "检索", "body"),
        ]
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()
        prompt = extractor._build_misreadings_prompt(terms)
        assert "张三" in prompt
        assert "BM25" in prompt
        assert "person" not in prompt.lower() or "人名" in prompt
        # 应包含 JSON 输出格式说明
        assert "mis_asr" in prompt

    def test_prompt_empty_terms(self):
        extractor = TermExtractor([])
        prompt = extractor._build_misreadings_prompt([])
        assert "术语列表" in prompt


class TestParseMisreadingsResponse:
    def test_valid_response(self):
        terms = [
            AsrTerm(term="张三", category="person", context="工程师"),
            AsrTerm(term="BM25", category="concept", context="检索算法"),
        ]
        response = json.dumps([
            {"term": "张三", "category": "person", "mis_asr": ["张珊", "章三"]},
            {"term": "BM25", "category": "concept", "mis_asr": ["bm二十五"]},
        ])
        TermExtractor._parse_misreadings_response(response, terms)
        assert terms[0].mis_asr == ["张珊", "章三"]
        assert terms[1].mis_asr == ["bm二十五"]

    def test_code_fence_wrapped(self):
        terms = [AsrTerm(term="张三", category="person", context="工程师")]
        response = '```json\n[{"term":"张三","category":"person","mis_asr":["张珊"]}]\n```'
        TermExtractor._parse_misreadings_response(response, terms)
        assert terms[0].mis_asr == ["张珊"]

    def test_invalid_json_graceful(self):
        terms = [AsrTerm(term="张三", category="person", context="工程师")]
        TermExtractor._parse_misreadings_response("not valid json at all", terms)
        assert terms[0].mis_asr == []

    def test_partial_match(self):
        """只有部分 term 被 LLM 返回。"""
        terms = [
            AsrTerm(term="张三", category="person", context="工程师"),
            AsrTerm(term="BM25", category="concept", context="检索"),
        ]
        response = json.dumps([
            {"term": "张三", "category": "person", "mis_asr": ["张珊"]},
        ])
        TermExtractor._parse_misreadings_response(response, terms)
        assert terms[0].mis_asr == ["张珊"]
        assert terms[1].mis_asr == []

    def test_mis_asr_truncated_to_5(self):
        """超过 5 个误识别时截断。"""
        terms = [AsrTerm(term="张三", category="person", context="工程师")]
        response = json.dumps([
            {"term": "张三", "category": "person",
             "mis_asr": ["a", "b", "c", "d", "e", "f", "g"]},
        ])
        TermExtractor._parse_misreadings_response(response, terms)
        assert len(terms[0].mis_asr) == 5


# ═══════════════════════════════════════════════════════════════
# render_asr_prompt
# ═══════════════════════════════════════════════════════════════

class TestRenderAsrPrompt:
    @staticmethod
    def _version():
        return AsrPromptVersion(
            version="1.0.0",
            generated_at="2026-06-27T00:00:00Z",
            wiki_page_count=5,
            term_count=3,
            fingerprint="abc",
        )

    def test_standard_contains_sections(self):
        terms = [
            AsrTerm(term="张三", category="person", context="工程师",
                     mis_asr=["张珊"]),
            AsrTerm(term="BM25", category="concept", context="检索",
                     mis_asr=["bm二十五"]),
            AsrTerm(term="Alpha", category="project", context="项目",
                     mis_asr=["阿尔法"]),
        ]
        result = render_asr_prompt(terms, self._version(), output_format="standard")
        assert "## 人名词典" in result
        assert "## 术语词典" in result
        assert "## 项目名词典" in result
        assert "张三" in result
        assert "张珊" in result
        assert "BM25" in result
        assert "Alpha" in result
        assert "v1.0.0" in result

    def test_compact_format(self):
        terms = [
            AsrTerm(term="张三", category="person", context="工程师",
                     mis_asr=["张珊"]),
        ]
        result = render_asr_prompt(terms, self._version(), output_format="compact")
        assert "【人名】" in result
        assert "张三" in result
        assert "张珊" in result
        assert "v1.0.0" in result

    def test_standard_with_empty_mis_asr(self):
        """mis_asr 为空时显示 '-'。"""
        terms = [AsrTerm(term="张三", category="person", context="工程师")]
        result = render_asr_prompt(terms, self._version(), output_format="standard")
        assert "| - |" in result or "| - |" in result

    def test_empty_terms(self):
        result = render_asr_prompt([], self._version(), output_format="standard")
        assert "校正助手" in result


# ═══════════════════════════════════════════════════════════════
# 版本管理
# ═══════════════════════════════════════════════════════════════

class TestVersionPersistence:
    def test_save_and_load(self, tmp_path):
        data_dir = tmp_path / "data"
        v = AsrPromptVersion(
            version="1.2.3", generated_at="2026-01-01T00:00:00Z",
            wiki_page_count=10, term_count=30, fingerprint="abc123",
        )
        save_version(data_dir, v)

        loaded = load_version(data_dir)
        assert loaded is not None
        assert loaded.version == "1.2.3"
        assert loaded.wiki_page_count == 10
        assert loaded.term_count == 30
        assert loaded.fingerprint == "abc123"

    def test_load_nonexistent(self, tmp_path):
        result = load_version(tmp_path / "nonexistent")
        assert result is None

    def test_load_corrupted(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "asr_prompt_version.json").write_text("not json", encoding="utf-8")
        result = load_version(data_dir)
        assert result is None


class TestDetermineNewVersion:
    def test_first_generation(self, tmp_path):
        pages = [_make_page("人物-张三", "person", "工程师", "body")]
        data_dir = tmp_path / "data"
        v = determine_new_version(pages, data_dir, bump="auto")
        assert v.version == "0.0.1"
        assert v.wiki_page_count == 1

    def test_auto_no_change(self, tmp_path):
        """auto 模式指纹不变应返回旧版本。"""
        pages = [_make_page("人物-张三", "person", "工程师", "body")]
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        # 先保存一个版本
        save_version(data_dir, AsrPromptVersion(
            version="1.0.5", generated_at="2026-01-01T00:00:00Z",
            wiki_page_count=1, term_count=1,
            fingerprint=compute_fingerprint(pages),
        ))

        v = determine_new_version(pages, data_dir, bump="auto")
        assert v.version == "1.0.5"  # 不变

    def test_auto_with_change(self, tmp_path):
        """auto 模式指纹变化应 bump patch。"""
        pages = [_make_page("人物-张三", "person", "工程师", "body")]
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        save_version(data_dir, AsrPromptVersion(
            version="1.0.5", generated_at="2026-01-01T00:00:00Z",
            wiki_page_count=1, term_count=1,
            fingerprint="different_fp",
        ))

        v = determine_new_version(pages, data_dir, bump="auto")
        assert v.version == "1.0.6"

    def test_manual_bump_ignores_fingerprint(self, tmp_path):
        """手动 bump 时忽略指纹，始终递增。"""
        pages = [_make_page("人物-张三", "person", "工程师", "body")]
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        save_version(data_dir, AsrPromptVersion(
            version="1.0.0", generated_at="2026-01-01T00:00:00Z",
            wiki_page_count=1, term_count=1,
            fingerprint=compute_fingerprint(pages),  # 指纹相同
        ))

        v = determine_new_version(pages, data_dir, bump="minor")
        assert v.version == "1.1.0"
