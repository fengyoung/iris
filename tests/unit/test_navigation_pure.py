"""navigation.py 单元测试 — 链接检测、changelog、原子写入、字符匹配。

覆盖 _is_wiki_broken_link（7 豁免分支）、append_changelog、_atomic_write、
_char_sequence_match 等核心函数。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from iris.wiki.navigation import (
    append_changelog,
    _atomic_write,
    _is_wiki_broken_link,
    _char_sequence_match,
)


# ── _char_sequence_match ──────────────────────────────────────────


class TestCharSequenceMatch:
    def test_exact_match(self):
        assert _char_sequence_match("Alpha", "Alpha") is True

    def test_subsequence_at_start(self):
        assert _char_sequence_match("Alpha", "AlphaProject手机拆修") is True

    def test_subsequence_scattered(self):
        """字符散落在长字符串中但保持顺序。"""
        assert _char_sequence_match("abc", "xaybzc") is True

    def test_no_match_order_broken(self):
        """字符存在但顺序不对。"""
        assert _char_sequence_match("abc", "xcybza") is False

    def test_empty_short(self):
        assert _char_sequence_match("", "anything") is True

    def test_longer_than_target(self):
        assert _char_sequence_match("abcdef", "abc") is False


# ── _is_wiki_broken_link ──────────────────────────────────────────


class TestIsWikiBrokenLink:
    """覆盖 7 个豁免分支 + broken 路径。"""

    def test_source_ref_pattern(self):
        """源文档引用豁免（行 170-171）。"""
        result = _is_wiki_broken_link("会议纪要/20260518-讨论", {})
        assert result is None

    def test_source_ref_no_path_prefix(self):
        result = _is_wiki_broken_link("20260601-周报-w25-李四", {})
        assert result is None

    def test_noise_link(self):
        """超短噪音豁免（行 173-174）。"""
        assert _is_wiki_broken_link(".", {}) is None
        assert _is_wiki_broken_link("#", {}) is None
        assert _is_wiki_broken_link("__", {}) is None

    def test_known_tech_term(self):
        """知名技术术语豁免（行 176-177）。"""
        result = _is_wiki_broken_link("BERT", {})
        assert result is None

    def test_external_concept_pattern(self):
        """外部概念正则豁免（行 179-181）。"""
        result = _is_wiki_broken_link("demo_app", {})
        assert result is None

    def test_exact_match_in_pages(self):
        """精确匹配到页面标题（行 183-184）。"""
        pages = {"张三": {"title": "张三", "path": "/x"}}
        result = _is_wiki_broken_link("张三", pages)
        assert result is None

    def test_substring_match(self):
        """模糊子串匹配（行 186-188）。"""
        pages = {"大模型应用实践": {"title": "大模型应用实践", "path": "/x"}}
        result = _is_wiki_broken_link("大模型", pages)
        assert result is None

    def test_prefix_match(self):
        """前缀匹配（行 190-193）。"""
        pages = {"AlphaTeam2026年目标与规划": {"title": "AlphaTeam2026年目标与规划", "path": "/x"}}
        result = _is_wiki_broken_link("AlphaTeam", pages)
        assert result is None

    def test_norm_equal_match(self):
        """去空格/标点后精确匹配（行 198-201）。"""
        pages = {"Agentic Cloud 与 AI Agent": {"title": "Agentic Cloud 与 AI Agent", "path": "/x"}}
        result = _is_wiki_broken_link("AgenticCloud与AIAgent", pages)
        assert result is None

    def test_norm_substring_match(self):
        """去空格/标点后子串匹配（行 202-205，需 norm 后 >=6 字符）。"""
        pages = {"大模型应用实践平台": {"title": "大模型应用实践平台", "path": "/x"}}
        result = _is_wiki_broken_link("大模型-应用实践", pages)
        assert result is None

    def test_char_sequence_match_branch(self):
        """字符级子序列匹配（行 207-210）。"""
        pages = {"AlphaProject手机拆修检测": {"title": "AlphaProject手机拆修检测", "path": "/x"}}
        result = _is_wiki_broken_link("AlphaProject", pages)
        assert result is None

    def test_decimal_normalize_match(self):
        """连写数字修复匹配（行 212-216）。"""
        pages = {"v1.0 发布计划": {"title": "v1.0 发布计划", "path": "/x"}}
        result = _is_wiki_broken_link("v10发布计划", pages)
        assert result is None

    def test_truly_broken(self):
        """真正的断裂链接。"""
        pages = {"张三": {"title": "张三", "path": "/x"}}
        result = _is_wiki_broken_link("不存在的页面", pages)
        assert result == "broken"


# ── append_changelog ──────────────────────────────────────────────


class TestAppendChangelog:
    """覆盖 changelog 写入两条路径（新建 + 追加）。"""

    def test_creates_file_when_missing(self, tmp_path):
        """文件不存在时初始化（行 91-92）。"""
        wiki_root = tmp_path / "wiki"
        wiki_root.mkdir()
        append_changelog(wiki_root, "新增页面 [[张三]]")
        changelog = wiki_root / "changelog.md"
        assert changelog.exists()
        content = changelog.read_text(encoding="utf-8")
        assert "# 变更日志" in content
        assert "新增页面 [[张三]]" in content

    def test_appends_when_file_exists(self, tmp_path):
        """文件已存在时追加（行 93-95）。"""
        wiki_root = tmp_path / "wiki"
        wiki_root.mkdir()
        cl = wiki_root / "changelog.md"
        cl.write_text("# 变更日志\n\n", encoding="utf-8")
        append_changelog(wiki_root, "第一次")
        append_changelog(wiki_root, "第二次")
        content = cl.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert "第一次" in lines[-2]
        assert "第二次" in lines[-1]


# ── _atomic_write ─────────────────────────────────────────────────


class TestAtomicWrite:
    def test_without_bundle_direct_write(self, tmp_path):
        """bundle=None 时直接写入（行 160-161）。"""
        path = tmp_path / "test.md"
        _atomic_write(path, "hello", bundle=None)
        assert path.read_text(encoding="utf-8") == "hello"

    def test_with_bundle_calls_safe_write(self, tmp_path):
        """bundle 不为 None 时走 safe_write_text（行 157-159）。"""
        path = tmp_path / "test.md"

        class FakeBundle:
            pass

        bundle = FakeBundle()
        with patch("iris.core.write_guard.safe_write_text") as mock_write:
            _atomic_write(path, "world", bundle=bundle)
            mock_write.assert_called_once_with(
                path, "world", bundle, allow_existing_outside=True,
            )
