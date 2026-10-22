"""daily-start：源文档被删除也必须触发 Wiki 发现。

背景：`_auto_discover_wiki_for_daily` 此前只累加 `rebuilt_documents`。清理废弃 /
已合并的文档时，重建数为 0，整轮会被判成「无文档变更」而跳过发现——被删文档
相关的候选与页面无人复核，且输出上与「今天真的没变化」完全相同。
"""

from __future__ import annotations

import types

from iris.app.cli._handlers._system import _auto_discover_wiki_for_daily


def _summary(**stats) -> types.SimpleNamespace:
    return types.SimpleNamespace(build_stats=stats)


def _capture(monkeypatch) -> list:
    """替换 helpers._auto_discover_wiki，记录传入的 changed_count。"""
    import iris.app.cli.helpers as helpers

    calls: list = []

    def _fake(bundle, *, changed_count: int = 0):
        calls.append(changed_count)
        return {"triggered": True, "changed_documents": changed_count}

    monkeypatch.setattr(helpers, "_auto_discover_wiki", _fake)
    return calls


class TestDeletionTriggersDiscovery:
    def test_only_deletions_still_trigger(self, monkeypatch):
        calls = _capture(monkeypatch)

        result = _auto_discover_wiki_for_daily(
            object(), [_summary(rebuilt_documents=0, cleaned_documents=3)],
        )

        assert calls == [3], "只删不增的一轮必须把删除计入变更数"
        assert result["triggered"] is True

    def test_rebuilt_and_removed_are_summed(self, monkeypatch):
        calls = _capture(monkeypatch)

        _auto_discover_wiki_for_daily(object(), [
            _summary(rebuilt_documents=2, cleaned_documents=1),
            _summary(rebuilt_documents=1, cleaned_documents=0),
        ])

        assert calls == [4]

    def test_no_change_still_skips(self, monkeypatch):
        calls = _capture(monkeypatch)

        _auto_discover_wiki_for_daily(
            object(), [_summary(rebuilt_documents=0, cleaned_documents=0)],
        )

        assert calls == [0], "真正无变化时仍应传 0，由 helpers 侧判定跳过"

    def test_missing_stats_treated_as_zero(self, monkeypatch):
        """旧版 chunk summary 没有 cleaned_documents 字段，不得因此报错。"""
        calls = _capture(monkeypatch)

        _auto_discover_wiki_for_daily(object(), [_summary(rebuilt_documents=1)])

        assert calls == [1]
