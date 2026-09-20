"""敏感文档判定 — 单元测试。

正例全部取自真实泄漏事件（2026-09-20 调薪/人员盘点内容渗入 Wiki 与 ASR 热词），
反例全部取自真实正常文档——**反例是本模块最重要的资产**：敏感判定的失效模式
不是漏判（漏判只会少一个候选），而是误判（会把正常业务文档整篇排除出 Wiki）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from iris.wiki._sensitive import (
    SENSITIVE_TERMS,
    filter_sensitive_hits,
    filter_sensitive_paths,
    filter_sensitive_terms,
    is_sensitive_path,
    is_sensitive_term,
    is_sensitive_title,
    matched_sensitive_term,
    normalize_for_match,
)


class TestNormalizeForMatch:
    def test_strips_separators_and_lowercases(self):
        assert normalize_for_match("2026-核心Leader盘点 — 冯扬团队") == "2026核心leader盘点冯扬团队"

    def test_handles_underscore_and_space(self):
        assert normalize_for_match("Leader_盘点") == normalize_for_match("leader 盘点")

    def test_empty_input(self):
        assert normalize_for_match("") == ""


class TestIsSensitivePath:
    """真实 SOURCE 路径上的判定。"""

    @pytest.mark.parametrize("path", [
        "02-部门管理/2026/20260806-数据智能部2026最终调薪方案.md",
        "02-部门管理/2026/20260806-质检研发团队2026最终调薪方案.md",
        "02-部门管理/2026/20260723-数据智能部2026调薪规则与推荐方案.md",
        "02-部门管理/2026/20260723-质检研发2026调薪规则与推荐方案.md",
        "02-部门管理/2026/20260716-人员盘点01-fengyoung.md",
        "02-部门管理/2026/20260716-人员盘点01-中间过程-fengyoung.md",
        "02-部门管理/2026/20260721-人员盘点02-fengyoung.md",
        "02-部门管理/2026/20260721-人员盘点02-中间过程-fengyoung.md",
    ])
    def test_real_sensitive_documents(self, path):
        assert is_sensitive_path(path) is True

    @pytest.mark.parametrize("path", [
        # 裸「盘点」不是敏感词：5 篇正常业务文档
        "03-方案报告/2026/20260129-首页推荐feeds系统性盘点.md",
        "09-工作简报/202608/20260804-简报-搜推特征盘点对齐（from飞书）.md",
        "04-讨论思考/202607/20260728-内部沟通-硬件团队下半年重点项目盘点与团队规划（with万涛）.md",
        "05-会议纪要/202608/20260805-项目讨论-XRay项目「少人工」数据盘点&可行性讨论.md",
        # 裸「定级」不是敏感词：24 篇「拍照3.0外观定级」业务
        "03-方案报告/2026/20260326-拍照3.0手机外观定级方案（meeting汇报）.md",
        "05-会议纪要/202609/20260916-项目讨论-笔记本外观定级标准与算法拍摄方案讨论.md",
        # 裸「校准」不是敏感词
        "09-工作简报/202608/20260812-简报-标准色卡校准白平衡讨论（from飞书）.md",
        # 组织架构不是敏感文档
        "02-部门管理/2026/20260710-数据智能+质检研发组织架构-v202606.md",
        # 成员周报正文含敏感词，但文档身份不是
        "07-成员周报/202607/20260718-周报-w29-许凤翔.md",
    ])
    def test_real_normal_documents_not_flagged(self, path):
        assert is_sensitive_path(path) is False

    def test_only_basename_is_matched(self):
        """目录名不参与判定——命中必须来自文档自身的名字。"""
        assert is_sensitive_path("02-部门管理/2026/正常文档.md") is False

    def test_backslash_path(self):
        assert is_sensitive_path(r"02-部门管理\2026\20260806-数据智能部2026最终调薪方案.md") is True

    @pytest.mark.parametrize("path", ["", "   ", "02-部门管理/2026", "a"])
    def test_degenerate_inputs(self, path):
        assert is_sensitive_path(path) is False

    def test_non_string_does_not_raise(self):
        assert is_sensitive_path(None) is False
        assert is_sensitive_path(MagicMock()) is False


class TestIsSensitiveTitle:
    @pytest.mark.parametrize("title", [
        "数据智能部 2026 调薪方案",
        "2026-核心Leader盘点 — 冯扬团队 · 子表2评估过程记录",
        "2026-核心Leader盘点 — 冯扬团队 · 问答过程记录",
        "2.3 930 名单",
        "调薪方案",
    ])
    def test_real_leaked_candidates(self, title):
        assert is_sensitive_title(title) is True

    @pytest.mark.parametrize("title", [
        "质检大脑质检池页面标准类型筛选 PRD",
        "讨论框架 - XRay项目「少人工」方向讨论框架",
        "首页推荐feeds系统性盘点",
        "拍照3.0外观定级",
    ])
    def test_normal_titles(self, title):
        assert is_sensitive_title(title) is False

    def test_normalization_variants(self):
        assert is_sensitive_title("LEADER盘点") is True
        assert is_sensitive_title("2026 Leader 盘点") is True
        assert is_sensitive_title("2026_leader盘点") is True

    def test_separators_are_joined_before_matching(self):
        """归一化剥离分隔符，使「Leader-盘点」「Leader 盘点」等价命中。

        代价是「调-薪」这类被分隔符切开的写法也会命中。这是有意取舍：
        真实泄漏标题是「2026-核心Leader盘点 — 冯扬团队」，分隔符形态多变，
        漏判的代价（敏感内容进 Wiki）远高于误判的代价（少一个候选）。
        误判率的实际防线是全量语料回归——当前 1001 篇文档上误判为 0。
        """
        assert is_sensitive_title("2026-调-薪") is True
        assert is_sensitive_title("Leader-盘点") is True

    def test_substring_is_directional(self):
        """「落位盘点」不含「盘点落位」。"""
        assert is_sensitive_title("落位盘点") is False


class TestIsSensitiveTerm:
    @pytest.mark.parametrize("term", [
        "调薪方案", "930调薪", "2026调薪", "2025年度绩效", "202607盘点评估",
        "盘点落位", "202601盘点落位", "晋升确认", "绩效A级", "930名单",
    ])
    def test_real_leaked_hotwords(self, term):
        assert is_sensitive_term(term) is True

    @pytest.mark.parametrize("term", ["3C", "AI", "万涛", "搜推", "XRay拆机检测", "二奢"])
    def test_normal_hotwords(self, term):
        assert is_sensitive_term(term) is False


class TestMatchedSensitiveTerm:
    def test_returns_matched_term(self):
        assert matched_sensitive_term("20260806-数据智能部2026最终调薪方案.md") == "调薪"

    def test_returns_none_when_clean(self):
        assert matched_sensitive_term("搜推特征盘点") is None


class TestFilters:
    def test_filter_sensitive_terms_keeps_order(self):
        terms = ["调薪方案", "AI", "绩效A级", "万搜", "万涛"]
        assert filter_sensitive_terms(terms) == ["AI", "万搜", "万涛"]

    def test_filter_sensitive_terms_on_drop_callback(self):
        dropped = []
        filter_sensitive_terms(["调薪方案", "AI"], on_drop=lambda t, m: dropped.append((t, m)))
        assert dropped == [("调薪方案", "调薪")]

    def test_filter_sensitive_terms_tolerates_non_str(self):
        assert filter_sensitive_terms(["AI", None, 42]) == ["AI", None, 42]

    def test_filter_sensitive_paths(self):
        paths = ["a/调薪方案.md", "a/正常.md", "b/人员盘点01.md"]
        assert filter_sensitive_paths(paths) == ["a/正常.md"]

    def test_filter_sensitive_hits(self):
        clean = MagicMock(relative_path="03-方案报告/正常.md")
        dirty = MagicMock(relative_path="02-部门管理/2026/调薪方案.md")
        assert filter_sensitive_hits([dirty, clean]) == [clean]

    def test_filter_sensitive_hits_tolerates_mock_without_str_path(self):
        """既有测试用 MagicMock() 造 hit，其 relative_path 也是 MagicMock。

        非 str 一律视为非敏感（保留），否则正则会以 TypeError 崩溃。
        """
        bare = MagicMock()
        assert filter_sensitive_hits([bare]) == [bare]

    def test_filter_sensitive_hits_missing_attribute(self):
        class Hit:
            pass

        hit = Hit()
        assert filter_sensitive_hits([hit]) == [hit]


class TestTermTable:
    def test_forbidden_bare_terms_absent(self):
        """裸词误伤率高，必须只能以组合词形式存在。"""
        for bare in ("盘点", "定级", "校准", "落位", "晋升"):
            assert bare not in SENSITIVE_TERMS

    def test_table_is_normalized(self):
        for term in SENSITIVE_TERMS:
            assert term == normalize_for_match(term)
