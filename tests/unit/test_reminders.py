"""ReminderEngine 主动提醒引擎单元测试（零 LLM，纯文件系统）。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from iris.analysis.reminders import ReminderEngine, _parse_yyyymmdd

_NOW = datetime(2026, 7, 29)


def _make_config(tmp_path, *, wiki_root=None, reminders_cfg=None):
    """构造引擎所需的最小配置对象（dict 风格访问）。"""
    source_root = tmp_path / "SOURCE"
    source_root.mkdir(exist_ok=True)
    return SimpleNamespace(
        app={"reminders": reminders_cfg or {}},
        data_source={
            "default_source": "main_source",
            "sources": {"main_source": {"path": str(source_root)}},
        },
        wiki={"wiki_root": str(wiki_root)} if wiki_root else None,
        root=tmp_path,
    ), source_root


def _write(path, content="内容"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 基础行为
# ─────────────────────────────────────────────────────────────

class TestReminderEngineBasics:
    def test_missing_source_root_skipped(self, tmp_path):
        config = SimpleNamespace(app={}, data_source={"default_source": "x", "sources": {}},
                                 wiki=None, root=tmp_path)
        result = ReminderEngine(config).collect(now=_NOW)
        assert result["status"] == "skipped"
        assert result["signal_count"] == 0

    def test_empty_source_no_signals(self, tmp_path):
        config, _ = _make_config(tmp_path)
        result = ReminderEngine(config).collect(now=_NOW)
        assert result["status"] == "ok"
        assert result["signal_count"] == 0

    def test_config_overrides_merged(self, tmp_path):
        config, _ = _make_config(tmp_path, reminders_cfg={
            "category_inactive_days": 60,
            "category_overrides": {"01-目标管理": 7},
        })
        engine = ReminderEngine(config)
        assert engine._cfg["category_inactive_days"] == 60
        assert engine._cfg["category_overrides"]["01-目标管理"] == 7
        # 默认 overrides 保留
        assert engine._cfg["category_overrides"]["05-会议纪要"] == 14


# ─────────────────────────────────────────────────────────────
# 信号 1：栏目断供
# ─────────────────────────────────────────────────────────────

class TestCategoryInactive:
    def test_stale_category_flagged(self, tmp_path):
        config, source_root = _make_config(tmp_path)
        _write(source_root / "01-目标管理" / "20260601-目标-规划.md")  # 58 天前
        result = ReminderEngine(config).collect(now=_NOW)
        types = [s["type"] for s in result["signals"]]
        assert "category_inactive" in types
        signal = next(s for s in result["signals"] if s["type"] == "category_inactive")
        assert signal["target"] == "01-目标管理"
        assert signal["days"] == 58

    def test_active_category_not_flagged(self, tmp_path):
        config, source_root = _make_config(tmp_path)
        _write(source_root / "01-目标管理" / "20260728-目标-规划.md")  # 1 天前
        result = ReminderEngine(config).collect(now=_NOW)
        assert result["signal_count"] == 0

    def test_override_threshold_applies(self, tmp_path):
        # 05-会议纪要 阈值 14 天：20 天前的文件触发；01 默认 30 天不触发
        config, source_root = _make_config(tmp_path)
        _write(source_root / "05-会议纪要" / "20260709-会议-某会.md")  # 20 天前
        _write(source_root / "01-目标管理" / "20260709-目标-某事.md")
        result = ReminderEngine(config).collect(now=_NOW)
        targets = [s["target"] for s in result["signals"]]
        assert "05-会议纪要" in targets
        assert "01-目标管理" not in targets

    def test_non_category_dirs_ignored(self, tmp_path):
        config, source_root = _make_config(tmp_path)
        _write(source_root / "v2-data" / "20250101-旧数据.md")
        result = ReminderEngine(config).collect(now=_NOW)
        assert result["signal_count"] == 0

    def test_nested_monthly_dirs_scanned(self, tmp_path):
        # v3.19.17 年月归档：日期取子目录内最新文件
        config, source_root = _make_config(tmp_path)
        _write(source_root / "03-方案报告" / "2026-06" / "20260615-方案-旧.md")
        _write(source_root / "03-方案报告" / "2026-07" / "20260728-方案-新.md")
        result = ReminderEngine(config).collect(now=_NOW)
        assert result["signal_count"] == 0  # 最新 1 天前，不告警


# ─────────────────────────────────────────────────────────────
# 信号 2：成员周报缺失
# ─────────────────────────────────────────────────────────────

class TestWeeklyReportMissing:
    def test_gap_member_flagged(self, tmp_path):
        config, source_root = _make_config(tmp_path)
        _write(source_root / "07-成员周报" / "20260727-周报-w31-张三.md")   # 2 天，正常
        _write(source_root / "07-成员周报" / "20260701-周报-w27-李四.md")   # 28 天，断档
        result = ReminderEngine(config).collect(now=_NOW)
        missing = [s for s in result["signals"] if s["type"] == "weekly_report_missing"]
        assert len(missing) == 1
        assert missing[0]["target"] == "李四"
        assert missing[0]["days"] == 28

    def test_left_member_not_flagged(self, tmp_path):
        # 超出 45 天 roster 窗口：视为离开统计范围
        config, source_root = _make_config(tmp_path)
        _write(source_root / "07-成员周报" / "20260301-周报-w9-王五.md")
        result = ReminderEngine(config).collect(now=_NOW)
        missing = [s for s in result["signals"] if s["type"] == "weekly_report_missing"]
        assert missing == []

    def test_latest_report_wins(self, tmp_path):
        # 同一人多份周报：按最新一份判定
        config, source_root = _make_config(tmp_path)
        _write(source_root / "07-成员周报" / "20260601-周报-w23-张三.md")
        _write(source_root / "07-成员周报" / "20260727-周报-w31-张三.md")
        result = ReminderEngine(config).collect(now=_NOW)
        missing = [s for s in result["signals"] if s["type"] == "weekly_report_missing"]
        assert missing == []

    def test_monthly_subdir_supported(self, tmp_path):
        config, source_root = _make_config(tmp_path)
        _write(source_root / "07-成员周报" / "2026-07" / "20260701-周报-w27-李四.md")
        result = ReminderEngine(config).collect(now=_NOW)
        missing = [s for s in result["signals"] if s["type"] == "weekly_report_missing"]
        assert len(missing) == 1

    def test_non_report_files_ignored(self, tmp_path):
        config, source_root = _make_config(tmp_path)
        _write(source_root / "07-成员周报" / "README.md")
        result = ReminderEngine(config).collect(now=_NOW)
        missing = [s for s in result["signals"] if s["type"] == "weekly_report_missing"]
        assert missing == []


# ─────────────────────────────────────────────────────────────
# 信号 3：项目停滞
# ─────────────────────────────────────────────────────────────

class TestProjectStalled:
    def _make_wiki_page(self, wiki_root, name, fingerprint_paths):
        from iris.wiki.discovery_utils import inject_source_fingerprint
        content = f"---\ntitle: {name}\ntype: project\nupdated: 2026-07-29\n---\n\n# {name}\n"
        content = inject_source_fingerprint(content, {p: "abc123def456" for p in fingerprint_paths})
        page = wiki_root / "03-项目" / f"{name}.md"
        _write(page, content)

    def test_stalled_project_flagged(self, tmp_path):
        wiki_root = tmp_path / "WIKI"
        config, source_root = _make_config(tmp_path, wiki_root=wiki_root)
        rel = "03-方案报告/20260601-方案-旧项目.md"  # 58 天前
        _write(source_root / rel)
        self._make_wiki_page(wiki_root, "项目-旧项目", [rel])
        result = ReminderEngine(config).collect(now=_NOW)
        stalled = [s for s in result["signals"] if s["type"] == "project_stalled"]
        assert len(stalled) == 1
        assert stalled[0]["target"] == "项目-旧项目"
        assert stalled[0]["days"] == 58

    def test_active_project_not_flagged(self, tmp_path):
        wiki_root = tmp_path / "WIKI"
        config, source_root = _make_config(tmp_path, wiki_root=wiki_root)
        old = "03-方案报告/20260601-方案-项目.md"
        fresh = "05-会议纪要/20260728-会议-项目周会.md"
        _write(source_root / old)
        _write(source_root / fresh)
        # 任一引用源新鲜 → 不算停滞
        self._make_wiki_page(wiki_root, "项目-活跃项目", [old, fresh])
        result = ReminderEngine(config).collect(now=_NOW)
        stalled = [s for s in result["signals"] if s["type"] == "project_stalled"]
        assert stalled == []

    def test_page_without_fingerprint_skipped(self, tmp_path):
        wiki_root = tmp_path / "WIKI"
        config, source_root = _make_config(tmp_path, wiki_root=wiki_root)
        page = wiki_root / "03-项目" / "项目-无指纹.md"
        _write(page, "---\ntitle: 项目-无指纹\ntype: project\n---\n\n正文\n")
        result = ReminderEngine(config).collect(now=_NOW)
        stalled = [s for s in result["signals"] if s["type"] == "project_stalled"]
        assert stalled == []

    def test_all_sources_missing_skipped(self, tmp_path):
        # 引用源全部缺失 → 断链问题，交给 wiki-lint，不产生停滞信号
        wiki_root = tmp_path / "WIKI"
        config, _ = _make_config(tmp_path, wiki_root=wiki_root)
        self._make_wiki_page(wiki_root, "项目-断链", ["gone/missing.md"])
        result = ReminderEngine(config).collect(now=_NOW)
        stalled = [s for s in result["signals"] if s["type"] == "project_stalled"]
        assert stalled == []

    def test_backup_page_skipped(self, tmp_path):
        """wiki-update 备份文件（*.bak.1.md）不产生重复停滞信号。"""
        wiki_root = tmp_path / "WIKI"
        config, source_root = _make_config(tmp_path, wiki_root=wiki_root)
        rel = "03-方案报告/20260601-方案-旧项目.md"  # 58 天前
        _write(source_root / rel)
        self._make_wiki_page(wiki_root, "项目-旧项目", [rel])
        # 模拟 wiki-update 备份：同内容 + .bak.1 后缀（也会被 glob 扫到）
        bak = wiki_root / "03-项目" / "项目-旧项目.bak.1.md"
        _write(bak, (wiki_root / "03-项目" / "项目-旧项目.md").read_text(encoding="utf-8"))
        result = ReminderEngine(config).collect(now=_NOW)
        stalled = [s for s in result["signals"] if s["type"] == "project_stalled"]
        assert len(stalled) == 1  # 备份不产生第二个信号


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

class TestParseYyyymmdd:
    def test_valid(self):
        assert _parse_yyyymmdd("20260729") == datetime(2026, 7, 29)

    def test_invalid(self):
        assert _parse_yyyymmdd("2026072") is None
        assert _parse_yyyymmdd("abcdefgh") is None
