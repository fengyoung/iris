"""analysis 模块纯函数测试（_helpers + service 模块级函数）。

覆盖不依赖 LLM / 网络的纯逻辑：证据渲染、JSON 解析容错、
双周报去重指纹提取、方向章节抽取、本地降级报告构建。
"""

from __future__ import annotations

from iris.analysis._helpers import (
    render_evidence_blocks,
    render_structured_evidence,
)
from iris.analysis import service as svc


# ── _helpers.render_evidence_blocks ─────────────────────────────

def _make_block(**overrides):
    base = {
        "evidence_type": "fact",
        "title": "标题A",
        "section_path": ["章节1", "子节"],
        "relative_path": "docs/a.md",
        "line_start": 10,
        "summary": "这是摘要",
    }
    base.update(overrides)
    return base


class TestRenderEvidenceBlocks:
    def test_empty_returns_placeholder(self):
        assert render_evidence_blocks([]) == "暂无候选证据"

    def test_uses_section_path_join(self):
        out = render_evidence_blocks([_make_block()])
        assert "章节1 > 子节" in out
        assert "docs/a.md:10" in out
        assert out.startswith("1. ")

    def test_falls_back_to_title_when_no_section_path(self):
        out = render_evidence_blocks([_make_block(section_path=[])])
        assert "章节：标题A" in out

    def test_multiple_blocks_are_numbered(self):
        out = render_evidence_blocks([_make_block(), _make_block(title="标题B")])
        assert "1. " in out and "2. " in out


class TestRenderStructuredEvidence:
    def test_empty_returns_none_marker(self):
        assert render_structured_evidence({}) == "无"

    def test_renders_overview_and_groups(self):
        structured = {
            "overview": "总体情况",
            "ordered_groups": ["进展", "风险"],
            "groups": {
                "进展": [{"summary": "进展1"}, {"summary": "进展2"}, {"summary": "进展3"}],
                "风险": [{"summary": "风险1"}],
            },
        }
        out = render_structured_evidence(structured)
        assert "总览：总体情况" in out
        # 每组最多取前 2 条
        assert "进展1 | 进展2" in out
        assert "进展3" not in out
        assert "风险1" in out

    def test_skips_empty_groups(self):
        structured = {"overview": "x", "ordered_groups": ["空组"], "groups": {"空组": []}}
        out = render_structured_evidence(structured)
        assert "空组" not in out


# ── service._try_parse_json ─────────────────────────────────────

class TestTryParseJson:
    def test_parses_clean_json(self):
        assert svc._try_parse_json('{"a": 1}') == {"a": 1}

    def test_extracts_embedded_json(self):
        text = "模型输出如下：\n{\"quality_score\": 9}\n以上。"
        result = svc._try_parse_json(text)
        assert result == {"quality_score": 9}

    def test_returns_none_on_garbage(self):
        assert svc._try_parse_json("完全不是 JSON") is None


# ── service._parse_review_json ──────────────────────────────────

class TestParseReviewJson:
    def test_prefers_object_with_quality_score(self):
        result = svc._parse_review_json('{"quality_score": 8, "issues": []}')
        assert result["quality_score"] == 8

    def test_returns_none_when_unparseable(self):
        assert svc._parse_review_json("no json here") is None


# ── service._extract_key_bullets ────────────────────────────────

class TestExtractKeyBullets:
    def test_extracts_bullet_lines_only(self):
        section = "普通行\n- 第一条\n* 第二条\n非 bullet\n- 第三条"
        bullets = svc._extract_key_bullets(section)
        # 保留 bullet 前缀，仅去除来源注记
        assert bullets == ["- 第一条", "* 第二条", "- 第三条"]

    def test_respects_max_per_report(self):
        section = "\n".join(f"- 条目{i}" for i in range(10))
        bullets = svc._extract_key_bullets(section, max_per_report=3)
        assert len(bullets) == 3

    def test_strips_source_annotation(self):
        section = "- 完成了功能A（来源：报告.md）"
        bullets = svc._extract_key_bullets(section)
        assert bullets == ["- 完成了功能A"]

    def test_truncates_long_bullet_to_120_chars(self):
        section = "- " + "字" * 200
        bullets = svc._extract_key_bullets(section)
        # clean 后截断到 120 字（含 "- " 前缀）
        assert len(bullets[0]) == 120


# ── service._extract_direction_section ──────────────────────────

class TestExtractDirectionSection:
    def test_extracts_matching_section(self):
        report = (
            "# 双周报\n"
            "## 方向一：项目Alpha\n"
            "- 进展A\n"
            "- 进展B\n"
            "## 方向二：数据质检\n"
            "- 进展C\n"
        )
        section = svc._extract_direction_section(report, "方向一：项目Alpha")
        assert "进展A" in section and "进展B" in section
        assert "进展C" not in section

    def test_returns_empty_when_no_match(self):
        report = "## 方向一：X\n- a\n"
        assert svc._extract_direction_section(report, "方向九：不存在") == ""


# ── service._build_multi_report_dedup_text ──────────────────────

class TestBuildMultiReportDedup:
    def test_empty_reports_returns_empty(self):
        assert svc._build_multi_report_dedup_text([], [{"name": "方向一：X"}]) == {}

    def test_collects_bullets_per_direction(self):
        reports = [
            {"content": "## 方向一：X\n- 已做A\n", "week": 28, "date_str": "2026-07-01"},
        ]
        directions = [{"name": "方向一：X"}]
        result = svc._build_multi_report_dedup_text(reports, directions)
        assert "方向一：X" in result
        assert "已做A" in result["方向一：X"]
        assert "w28" in result["方向一：X"]


# ── service._collect_direction_concepts ─────────────────────────

class TestCollectDirectionConcepts:
    def test_strips_bracket_prefix(self):
        direction = {"sub_areas": [{"name": "1.1 【验功能】搜索召回"}]}
        concepts = svc._collect_direction_concepts(direction)
        assert concepts == ["搜索召回"]

    def test_skips_empty_names(self):
        direction = {"sub_areas": [{"name": ""}, {"name": "纯名称"}]}
        assert svc._collect_direction_concepts(direction) == ["纯名称"]


# ── service._build_local_report / _resolve_section_content ──────

class TestBuildLocalReport:
    def test_builds_report_with_default_sections(self):
        blocks = [
            {"summary": "概述内容", "relative_path": "a.md", "line_start": 1},
            {"summary": "进展内容", "relative_path": "b.md", "line_start": 2},
        ]
        structured = {"overview": "总览文本", "groups": {}}
        report = svc._build_local_report("测试查询", "回答文本", blocks, structured)
        assert report.startswith("# 测试查询 分析报告")
        assert "## 背景概览" in report
        assert "总览文本" in report
        assert "## 参考来源" in report
        assert "a.md:1" in report

    def test_sources_section_placeholder_when_no_blocks(self):
        report = svc._build_local_report("q", "ans", [], {"overview": "o"})
        assert "- 暂无" in report


class TestLoadReportSections:
    def test_returns_default_when_no_custom(self):
        assert svc._load_report_sections({}) == svc.DEFAULT_REPORT_SECTIONS

    def test_uses_custom_sections(self):
        cfg = {"report": {"sections": [{"title": "自定义", "group": "overview"}]}}
        result = svc._load_report_sections(cfg)
        assert result == [("自定义", "overview")]

    def test_falls_back_when_custom_invalid(self):
        cfg = {"report": {"sections": [{"title": "", "group": ""}]}}
        assert svc._load_report_sections(cfg) == svc.DEFAULT_REPORT_SECTIONS
