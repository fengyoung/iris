"""测试 Trello 模块纯函数和模型 — trello/llm.py + trello/models.py。"""

from __future__ import annotations


from iris.trello.llm import _extract_json_block
from iris.trello.models import TrelloLabel, TrelloList, TrelloCard, TrelloBoard, TrelloOverview


class TestExtractJsonBlock:
    def test_plain_text(self):
        assert _extract_json_block('{"key": "value"}') == '{"key": "value"}'

    def test_markdown_block(self):
        result = _extract_json_block('```json\n{"key": "value"}\n```')
        assert result == '{"key": "value"}'

    def test_markdown_no_lang(self):
        result = _extract_json_block('```\n{"key": "value"}\n```')
        assert result == '{"key": "value"}'

    def test_strips_empty_first_line(self):
        result = _extract_json_block('```\n\n{"key": "value"}\n```')
        assert result == '{"key": "value"}'


class TestTrelloModels:
    def test_label(self):
        l = TrelloLabel(id="l1", name="重要", color="red")
        assert l.name == "重要"

    def test_list(self):
        tl = TrelloList(id="list1", name="TODO", id_board="board1")
        assert tl.name == "TODO"

    def test_card_minimal(self):
        c = TrelloCard(id="c1", name="测试卡片", desc="描述")
        assert c.name == "测试卡片"

    def test_card_with_labels(self):
        label = TrelloLabel(id="l1", name="bug", color="red")
        c = TrelloCard(id="c1", name="bug card", desc="",
                       labels=[label], list_name="TODO")
        assert len(c.labels) == 1
        assert c.labels[0].name == "bug"

    def test_board(self):
        b = TrelloBoard(id="b1", name="项目板", url="http://t.com", lists=[], labels=[])
        assert b.name == "项目板"

    def test_overview(self):
        o = TrelloOverview(board_name="项目板", total_incomplete=5)
        assert o.board_name == "项目板"
        assert o.total_incomplete == 5
        assert o.today == []

    def test_card_is_completed(self):
        c = TrelloCard(id="c1", name="done", closed=True)
        assert c.is_completed is True

    def test_card_category(self):
        label = TrelloLabel(id="l1", name="工作", color="blue")
        c = TrelloCard(id="c1", name="task", labels=[label])
        assert c.category == "work"

    def test_card_to_dict(self):
        c = TrelloCard(id="c1", name="test", desc="desc", id_list="list1")
        d = c.to_dict()
        assert d["name"] == "test"
        assert d["desc"] == "desc"
