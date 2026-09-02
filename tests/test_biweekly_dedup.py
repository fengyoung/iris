"""build-biweekly-report 去重逻辑单元测试。

覆盖：多期报告加载、方向章节提取、key bullets 提取、多期去重文本构建。
"""

from __future__ import annotations

from datetime import datetime


from iris.analysis._biweekly_helpers import (
    _extract_direction_section,
    _extract_key_bullets,
    _extract_previous_direction_sections,
    _build_multi_report_dedup_text,
)


# ── 方向章节提取 ──────────────────────────────────────────


SAMPLE_REPORT = """
*时间周期：2026.06.08～2026.06.21*

## 质检流程智能化

> 战略定位

战略分析段落内容。

**关键进展：**

- 项目Alpha检测安卓机型覆盖百余款，整体检出率约5%。（来源：张三周报-0608）
- 项目Beta分析精度一致性经历三阶段爬坡达90%+。（来源：李四周报-0613）
- 项目Gamma全场景通过率首次突破30%+。（来源：李四周报-0613）

## 搜索推荐体验

> 提升多品类搜索体验

搜索推荐体验方向战略分析内容。

**关键进展：**

- LLM-based相关性全量上线，长尾品类整体支付转化率+20%。（来源：王五周报）
- 首页流量调控全量，低价商品曝光占比+10%。（来源：赵六周报）
"""


class TestExtractDirectionSection:
    """验证 _extract_direction_section 从报告中提取特定方向章节。"""

    def test_extract_standard_direction(self):
        """提取标准方向章节。"""
        content = _extract_direction_section(SAMPLE_REPORT, "质检流程智能化")
        assert "项目Alpha检测" in content
        assert "项目Beta" in content
        assert "关键进展" in content

    def test_extract_second_direction(self):
        """提取第二个方向章节。"""
        content = _extract_direction_section(SAMPLE_REPORT, "搜索推荐体验")
        assert "LLM-based" in content
        assert "长尾品类" in content

    def test_extract_nonexistent_direction(self):
        """不存在的方向返回空。"""
        content = _extract_direction_section(SAMPLE_REPORT, "不存在的方向")
        assert content == ""

    def test_extract_with_colon_prefix(self):
        """带「方向N：」前缀的匹配。"""
        report = """
## 方向一：质检流程智能化

> test

content
"""
        content = _extract_direction_section(report, "方向一：质检流程智能化")
        assert "content" in content

    def test_section_boundary(self):
        """提取在下一个 ## 标题处停止。"""
        content = _extract_direction_section(SAMPLE_REPORT, "质检流程智能化")
        # 不应包含搜索推荐体验方向的内容
        assert "LLM-based" not in content
        assert "长尾品类" not in content


# ── Key Bullets 提取 ─────────────────────────────────────


class TestExtractKeyBullets:
    """验证 _extract_key_bullets 提取关键进展 bullets。"""

    def test_extract_bullets(self):
        """提取 bullet 列表。"""
        section = """
**关键进展：**

- 第一条进展内容（来源：某人）
- 第二条进展内容（来源：某人）
- 第三条进展内容
"""
        bullets = _extract_key_bullets(section, max_per_report=3)
        assert len(bullets) == 3
        assert "第一条" in bullets[0]
        assert "第二条" in bullets[1]

    def test_remove_citation(self):
        """去掉引用标签以减少去重噪音。"""
        section = "- 进展内容（来源：某人周报-0608）"
        bullets = _extract_key_bullets(section)
        assert "（来源：" not in bullets[0]
        assert "进展内容" in bullets[0]

    def test_max_limit(self):
        """控制最大条数。"""
        section = "\n".join(f"- bullet {i}" for i in range(10))
        bullets = _extract_key_bullets(section, max_per_report=3)
        assert len(bullets) == 3

    def test_skip_non_bullets(self):
        """跳过非 bullet 行。"""
        section = """
## 标题

正文段落

- bullet 1
- bullet 2
"""
        bullets = _extract_key_bullets(section)
        assert len(bullets) == 2


# ── 多期报告去重文本构建 ────────────────────────────────


FAKE_DIRECTION = {
    "id": 1,
    "name": "质检流程智能化",
    "strategic_quote": "构建功能检测、质量检测、商品评估的检测能力",
    "scope_summary": "覆盖项目Alpha、项目Beta、项目Gamma",
    "key_indicators": ["检出率", "通过率"],
    "sub_areas": [],
}


class TestBuildMultiReportDedupText:
    """验证 _build_multi_report_dedup_text 构建多期去重参考文本。"""

    def test_empty_reports(self):
        """空报告列表返回空 dict。"""
        result = _build_multi_report_dedup_text([], [FAKE_DIRECTION])
        assert result == {}

    def test_single_report(self):
        """单份报告提取正确。"""
        reports = [{
            "week": 25,
            "date": datetime(2026, 6, 21),
            "date_str": "2026.06.21",
            "content": SAMPLE_REPORT,
        }]
        result = _build_multi_report_dedup_text(reports, [FAKE_DIRECTION])
        assert "质检流程智能化" in result
        dedup_text = result["质检流程智能化"]
        assert "w25" in dedup_text
        assert "2026.06.21" in dedup_text

    def test_multiple_reports(self):
        """多份报告分别列出。"""
        reports = [
            {
                "week": 25,
                "date": datetime(2026, 6, 21),
                "date_str": "2026.06.21",
                "content": SAMPLE_REPORT,
            },
            {
                "week": 23,
                "date": datetime(2026, 6, 7),
                "date_str": "2026.06.07",
                "content": SAMPLE_REPORT.replace("151款", "140款"),
            },
        ]
        result = _build_multi_report_dedup_text(reports, [FAKE_DIRECTION])
        dedup_text = result.get("质检流程智能化", "")
        assert "w25" in dedup_text
        assert "w23" in dedup_text

    def test_no_matching_direction(self):
        """无匹配方向不生成条目。"""
        reports = [{
            "week": 25,
            "date": datetime(2026, 6, 21),
            "date_str": "2026.06.21",
            "content": SAMPLE_REPORT,
        }]
        fake_dir = {"id": 99, "name": "不存在的方向", "scope_summary": "test"}
        result = _build_multi_report_dedup_text(reports, [fake_dir])
        # 不存在的方向不应有去重文本
        assert "不存在的方向" not in result


# ── 单期去重兼容 ──────────────────────────────────────────


class TestExtractPreviousDirectionSections:
    """验证 _extract_previous_direction_sections（兼容旧接口）。"""

    def test_extract_all_directions(self):
        """提取所有方向章节。"""
        directions = [
            {"name": "质检流程智能化"},
            {"name": "搜索推荐体验"},
        ]
        result = _extract_previous_direction_sections(SAMPLE_REPORT, directions)
        assert "质检流程智能化" in result
        assert "搜索推荐体验" in result

    def test_empty_report(self):
        """空报告返回空 dict。"""
        result = _extract_previous_direction_sections("", [FAKE_DIRECTION])
        assert result == {}
