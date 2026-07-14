"""trello/formatter.py 纯格式化函数测试。"""

from __future__ import annotations

from iris.trello.formatter import format_trello_payload


class TestFormatDispatch:
    def test_unknown_command_returns_empty(self):
        assert format_trello_payload("no-such-command", {}) == ""

    def test_status(self):
        payload = {
            "board_name": "看板X",
            "board_url": "http://x",
            "total_lists": 3,
            "total_incomplete": 5,
            "by_list": {"TODO": 3, "DOING": 2},
            "by_category": {"work": 5},
            "today_count": 1,
            "this_week_count": 4,
            "overdue_count": 0,
        }
        out = format_trello_payload("status", payload)
        assert "看板：看板X" in out
        assert "未完成待办总数：5" in out
        assert "- TODO: 3" in out
        assert "今日待办：1" in out

    def test_lists(self):
        payload = {"lists": [{"name": "TODO", "id": "l1"}, {"name": "DOING", "id": "l2"}]}
        out = format_trello_payload("lists", payload)
        assert "- TODO (l1)" in out
        assert "- DOING (l2)" in out

    def test_cards_list(self):
        payload = {
            "total": 1,
            "cards": [
                {
                    "list_name": "TODO",
                    "name": "任务A",
                    "labels": [{"name": "工作", "color": "blue"}],
                    "due": "2026-07-20",
                }
            ],
        }
        out = format_trello_payload("list", payload)
        assert "待办数量：1" in out
        assert "[TODO] 任务A" in out
        assert "工作(blue)" in out
        assert "截止：2026-07-20" in out

    def test_card_detail(self):
        payload = {
            "name": "任务B",
            "id": "c1",
            "list_name": "TODO",
            "desc": "",
            "due": "无",
            "due_complete": False,
            "labels": [],
            "category": "work",
            "url": "http://c",
        }
        out = format_trello_payload("show", payload)
        assert "标题：任务B" in out
        assert "描述：(无)" in out
        assert "分类：work" in out

    def test_summarize_passthrough(self):
        assert format_trello_payload("summarize", {"summary": "摘要文本"}) == "摘要文本"

    def test_prioritize(self):
        payload = {"items": [{"suggested_order": 1, "name": "先做A", "priority_reason": "最紧急"}]}
        out = format_trello_payload("prioritize", payload)
        assert "1. 先做A" in out
        assert "理由：最紧急" in out

    def test_discover_with_candidates(self):
        payload = {
            "candidates": [
                {"title": "候选1", "confidence": 0.9, "category": "work", "desc": "说明", "context": "上下文"}
            ],
            "existing_similar": [],
            "auto_created": [],
        }
        out = format_trello_payload("discover", payload)
        assert "发现 1 个候选待办" in out
        assert "候选1" in out
        assert "🔵 工作" in out

    def test_discover_empty(self):
        out = format_trello_payload("discover", {"candidates": [], "existing_similar": [], "auto_created": []})
        assert "未发现待办事项" in out

    def test_discover_auto_created(self):
        payload = {
            "candidates": [],
            "existing_similar": [{"name": "重复项"}],
            "auto_created": [{"name": "已建A"}],
        }
        out = format_trello_payload("discover", payload)
        assert "已自动创建 1 个待办" in out
        assert "已建A" in out
        assert "跳过 1 个重复项" in out
