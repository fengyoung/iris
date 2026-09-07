"""_biweekly_helpers.py 单元测试 — 覆盖全部纯函数（无需 mock）。"""

from __future__ import annotations

from datetime import datetime

from iris.analysis._biweekly_helpers import (
    _build_boundaries_text,
    _build_file_manifest,
    _build_local_fallback,
    _build_multi_report_dedup_text,
    _collect_direction_concepts,
    _extract_direction_section,
    _extract_key_bullets,
    _extract_previous_direction_sections,
    _group_briefs_by_subarea,
    _s3_build_concept_boundaries,
    _s3_build_direction_index,
    _s3_check_subarea_order,
    _s3_extract_strategic_insights,
    _normalize_key_progress_heading,
    _strip_direction_quotes,
    _strip_review_scaffolding,
)


class TestCollectDirectionConcepts:
    def test_empty_direction(self):
        assert _collect_direction_concepts({}) == []

    def test_no_sub_areas(self):
        assert _collect_direction_concepts({"name": "方向1"}) == []

    def test_simple_sub_areas(self):
        d = {"sub_areas": [{"name": "搜索"}, {"name": "推荐"}]}
        result = _collect_direction_concepts(d)
        assert "搜索" in result
        assert "推荐" in result

    def test_sub_area_with_bracket_prefix(self):
        d = {"sub_areas": [{"name": "1.1 【搜索体验】核心"}]}
        result = _collect_direction_concepts(d)
        # 应取 】 之后的核心部分
        assert any("核心" in r for r in result)

    def test_sub_area_with_space_prefix(self):
        d = {"sub_areas": [{"name": "1.1 搜索相关"}]}
        result = _collect_direction_concepts(d)
        assert any("搜索相关" in r for r in result)


class TestBuildBoundariesText:
    def test_empty_bounds(self):
        result = _build_boundaries_text("方向1", {})
        assert result == ""

    def test_own_concepts_only(self):
        result = _build_boundaries_text("方向1", {"own": ["搜索", "推荐"], "others": {}})
        assert "搜索" in result
        assert "本方向自有概念" in result

    def test_own_and_others(self):
        bounds = {"own": ["搜索"], "others": {"方向2：质检": ["质检", "审核"]}}
        result = _build_boundaries_text("方向1", bounds)
        assert "搜索" in result
        assert "其他方向" in result
        assert "质检" in result

    def test_own_truncated_to_12(self):
        own = [f"概念{i}" for i in range(20)]
        result = _build_boundaries_text("方向1", {"own": own})
        # 最多展示前 12 个
        assert "概念11" in result
        assert "概念12" not in result


class TestExtractDirectionSection:
    def test_exact_match(self):
        report = "## 方向一：搜索体验\n内容A\n内容B\n## 方向二：推荐\n内容C"
        result = _extract_direction_section(report, "方向一：搜索体验")
        assert "内容A" in result
        assert "内容B" in result
        assert "内容C" not in result

    def test_no_match(self):
        report = "## 方向一：搜索\n内容A"
        result = _extract_direction_section(report, "方向三：不存在")
        assert result == ""

    def test_last_section_no_end_marker(self):
        report = "## 方向一：搜索\n内容A\n内容B"
        result = _extract_direction_section(report, "方向一：搜索")
        assert "内容A" in result

    def test_core_name_match(self):
        report = "## 搜索体验\n内容A\n## 推荐\n内容B"
        result = _extract_direction_section(report, "方向一：搜索体验")
        assert "内容A" in result


class TestExtractKeyBullets:
    def test_empty_section(self):
        assert _extract_key_bullets("") == []

    def test_no_bullets(self):
        assert _extract_key_bullets("普通文本\n没有 bullet") == []

    def test_dash_bullets(self):
        section = "- 进展A\n- 进展B\n- 进展C"
        result = _extract_key_bullets(section)
        assert len(result) == 3
        assert "进展A" in result[0]

    def test_star_bullets(self):
        section = "* 进展A\n* 进展B"
        result = _extract_key_bullets(section)
        assert len(result) == 2

    def test_max_per_report_truncation(self):
        bullets = "\n".join(f"- 进展{i}" for i in range(10))
        result = _extract_key_bullets(bullets, max_per_report=3)
        assert len(result) == 3

    def test_long_bullet_truncated_to_120(self):
        long_bullet = "- " + "X" * 200
        result = _extract_key_bullets(long_bullet)
        assert len(result[0]) <= 120

    def test_source_tag_removed(self):
        section = "- 进展A（来源：某文件.md）"
        result = _extract_key_bullets(section)
        assert "来源" not in result[0]


class TestExtractPreviousDirectionSections:
    def test_empty_report(self):
        directions = [{"name": "方向一"}]
        result = _extract_previous_direction_sections("", directions)
        assert result == {}

    def test_match_found(self):
        report = "## 方向一：搜索\n内容A"
        directions = [{"name": "方向一：搜索"}]
        result = _extract_previous_direction_sections(report, directions)
        assert "方向一：搜索" in result
        assert "内容A" in result["方向一：搜索"]

    def test_no_match(self):
        report = "## 其他\n内容"
        directions = [{"name": "方向一"}]
        result = _extract_previous_direction_sections(report, directions)
        assert result == {}


class TestBuildMultiReportDedupText:
    def test_empty_reports(self):
        assert _build_multi_report_dedup_text([], [{"name": "方向一"}]) == {}

    def test_single_report_with_bullets(self):
        reports = [{"content": "## 方向一\n- 进展A", "week": 1, "date_str": "2026-07-01"}]
        directions = [{"name": "方向一"}]
        result = _build_multi_report_dedup_text(reports, directions)
        assert "方向一" in result
        assert "进展A" in result["方向一"]

    def test_direction_not_in_report(self):
        reports = [{"content": "## 其他\n- 内容", "week": 1, "date_str": "2026-07-01"}]
        directions = [{"name": "方向一"}]
        result = _build_multi_report_dedup_text(reports, directions)
        assert "方向一" not in result


class TestS3BuildDirectionIndex:
    def test_empty(self):
        by_name, by_id = _s3_build_direction_index([])
        assert by_name == {}
        assert by_id == {}

    def test_normal(self):
        directions = [
            {"name": "方向一", "id": 1},
            {"name": "方向二", "id": 2},
        ]
        by_name, by_id = _s3_build_direction_index(directions)
        assert "方向一" in by_name
        assert 1 in by_id
        assert by_id[1]["name"] == "方向一"

    def test_missing_id(self):
        directions = [{"name": "方向一"}]
        by_name, by_id = _s3_build_direction_index(directions)
        assert "方向一" in by_name
        assert by_id == {}


class TestS3BuildConceptBoundaries:
    def test_empty(self):
        result = _s3_build_concept_boundaries([])
        assert result == {}

    def test_single_direction(self):
        directions = [{"name": "方向一", "sub_areas": [{"name": "搜索"}]}]
        result = _s3_build_concept_boundaries(directions)
        assert "方向一" in result
        assert "搜索" in result["方向一"]["own"]
        assert result["方向一"]["others"] == {}

    def test_two_directions_mutual_exclusion(self):
        directions = [
            {"name": "方向一", "sub_areas": [{"name": "搜索"}]},
            {"name": "方向二", "sub_areas": [{"name": "推荐"}]},
        ]
        result = _s3_build_concept_boundaries(directions)
        # 方向一的 others 应包含方向二的概念
        assert "方向二" in result["方向一"]["others"]
        assert "推荐" in result["方向一"]["others"]["方向二"]


class TestS3ExtractStrategicInsights:
    def test_empty_briefs(self):
        assert _s3_extract_strategic_insights([]) == ""

    def test_no_insights(self):
        briefs = [{"label": "文件A", "strategic_insights": []}]
        assert _s3_extract_strategic_insights(briefs) == ""

    def test_single_insight(self):
        briefs = [{"strategic_insights": ["洞察A"]}]
        result = _s3_extract_strategic_insights(briefs)
        assert "洞察A" in result
        assert "战略洞察" in result

    def test_dedup_across_briefs(self):
        briefs = [
            {"strategic_insights": ["洞察A"]},
            {"strategic_insights": ["洞察A"]},  # 重复
        ]
        result = _s3_extract_strategic_insights(briefs)
        assert result.count("洞察A") == 1


class TestGroupBriefsBySubarea:
    def test_empty_briefs(self):
        direction = {"name": "方向一", "sub_areas": [{"name": "搜索"}]}
        result = _group_briefs_by_subarea([], direction)
        assert result == {}  # 空组被清理

    def test_no_sub_areas_returns_fallback(self):
        direction = {"name": "方向一", "sub_areas": []}
        brief = {"label": "文件A", "key_facts": ["进展A"], "quantitative_data": []}
        result = _group_briefs_by_subarea([brief], direction)
        assert "跨领域综合" in result

    def test_matching_by_keyword(self):
        direction = {
            "name": "方向一",
            "sub_areas": [{"name": "搜索体验"}, {"name": "推荐优化"}],
        }
        brief_search = {"label": "搜索相关", "key_facts": ["搜索体验改进"], "quantitative_data": []}
        result = _group_briefs_by_subarea([brief_search], direction)
        # brief 应被归入搜索相关的子领域
        assert any("搜索" in k for k in result.keys())


class TestBuildFileManifest:
    def test_empty_files(self):
        result = _build_file_manifest([])
        assert "无数据源文件" in result

    def test_single_file(self):
        files = [{
            "dir": "05-会议纪要",
            "label": "[A1]",
            "filename": "meeting.md",
            "date": datetime(2026, 7, 19),
            "char_count": 1000,
            "content": "会议内容",
        }]
        result = _build_file_manifest(files)
        assert "05-会议纪要" in result
        assert "[A1]" in result
        assert "2026-07-19" in result

    def test_content_truncated(self):
        files = [{
            "dir": "测试",
            "label": "[B1]",
            "filename": "long.md",
            "date": datetime(2026, 7, 1),
            "char_count": 5000,
            "content": "X" * 3000,
        }]
        result = _build_file_manifest(files)
        assert "截断" in result


class TestBuildLocalFallback:
    def test_basic(self):
        result = _build_local_fallback("2026-07-01~2026-07-14", "OP 内容", "文件清单")
        assert "2026-07-01~2026-07-14" in result
        assert "OP 内容" in result
        assert "文件清单" in result

    def test_empty_op_and_manifest(self):
        result = _build_local_fallback("2026-07-01~2026-07-14", "", "")
        assert "2026-07-01~2026-07-14" in result


class TestS3CheckSubareaOrder:
    def test_correct_order_no_warning(self, caplog):
        sub_areas = [{"name": "搜索体验核心功能"}, {"name": "推荐优化"}]
        section = "搜索体验核心功能...\n推荐优化...\n"
        import logging
        with caplog.at_level(logging.WARNING):
            _s3_check_subarea_order("方向一", section, sub_areas)
        assert not any("顺序" in r.message for r in caplog.records)

    def test_wrong_order_logs_warning(self, caplog):
        sub_areas = [{"name": "搜索体验核心功能"}, {"name": "推荐优化"}]
        section = "推荐优化...\n搜索体验核心功能...\n"  # 顺序颠倒
        import logging
        with caplog.at_level(logging.WARNING):
            _s3_check_subarea_order("方向一", section, sub_areas)
        assert any("顺序" in r.message for r in caplog.records)

    def test_empty_sub_areas_no_error(self):
        _s3_check_subarea_order("方向一", "内容", [])  # 不应抛异常

    def test_single_sub_area_no_warning(self, caplog):
        _s3_check_subarea_order("方向一", "搜索体验", [{"name": "搜索体验"}])
        assert caplog.records == []


class TestW35StyleLean:
    """防回归：Stage 3 模板与默认风格指南须保持「w35 精简风格」——
    总结段=方向总览 + 只展开有判断/决策点的项目（反程式化、篇幅克制）；
    关键进展=每方向 2-4 条 + 「关键」门槛，不要求与 sub_area 一一对应；
    low 相关文件不作关键进展来源。"""

    @staticmethod
    def _template_text() -> str:
        from pathlib import Path
        p = Path(__file__).resolve().parents[2] / "templates" / "prompt" / "biweekly_stage3_direction.md"
        return p.read_text(encoding="utf-8")

    def test_summary_requires_judgment_and_decision(self):
        text = self._template_text()
        assert "判断" in text and "决策" in text
        assert "判断是目的" in text
        assert "读者标尺" in text

    def test_summary_anti_formulaic_and_length_budget(self):
        text = self._template_text()
        assert "反程式化" in text
        assert "禁止逐句套用" in text
        assert "固定骨架句式" in text
        assert "≤400 字" in text

    def test_summary_force_split_short_paragraphs(self):
        text = self._template_text()
        assert "分段硬约束" in text
        assert "严禁把多个项目的判断揉成一个超长段落" in text
        assert "≤150 字" in text

    def test_summary_not_forced_per_subarea(self):
        text = self._template_text()
        assert "并入方向总览，不单独成段" in text
        assert "不为覆盖而写" in text
        assert "有判断才展开" in text

    def test_progress_value_gate_and_cap(self):
        text = self._template_text()
        assert "条数硬顶" in text
        assert "2-4 个聚合条目" in text
        assert "「关键」门槛" in text
        assert "不要求与 sub_area 一一对应" in text
        assert "例行部署、次要指标微调、纯状态或计划描述" in text
        assert "不构成关键进展" in text

    def test_progress_project_aggregation_kept(self):
        text = self._template_text()
        assert "项目级聚合（硬约束）" in text
        assert "严禁把同一项目拆成多个并列条目" in text

    def test_progress_low_files_not_source(self):
        text = self._template_text()
        assert "low 相关" in text
        assert "默认不作为关键进展来源" in text

    def test_default_style_guide_matches_lean(self):
        from iris.analysis._biweekly_helpers import DEFAULT_STYLE_GUIDE
        assert "短段" in DEFAULT_STYLE_GUIDE["paragraph_structure"]
        assert "严禁" in DEFAULT_STYLE_GUIDE["paragraph_structure"]
        assert "≤400 字" in DEFAULT_STYLE_GUIDE["density_note"]
        patterns = "".join(DEFAULT_STYLE_GUIDE["strategic_patterns"])
        assert "固定骨架句式连排" in patterns
        assert "严禁把多个项目的判断揉成一个超长单段" in patterns
        assert "成员周报例行进展仅作关键进展事实来源" in patterns
        rules = "".join(DEFAULT_STYLE_GUIDE["writing_rules"])
        assert "禁止流水账式事实罗列" in rules
        assert "无判断点的项目并入总览不单列" in rules


class TestStripReviewScaffolding:
    def test_strips_trailing_op_sections(self):
        md = ("*时间周期：2026.08.16～2026.08.30*\n\n"
              "## 图验技术\n\n正文……\n\n"
              "## OP 方向摘要\n用于完整性检查的辅助内容\n\n"
              "## OP 核心指标状态\n指标回吐内容\n")
        out = _strip_review_scaffolding(md)
        assert out.startswith("*时间周期")
        assert "## 图验技术" in out
        assert "OP 方向摘要" not in out
        assert "OP 核心指标" not in out
        assert out.endswith("正文……")

    def test_no_op_scaffolding_returns_unchanged(self):
        md = "*时间周期：x*\n\n## 图验技术\n\n正文"
        assert _strip_review_scaffolding(md) == md


class TestNormalizeKeyProgressHeading:
    def test_plain_text_line_to_heading(self):
        md = "## 图验技术\n\n正文\n\n关键进展：\n\n- a"
        out = _normalize_key_progress_heading(md)
        assert "### 关键进展" in out
        assert "关键进展：\n" not in out

    def test_already_heading_kept(self):
        md = "### 关键进展\n- a"
        out = _normalize_key_progress_heading(md)
        assert out == "### 关键进展\n- a"

    def test_sentence_mention_not_touched(self):
        md = "本章的关键进展：详见下文。"
        assert _normalize_key_progress_heading(md) == md


class TestStripDirectionQuotes:
    def test_removes_quote_after_direction_heading(self):
        md = ("## 图验技术\n\n> 战略定位句。\n\n正文内容\n\n"
              "## 质检执行智能化\n\n> 另一句定位。\n\n正文")
        out = _strip_direction_quotes(md)
        assert "> 战略定位句。" not in out
        assert "> 另一句定位。" not in out
        assert out.count("## ") == 2
        assert "正文内容" in out

    def test_quote_only_first_content_after_head_removed(self):
        md = "## 方向\n\n> 引用\n\n> 引用续行\n\n正文\n> 保留的正文引用"
        out = _strip_direction_quotes(md)
        assert "> 引用续行" not in out
        assert "> 保留的正文引用" in out

    def test_no_quote_keeps_content(self):
        md = "## 图验技术\n\n直接正文。"
        assert _strip_direction_quotes(md) == md
