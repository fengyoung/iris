"""iris.analysis._biweekly_helpers 纯函数单元测试。"""

from __future__ import annotations

import pytest

from iris.analysis._biweekly_helpers import (
    DEFAULT_REPORT_SECTIONS,
    _build_boundaries_text,
    _build_file_manifest,
    _build_local_fallback,
    _build_local_report,
    _collect_direction_concepts,
    _extract_direction_section,
    _extract_key_bullets,
    _load_report_sections,
    _pick_group_line,
    _render_group_lines,
    _resolve_section_content,
    _s3_build_concept_boundaries,
    _s3_build_direction_index,
    _s3_extract_strategic_insights,
    _s3_index_briefs_by_direction,
    _try_parse_json,
)
from datetime import datetime


# ── _collect_direction_concepts ─────────────────────────────


class TestCollectDirectionConcepts:
    def test_empty_direction(self):
        result = _collect_direction_concepts({})
        assert result == []

    def test_no_sub_areas(self):
        result = _collect_direction_concepts({"name": "方向A", "id": 1})
        assert result == []

    def test_sub_areas_simple_names(self):
        direction = {
            "name": "方向A",
            "sub_areas": [{"name": "概念X"}, {"name": "项目Y"}],
        }
        result = _collect_direction_concepts(direction)
        assert "概念X" in result
        assert "项目Y" in result

    def test_sub_areas_with_bracket_prefix(self):
        """带 【】前缀的子领域名，只取括号后部分。"""
        direction = {
            "sub_areas": [{"name": "1.1 【验功能】搜索召回"}],
        }
        result = _collect_direction_concepts(direction)
        assert len(result) == 1
        assert "搜索召回" in result[0]

    def test_sub_areas_with_space_prefix(self):
        """带空格编号前缀的子领域名，取空格后部分。"""
        direction = {
            "sub_areas": [{"name": "1.1 项目Alpha"}],
        }
        result = _collect_direction_concepts(direction)
        assert "项目Alpha" in result[0]

    def test_empty_name_sub_area_skipped(self):
        direction = {
            "sub_areas": [{"name": ""}, {"name": "有效概念"}],
        }
        result = _collect_direction_concepts(direction)
        assert result == ["有效概念"]


# ── _build_boundaries_text ──────────────────────────────────


class TestBuildBoundariesText:
    def test_empty_bounds(self):
        result = _build_boundaries_text("方向A", {})
        assert result == ""

    def test_with_own_only(self):
        result = _build_boundaries_text("方向A", {"own": ["概念1", "概念2"]})
        assert "概念1" in result
        assert "自有" in result

    def test_with_others_only(self):
        result = _build_boundaries_text("方向A", {"others": {"方向B": ["概念B1"]}})
        assert "概念B1" in result
        assert "其他方向" in result

    def test_own_truncated_at_12(self):
        """own 列表超过 12 个时，只展示前 12 个。"""
        # 使用独特词 X0Y 到 X19Y 避免标题行干扰计数
        own_list = [f"X{i}Y" for i in range(20)]
        result = _build_boundaries_text("方向A", {"own": own_list})
        shown = [f"X{i}Y" for i in range(12)]
        hidden = [f"X{i}Y" for i in range(12, 20)]
        for item in shown:
            assert item in result
        for item in hidden:
            assert item not in result

    def test_with_both_own_and_others(self):
        result = _build_boundaries_text("方向A", {
            "own": ["概念A"],
            "others": {"方向B：子标题": ["概念B"]},
        })
        assert "概念A" in result
        assert "概念B" in result


# ── _extract_direction_section ──────────────────────────────


class TestExtractDirectionSection:
    def test_hit_exact_direction_name(self):
        report = "## 方向A\n\n- 进展1\n- 进展2\n\n## 其他章节\n内容"
        result = _extract_direction_section(report, "方向A")
        assert "进展1" in result
        assert "进展2" in result

    def test_no_match_returns_empty(self):
        report = "## 方向A\n内容\n"
        result = _extract_direction_section(report, "方向Z")
        assert result == ""

    def test_core_name_matching(self):
        """去掉 '方向N：' 前缀后仍能匹配。"""
        report = "## 数据质量\n\n- 质量提升\n\n## 下一节\n内容"
        result = _extract_direction_section(report, "方向一：数据质量")
        assert "质量提升" in result

    def test_stops_at_next_h2(self):
        """在下一个 ## 标题处停止。"""
        report = "## 方向A\n- 内容A\n## 方向B\n- 内容B"
        result = _extract_direction_section(report, "方向A")
        assert "内容A" in result
        assert "内容B" not in result


# ── _extract_key_bullets ────────────────────────────────────


class TestExtractKeyBullets:
    def test_empty_section(self):
        assert _extract_key_bullets("") == []

    def test_basic_bullets(self):
        section = "## 进展\n- 完成功能A\n- 上线功能B\n"
        result = _extract_key_bullets(section)
        assert len(result) == 2
        assert any("功能A" in b for b in result)

    def test_max_per_report_truncation(self):
        lines = "\n".join(f"- 进展{i}" for i in range(10))
        result = _extract_key_bullets(lines, max_per_report=3)
        assert len(result) == 3

    def test_removes_source_annotation(self):
        section = "- 完成测试（来源：张三周报）\n"
        result = _extract_key_bullets(section)
        assert result
        assert "来源" not in result[0]

    def test_long_bullet_truncated(self):
        long_bullet = "- " + "A" * 200
        result = _extract_key_bullets(long_bullet)
        assert result
        assert len(result[0]) <= 120

    def test_asterisk_bullets_included(self):
        section = "* 任务X完成\n* 任务Y进行中\n"
        result = _extract_key_bullets(section)
        assert len(result) == 2


# ── _s3_build_direction_index ───────────────────────────────


class TestS3BuildDirectionIndex:
    def test_empty_directions(self):
        by_name, by_id = _s3_build_direction_index([])
        assert by_name == {}
        assert by_id == {}

    def test_basic_index(self):
        directions = [
            {"name": "方向A", "id": 1},
            {"name": "方向B", "id": 2},
        ]
        by_name, by_id = _s3_build_direction_index(directions)
        assert "方向A" in by_name
        assert 1 in by_id
        assert by_id[1]["name"] == "方向A"

    def test_missing_id_skipped_in_by_id(self):
        directions = [{"name": "方向C"}]
        by_name, by_id = _s3_build_direction_index(directions)
        assert "方向C" in by_name
        assert by_id == {}


# ── _s3_index_briefs_by_direction ───────────────────────────


class TestS3IndexBriefsByDirection:
    def _make_dir_indexes(self):
        directions = [{"name": "方向A", "id": 1}, {"name": "方向B", "id": 2}]
        return _s3_build_direction_index(directions)

    def test_basic_indexing(self):
        by_name, by_id = self._make_dir_indexes()
        briefs = {
            "label1": {"primary_direction": 1, "relevant_directions": [1]},
        }
        result = _s3_index_briefs_by_direction(briefs, by_name, by_id)
        assert "方向A" in result
        assert len(result["方向A"]) == 1

    def test_primary_direction_fallback(self):
        """relevant_directions 为空时，回退到 primary_direction。"""
        by_name, by_id = self._make_dir_indexes()
        briefs = {
            "label1": {"primary_direction": 2, "relevant_directions": []},
        }
        result = _s3_index_briefs_by_direction(briefs, by_name, by_id)
        assert "方向B" in result

    def test_string_type_primary_direction(self):
        """primary_direction 为字符串数字时也能匹配。"""
        by_name, by_id = self._make_dir_indexes()
        briefs = {
            "label1": {"primary_direction": "1", "relevant_directions": []},
        }
        result = _s3_index_briefs_by_direction(briefs, by_name, by_id)
        assert "方向A" in result


# ── _s3_build_concept_boundaries ────────────────────────────


class TestS3BuildConceptBoundaries:
    def test_empty_directions(self):
        result = _s3_build_concept_boundaries([])
        assert result == {}

    def test_two_directions(self):
        directions = [
            {"name": "方向A", "sub_areas": [{"name": "概念X"}]},
            {"name": "方向B", "sub_areas": [{"name": "概念Y"}]},
        ]
        result = _s3_build_concept_boundaries(directions)
        assert "方向A" in result
        assert "方向B" in result
        # 方向A 的 own 应包含 X，others 应包含方向B
        own_a = result["方向A"]["own"]
        assert any("概念X" in c or "X" in c for c in own_a)
        others_a = result["方向A"]["others"]
        assert "方向B" in others_a


# ── _s3_extract_strategic_insights ──────────────────────────


class TestS3ExtractStrategicInsights:
    def test_empty_briefs(self):
        assert _s3_extract_strategic_insights([]) == ""

    def test_dedup_insights(self):
        briefs = [
            {"strategic_insights": ["洞察A", "洞察B"]},
            {"strategic_insights": ["洞察A"]},  # 重复
        ]
        result = _s3_extract_strategic_insights(briefs)
        assert result.count("洞察A") == 1
        assert "洞察B" in result

    def test_none_insights_skipped(self):
        briefs = [{"strategic_insights": None}]
        result = _s3_extract_strategic_insights(briefs)
        assert result == ""

    def test_output_format(self):
        briefs = [{"strategic_insights": ["关键洞察"]}]
        result = _s3_extract_strategic_insights(briefs)
        assert "战略洞察" in result
        assert "关键洞察" in result


# ── _build_file_manifest ─────────────────────────────────────


class TestBuildFileManifest:
    def test_empty_files(self):
        result = _build_file_manifest([])
        assert "无" in result or "无数据" in result

    def test_single_file(self):
        files = [{
            "dir": "成员周报",
            "label": "张三-0703",
            "filename": "张三周报.md",
            "date": datetime(2026, 7, 3),
            "content": "本周完成了功能A",
            "char_count": 8,
        }]
        result = _build_file_manifest(files)
        assert "张三周报.md" in result
        assert "功能A" in result

    def test_content_truncated_at_2000(self):
        long_content = "X" * 3000
        files = [{
            "dir": "周报",
            "label": "label1",
            "filename": "file.md",
            "date": datetime(2026, 7, 1),
            "content": long_content,
            "char_count": 3000,
        }]
        result = _build_file_manifest(files)
        assert "截断" in result


# ── _build_local_fallback ─────────────────────────────────────


class TestBuildLocalFallback:
    def test_basic_output(self):
        result = _build_local_fallback("2026-07-01 ~ 2026-07-14", "OP文档内容", "文件清单")
        assert "2026-07-01" in result
        assert "OP文档内容" in result
        assert "文件清单" in result

    def test_contains_report_header(self):
        result = _build_local_fallback("周期", "", "")
        assert "本周进展" in result


# ── _try_parse_json ───────────────────────────────────────────


class TestTryParseJson:
    def test_valid_json(self):
        result = _try_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_embedded_in_text(self):
        text = '一些说明文字\n{"key": "hello"}\n后续文字'
        result = _try_parse_json(text)
        assert result is not None
        assert result.get("key") == "hello"

    def test_invalid_returns_none(self):
        result = _try_parse_json("这不是JSON内容")
        assert result is None


# ── DEFAULT_REPORT_SECTIONS 格式校验 ─────────────────────────


class TestDefaultReportSections:
    def test_is_list_of_tuples(self):
        assert isinstance(DEFAULT_REPORT_SECTIONS, list)
        for item in DEFAULT_REPORT_SECTIONS:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_non_empty(self):
        assert len(DEFAULT_REPORT_SECTIONS) > 0

    def test_all_have_non_empty_title_and_group(self):
        for title, group in DEFAULT_REPORT_SECTIONS:
            assert title
            assert group


# ── _load_report_sections ────────────────────────────────────


class TestLoadReportSections:
    def test_empty_config_uses_default(self):
        result = _load_report_sections({})
        assert result == DEFAULT_REPORT_SECTIONS

    def test_custom_sections(self):
        config = {
            "report": {
                "sections": [
                    {"title": "自定义标题", "group": "custom_group"},
                ]
            }
        }
        result = _load_report_sections(config)
        assert result == [("自定义标题", "custom_group")]

    def test_invalid_custom_falls_back_to_default(self):
        """自定义 sections 无效时回退到默认。"""
        config = {
            "report": {
                "sections": [
                    {"title": "", "group": ""},  # 无效条目
                ]
            }
        }
        result = _load_report_sections(config)
        assert result == DEFAULT_REPORT_SECTIONS


# ── _build_local_report ──────────────────────────────────────


class TestBuildLocalReport:
    def _make_blocks(self):
        return [
            {"summary": "摘要1", "relative_path": "file1.md", "line_start": 1},
            {"summary": "摘要2", "relative_path": "file2.md", "line_start": 10},
        ]

    def test_basic_output(self):
        blocks = self._make_blocks()
        structured = {"overview": "总体概览内容"}
        result = _build_local_report("测试查询", "回答内容", blocks, structured)
        assert "测试查询" in result
        assert "总体概览内容" in result

    def test_contains_section_headers(self):
        blocks = self._make_blocks()
        structured = {}
        result = _build_local_report("查询", "回答", blocks, structured)
        # 应包含至少一个章节标题
        assert "##" in result


# ── _resolve_section_content ─────────────────────────────────


class TestResolveSectionContent:
    def _make_blocks(self):
        return [
            {"summary": "摘要A", "relative_path": "a.md", "line_start": 1},
            {"summary": "摘要B", "relative_path": "b.md", "line_start": 5},
        ]

    def test_sources_group(self):
        blocks = self._make_blocks()
        result = _resolve_section_content("sources", {}, blocks, "answer", "overview")
        assert "a.md" in result

    def test_next_steps_group(self):
        structured = {"recommended_next_steps": ["步骤1", "步骤2"]}
        result = _resolve_section_content("next_steps", structured, [], "answer", "overview")
        assert "步骤1" in result

    def test_overview_group(self):
        result = _resolve_section_content("overview", {}, [], "answer", "总体概览")
        assert "总体概览" in result

    def test_goal_group(self):
        structured = {"groups": {"goal": [{"summary": "目标内容"}]}}
        result = _resolve_section_content("goal", structured, [], "answer", "overview")
        assert "目标内容" in result

    def test_decision_group(self):
        structured = {"groups": {"decision": [{"summary": "结论X"}]}}
        result = _resolve_section_content("decision", structured, [], "answer", "overview")
        assert "结论X" in result

    def test_risk_group(self):
        result = _resolve_section_content("risk", {}, [], "answer", "overview")
        assert "暂无" in result

    def test_unknown_group(self):
        result = _resolve_section_content("unknown_group_xyz", {}, [], "answer", "overview")
        assert "暂无" in result


# ── _pick_group_line / _render_group_lines ───────────────────


class TestGroupLineFunctions:
    def test_pick_group_line_empty(self):
        result = _pick_group_line({}, "goal")
        assert result == ""

    def test_pick_group_line_with_item(self):
        structured = {"groups": {"goal": [{"summary": "目标文本"}]}}
        result = _pick_group_line(structured, "goal")
        assert result == "目标文本"

    def test_render_group_lines_fallback(self):
        result = _render_group_lines({}, "risk", fallback="- 无风险")
        assert result == "- 无风险"

    def test_render_group_lines_with_items(self):
        structured = {"groups": {"risk": [{"summary": "风险A"}, {"summary": "风险B"}]}}
        result = _render_group_lines(structured, "risk", fallback="- 无")
        assert "风险A" in result
        assert "风险B" in result
