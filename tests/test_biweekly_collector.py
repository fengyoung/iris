"""BiweeklyCollector 单元测试。"""

from __future__ import annotations

import json
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iris.analysis._biweekly_collector import BiweeklyCollector


def _make_bundle(tmp_path: Path, source_dir: Path | None = None,
                 biweekly_cfg: dict | None = None) -> types.SimpleNamespace:
    """构建最小化 ConfigBundle 替代对象。"""
    sources = {}
    if source_dir is not None:
        source_dir.mkdir(parents=True, exist_ok=True)
        sources = {"main": {"enabled": True, "path": str(source_dir)}}
    return types.SimpleNamespace(
        root=tmp_path,
        app={"biweekly_report": biweekly_cfg or {}},
        data_source={"sources": sources},
        llm={},
        wiki=None,
    )


# ── load_op_document ────────────────────────────────────────────

class TestLoadOpDocument:
    def test_returns_empty_when_no_source(self, tmp_path):
        bundle = _make_bundle(tmp_path)
        c = BiweeklyCollector(bundle)
        assert c.load_op_document() == ""

    def test_returns_empty_when_no_op_dir(self, tmp_path):
        src = tmp_path / "SOURCE"
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        assert c.load_op_document() == ""

    def test_loads_latest_md_file(self, tmp_path):
        src = tmp_path / "SOURCE"
        op_dir = src / "01-目标管理"
        op_dir.mkdir(parents=True)
        (op_dir / "20260101-OP.md").write_text("旧版OP内容", encoding="utf-8")
        (op_dir / "20260701-OP.md").write_text("新版OP内容", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        result = c.load_op_document()
        assert result == "新版OP内容"

    def test_strips_frontmatter(self, tmp_path):
        src = tmp_path / "SOURCE"
        op_dir = src / "01-目标管理"
        op_dir.mkdir(parents=True)
        (op_dir / "20260701-OP.md").write_text(
            "---\ntitle: OP\n---\n\n正文内容", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        assert c.load_op_document() == "正文内容"

    def test_cached_on_second_call(self, tmp_path):
        src = tmp_path / "SOURCE"
        op_dir = src / "01-目标管理"
        op_dir.mkdir(parents=True)
        f = op_dir / "20260701-OP.md"
        f.write_text("初始内容", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        first = c.load_op_document()
        # 修改文件后第二次调用应仍返回缓存值
        f.write_text("修改后内容", encoding="utf-8")
        second = c.load_op_document()
        assert first == second == "初始内容"

    def test_prefers_dept_level_over_team_okr(self, tmp_path):
        """目录同时存在部门级和团队 OKR 文件时，应优先取部门级文件。"""
        src = tmp_path / "SOURCE"
        op_dir = src / "01-目标管理"
        op_dir.mkdir(parents=True)
        # 团队 OKR（按 team_okr_patterns 排除，文件名命中「-团队名-人名-OKR」）
        (op_dir / "20260701-数据部门-测试团队-张三-OKR.md").write_text(
            "团队OKR内容", encoding="utf-8")
        # 部门级 OP（应被选中）
        (op_dir / "20260701-数据部门-OP规划.md").write_text(
            "部门OP内容", encoding="utf-8")
        bundle = _make_bundle(
            tmp_path, source_dir=src,
            biweekly_cfg={"dept_op_keyword": "数据部门",
                          "team_okr_patterns": ["测试团队"]},
        )
        c = BiweeklyCollector(bundle)
        result = c.load_op_document()
        assert result == "部门OP内容"

    def test_excludes_file_matching_team_okr_pattern(self, tmp_path):
        """文件名命中 team_okr_patterns 时应被排除，只保留部门级文件。"""
        src = tmp_path / "SOURCE"
        op_dir = src / "01-目标管理"
        op_dir.mkdir(parents=True)
        # 自定义 team_okr_patterns
        biweekly_cfg = {"team_okr_patterns": ["测试团队"], "dept_op_keyword": "数据部门"}
        (op_dir / "20260701-数据部门-测试团队-李四-OKR.md").write_text(
            "团队OKR", encoding="utf-8")
        (op_dir / "20260701-数据部门-年度规划.md").write_text(
            "年度规划", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src, biweekly_cfg=biweekly_cfg)
        c = BiweeklyCollector(bundle)
        result = c.load_op_document()
        assert result == "年度规划"

    def test_fallback_when_no_dept_keyword_file(self, tmp_path, caplog):
        """没有含 dept_op_keyword 的文件时，fallback 到目录第一个文件并发出 warning。"""
        import logging
        src = tmp_path / "SOURCE"
        op_dir = src / "01-目标管理"
        op_dir.mkdir(parents=True)
        # 配置了部门关键词，但目录中只有不含关键词的普通文件
        (op_dir / "20260701-其他团队-OP.md").write_text("兜底内容", encoding="utf-8")
        bundle = _make_bundle(
            tmp_path, source_dir=src,
            biweekly_cfg={"dept_op_keyword": "数据部门"},
        )
        c = BiweeklyCollector(bundle)
        with caplog.at_level(logging.WARNING):
            result = c.load_op_document()
        assert result == "兜底内容"
        # fallback 时应有 warning 提示
        assert any("兜底" in r.message or "部门级 OP" in r.message
                   for r in caplog.records if r.levelno >= logging.WARNING)

    def test_custom_dept_op_keyword(self, tmp_path):
        """dept_op_keyword 配置后，按新关键词筛选文件。"""
        src = tmp_path / "SOURCE"
        op_dir = src / "01-目标管理"
        op_dir.mkdir(parents=True)
        biweekly_cfg = {"dept_op_keyword": "AI研发部", "team_okr_patterns": []}
        (op_dir / "20260701-数据部门-OP.md").write_text("旧部门", encoding="utf-8")
        (op_dir / "20260701-AI研发部-OP.md").write_text("新部门", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src, biweekly_cfg=biweekly_cfg)
        c = BiweeklyCollector(bundle)
        result = c.load_op_document()
        assert result == "新部门"


# ── collect_recent_files ────────────────────────────────────────

class TestCollectRecentFiles:
    def _setup_source(self, tmp_path: Path) -> Path:
        src = tmp_path / "SOURCE"
        for d in ["05-会议纪要", "07-成员周报", "04-讨论思考", "03-方案报告"]:
            (src / d).mkdir(parents=True)
        return src

    def test_returns_empty_when_no_source(self, tmp_path):
        bundle = _make_bundle(tmp_path)
        c = BiweeklyCollector(bundle)
        assert c.collect_recent_files(datetime.now() - timedelta(days=14)) == []

    def test_filters_by_date(self, tmp_path):
        src = self._setup_source(tmp_path)
        since = datetime.now() - timedelta(days=7)
        # 一个在窗口内，一个在窗口外
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        new_date = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
        (src / "05-会议纪要" / f"{old_date}-旧会议.md").write_text("旧内容", encoding="utf-8")
        (src / "05-会议纪要" / f"{new_date}-新会议.md").write_text("新内容", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        results = c.collect_recent_files(since)
        labels = [f["label"] for f in results]
        assert any("新会议" in l for l in labels)
        assert not any("旧会议" in l for l in labels)

    def test_member_report_dedup_keeps_latest(self, tmp_path):
        src = self._setup_source(tmp_path)
        since = datetime.now() - timedelta(days=14)
        d1 = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        d2 = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
        (src / "07-成员周报" / f"{d1}-周报-w25-张三.md").write_text("旧周报", encoding="utf-8")
        (src / "07-成员周报" / f"{d2}-周报-w27-张三.md").write_text("新周报", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        results = c.collect_recent_files(since)
        person_reports = [f for f in results if f["dir"] == "成员周报"]
        assert len(person_reports) == 1
        assert person_reports[0]["content"] == "新周报"

    def test_strips_frontmatter_from_content(self, tmp_path):
        src = self._setup_source(tmp_path)
        since = datetime.now() - timedelta(days=14)
        d = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        (src / "05-会议纪要" / f"{d}-会议.md").write_text(
            "---\ntitle: 会议\n---\n\n会议正文", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        results = c.collect_recent_files(since)
        assert results[0]["content"] == "会议正文"

    def test_custom_dir_map_from_config(self, tmp_path):
        src = tmp_path / "SOURCE"
        custom_dir = src / "99-自定义"
        custom_dir.mkdir(parents=True)
        d = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        (custom_dir / f"{d}-自定义文件.md").write_text("内容", encoding="utf-8")
        biweekly_cfg = {
            "data_sources": ["自定义分类"],
            "dir_map": {"自定义分类": ["99-自定义", "自定义分类"]},
        }
        bundle = _make_bundle(tmp_path, source_dir=src, biweekly_cfg=biweekly_cfg)
        c = BiweeklyCollector(bundle)
        results = c.collect_recent_files(datetime.now() - timedelta(days=7))
        assert len(results) == 1
        assert results[0]["dir"] == "自定义分类"


# ── 静态工具方法 ─────────────────────────────────────────────────

class TestBuildCitationLabel:
    def test_member_report(self):
        label = BiweeklyCollector._build_citation_label("20260703-周报-w27-张三.md", "成员周报")
        assert label == "张三周报-0703"

    def test_meeting_minutes(self):
        label = BiweeklyCollector._build_citation_label("20260702-项目讨论-项目Alpha检测.md", "会议纪要")
        assert label == "项目讨论-项目Alpha检测-0702"

    def test_discussion(self):
        label = BiweeklyCollector._build_citation_label("20260701-内部讨论-智能检测.md", "讨论思考")
        assert label == "内部讨论-智能检测-0701"

    def test_fallback_dir(self):
        label = BiweeklyCollector._build_citation_label("20260701-未知文件.md", "其他目录")
        assert "0701" in label


# ── load_recent_biweeklies ───────────────────────────────────────

class TestLoadRecentBiweeklies:
    def test_returns_empty_when_no_report_dir(self, tmp_path):
        src = tmp_path / "SOURCE"
        src.mkdir()
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        assert c.load_recent_biweeklies(since_days=35) == []

    def test_loads_files_within_window(self, tmp_path):
        src = tmp_path / "SOURCE"
        report_dir = src / "06-我的周报"
        report_dir.mkdir(parents=True)
        recent = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        old = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")
        (report_dir / f"双周报-w27-testuser-{recent}.md").write_text("近期报告", encoding="utf-8")
        (report_dir / f"双周报-w22-testuser-{old}.md").write_text("旧报告", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        results = c.load_recent_biweeklies(since_days=35)
        assert len(results) == 1
        assert results[0]["content"] == "近期报告"

    def test_strips_footer(self, tmp_path):
        src = tmp_path / "SOURCE"
        (src / "06-我的周报").mkdir(parents=True)
        d = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        (src / "06-我的周报" / f"双周报-w27-testuser-{d}.md").write_text(
            "报告正文\n\n> This report was written by Iris.", encoding="utf-8")
        bundle = _make_bundle(tmp_path, source_dir=src)
        c = BiweeklyCollector(bundle)
        results = c.load_recent_biweeklies(since_days=35)
        assert "This report was" not in results[0]["content"]
        assert "报告正文" in results[0]["content"]
