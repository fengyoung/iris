"""build-biweekly-report 文件处理单元测试。

覆盖：日期提取、人名提取、引用标签构建、文件收集、frontmatter 日期 fallback。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iris.analysis._biweekly_collector import BiweeklyCollector


# ── 日期提取 ──────────────────────────────────────────────


class TestExtractDateFromPath:
    """验证 _extract_date_from_path 从文件名提取 YYYYMMDD。"""

    def test_standard_weekly_report(self):
        """标准成员周报格式。"""
        d = BiweeklyCollector._extract_date_from_path("20250621-周报-w25-李四.md")
        assert d == datetime(2025, 6, 21)

    def test_meeting_minutes(self):
        """会议纪要格式。"""
        d = BiweeklyCollector._extract_date_from_path("20260702-项目讨论-项目Alpha检测.md")
        assert d == datetime(2026, 7, 2)

    def test_discussion(self):
        """讨论思考格式。"""
        d = BiweeklyCollector._extract_date_from_path("20260701-内部讨论-质检执行智能化.md")
        assert d == datetime(2026, 7, 1)

    def test_no_date(self):
        """无日期文件名返回 None。"""
        d = BiweeklyCollector._extract_date_from_path("周报-李四.md")
        assert d is None

    def test_invalid_date(self):
        """非法日期返回 None。"""
        d = BiweeklyCollector._extract_date_from_path("99999999-测试.md")
        assert d is None

    def test_biweekly_report_name(self):
        """双周报文件名格式。"""
        d = BiweeklyCollector._extract_date_from_path("双周报-w25-团队成员J-20260621.md")
        assert d == datetime(2026, 6, 21)


# ── Frontmatter 日期 fallback ──────────────────────────────


class TestExtractDateFromFrontmatter:
    """验证 _extract_date_from_frontmatter 从 YAML frontmatter 提取日期。"""

    def test_standard_frontmatter(self):
        """标准 date: YYYY-MM-DD 格式。"""
        content = """---
title: Test
date: 2026-07-03
---
正文内容"""
        d = BiweeklyCollector._extract_date_from_frontmatter(content)
        assert d == datetime(2026, 7, 3)

    def test_chinese_date_format(self):
        """中文 日期：YYYY年MM月DD日 格式。"""
        content = """---
日期：2026年7月3日
---
正文"""
        d = BiweeklyCollector._extract_date_from_frontmatter(content)
        assert d == datetime(2026, 7, 3)

    def test_no_frontmatter(self):
        """无 frontmatter 文件返回 None。"""
        content = "# 标题\n\n正文内容"
        d = BiweeklyCollector._extract_date_from_frontmatter(content)
        assert d is None

    def test_frontmatter_without_date(self):
        """frontmatter 无日期字段返回 None。"""
        content = """---
title: Test
author: 张三
---
正文"""
        d = BiweeklyCollector._extract_date_from_frontmatter(content)
        assert d is None


# ── 人名提取 ──────────────────────────────────────────────


class TestExtractPersonFromFilename:
    """验证 _extract_person_from_filename 从文件名提取人名。"""

    def test_standard_weekly_report(self):
        """标准成员周报格式: YYYYMMDD-周报-w{week}-{name}.md。"""
        name = BiweeklyCollector._extract_person_from_filename("20250621-周报-w25-李四.md")
        assert name == "李四"

    def test_two_char_name(self):
        """两字人名。"""
        name = BiweeklyCollector._extract_person_from_filename("20250613-周报-w24-张三.md")
        assert name == "张三"

    def test_three_char_name(self):
        """三字人名。"""
        name = BiweeklyCollector._extract_person_from_filename("20250608-周报-w23-王小明.md")
        assert name == "王小明"

    def test_not_weekly_report(self):
        """非成员周报格式返回 None。"""
        name = BiweeklyCollector._extract_person_from_filename("20260702-项目讨论-项目Alpha检测.md")
        assert name is None

    def test_fallback_dash_format(self):
        """fallback: 尝试横线后两字中文。"""
        name = BiweeklyCollector._extract_person_from_filename("周报-刘备.md")
        assert name == "刘备"


# ── 引用标签构建 ──────────────────────────────────────────


class TestBuildCitationLabel:
    """验证 _build_citation_label 构建简化引用标签。"""

    def test_weekly_report_person(self):
        """成员周报提取人名。"""
        label = BiweeklyCollector._build_citation_label(
            "20250621-周报-w25-李四.md", "成员周报")
        assert "李四周报" in label
        assert "0621" in label or "06" in label

    def test_meeting_minutes(self):
        """会议纪要保留完整描述。"""
        label = BiweeklyCollector._build_citation_label(
            "20260702-项目讨论-项目Alpha检测-H2检出率目标测算.md", "会议纪要")
        assert "项目讨论" in label
        assert "项目Alpha" in label
        assert "0702" in label

    def test_discussion(self):
        """讨论思考保留完整描述。"""
        label = BiweeklyCollector._build_citation_label(
            "20260701-内部讨论-质检执行智能化.md", "讨论思考")
        assert "内部讨论" in label
        assert "质检" in label
        assert "0701" in label
