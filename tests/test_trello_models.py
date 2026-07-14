"""trello/models.py 纯 dataclass 测试。"""

from __future__ import annotations

from iris.trello.models import TrelloCard, TrelloLabel, TrelloOverview


def _card(**overrides) -> TrelloCard:
    base = dict(id="c1", name="待办A")
    base.update(overrides)
    return TrelloCard(**base)


class TestTrelloCardIsCompleted:
    def test_closed_is_completed(self):
        assert _card(closed=True).is_completed is True

    def test_due_complete_is_completed(self):
        assert _card(due_complete=True).is_completed is True

    def test_open_card_not_completed(self):
        assert _card().is_completed is False


class TestTrelloCardCategory:
    def test_blue_label_is_work(self):
        card = _card(labels=[TrelloLabel(id="l1", name="", color="blue")])
        assert card.category == "work"

    def test_green_label_is_life(self):
        card = _card(labels=[TrelloLabel(id="l2", name="", color="green")])
        assert card.category == "life"

    def test_name_keyword_work(self):
        card = _card(labels=[TrelloLabel(id="l3", name="工作项", color="purple")])
        assert card.category == "work"

    def test_name_keyword_life(self):
        card = _card(labels=[TrelloLabel(id="l4", name="生活琐事", color="orange")])
        assert card.category == "life"

    def test_no_label_returns_none(self):
        assert _card().category is None


class TestTrelloCardToDict:
    def test_serializes_labels_and_category(self):
        card = _card(
            desc="描述",
            due="2026-07-20T10:00:00.000Z",
            list_name="TODO",
            labels=[TrelloLabel(id="l1", name="工作", color="blue")],
            url="http://x",
        )
        d = card.to_dict()
        assert d["id"] == "c1"
        assert d["category"] == "work"
        assert d["labels"] == [{"name": "工作", "color": "blue"}]
        assert d["list_name"] == "TODO"
        assert d["url"] == "http://x"


class TestTrelloOverviewToDict:
    def test_counts_and_nested_cards(self):
        c = _card()
        ov = TrelloOverview(
            board_name="我的看板",
            total_incomplete=3,
            by_list={"TODO": 2, "DOING": 1},
            by_category={"work": 3},
            today=[c],
            this_week=[c, c],
            overdue=[],
        )
        d = ov.to_dict()
        assert d["board_name"] == "我的看板"
        assert d["total_incomplete"] == 3
        assert d["today_count"] == 1
        assert d["this_week_count"] == 2
        assert d["overdue_count"] == 0
        assert len(d["today"]) == 1
        assert d["today"][0]["id"] == "c1"
