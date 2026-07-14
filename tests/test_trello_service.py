"""trello/service.py 业务逻辑测试（mock TrelloClient，无网络）。

覆盖：_parse_card 解析、list_cards 过滤（完成/分类）、overview 聚合。
"""

from __future__ import annotations

from iris.trello.service import TrelloService


def _make_service() -> TrelloService:
    config = {
        "api_key": "k",
        "token": "t",
        "workspace_name": "ws",
        "board_name": "bd",
        "default_lists": ["TODO", "DOING"],
        "labels": {"work": {"name": "工作", "color": "blue"}, "life": {"name": "生活", "color": "green"}},
        "default_due_hours": 24,
    }
    svc = TrelloService(config)
    # 预置 board，跳过 ensure_board 的网络调用
    svc._board = {"id": "board1", "name": "我的看板", "url": "http://board"}
    return svc


class FakeClient:
    """TrelloClient 的最小替身。"""

    def __init__(self, lists, cards_by_list):
        self._lists = lists
        self._cards_by_list = cards_by_list

    def list_lists(self, board_id):
        return self._lists

    def list_cards(self, list_id):
        return self._cards_by_list.get(list_id, [])


class TestParseCard:
    def test_parses_raw_card(self):
        svc = _make_service()
        raw = {
            "id": "c1", "name": "任务A", "desc": "描述",
            "due": "2026-07-20T10:00:00.000Z", "dueComplete": True, "closed": False,
            "idList": "l1", "idBoard": "board1", "url": "http://c",
            "labels": [{"id": "lb1", "name": "工作", "color": "blue"}],
        }
        card = svc._parse_card(raw, list_name="TODO")
        assert card.id == "c1"
        assert card.name == "任务A"
        assert card.due_complete is True
        assert card.list_name == "TODO"
        assert card.category == "work"

    def test_handles_missing_optional_fields(self):
        svc = _make_service()
        card = svc._parse_card({"id": "c2"})
        assert card.name == ""
        assert card.labels == []
        assert card.due is None


class TestListCards:
    def test_filters_completed_by_default(self):
        svc = _make_service()
        svc._client = FakeClient(
            lists=[{"id": "l1", "name": "TODO", "idBoard": "board1"}],
            cards_by_list={
                "l1": [
                    {"id": "c1", "name": "未完成", "closed": False, "dueComplete": False},
                    {"id": "c2", "name": "已完成", "closed": True},
                ]
            },
        )
        cards = svc.list_cards()
        names = [c.name for c in cards]
        assert names == ["未完成"]

    def test_include_completed(self):
        svc = _make_service()
        svc._client = FakeClient(
            lists=[{"id": "l1", "name": "TODO", "idBoard": "board1"}],
            cards_by_list={
                "l1": [
                    {"id": "c1", "name": "未完成", "closed": False},
                    {"id": "c2", "name": "已完成", "closed": True},
                ]
            },
        )
        cards = svc.list_cards(include_completed=True)
        assert len(cards) == 2

    def test_filters_by_category(self):
        svc = _make_service()
        svc._client = FakeClient(
            lists=[{"id": "l1", "name": "TODO", "idBoard": "board1"}],
            cards_by_list={
                "l1": [
                    {"id": "c1", "name": "工作卡", "labels": [{"id": "b", "name": "", "color": "blue"}]},
                    {"id": "c2", "name": "生活卡", "labels": [{"id": "g", "name": "", "color": "green"}]},
                ]
            },
        )
        cards = svc.list_cards(category="life")
        assert [c.name for c in cards] == ["生活卡"]

    def test_sorted_by_due(self):
        svc = _make_service()
        svc._client = FakeClient(
            lists=[{"id": "l1", "name": "TODO", "idBoard": "board1"}],
            cards_by_list={
                "l1": [
                    {"id": "c1", "name": "晚", "due": "2026-08-01"},
                    {"id": "c2", "name": "早", "due": "2026-07-01"},
                ]
            },
        )
        cards = svc.list_cards()
        assert [c.name for c in cards] == ["早", "晚"]


class TestOverview:
    def test_aggregates_by_list_and_category(self):
        svc = _make_service()
        svc._client = FakeClient(
            lists=[{"id": "l1", "name": "TODO", "idBoard": "board1"}],
            cards_by_list={
                "l1": [
                    {"id": "c1", "name": "工作卡", "labels": [{"id": "b", "name": "", "color": "blue"}]},
                    {"id": "c2", "name": "无分类卡"},
                ]
            },
        )
        ov = svc.overview()
        assert ov.board_name == "我的看板"
        assert ov.total_incomplete == 2
        assert ov.by_list["TODO"] == 2
        assert ov.by_category["work"] == 1
        assert ov.by_category["未分类"] == 1

    def test_overdue_detection(self):
        svc = _make_service()
        svc._client = FakeClient(
            lists=[{"id": "l1", "name": "TODO", "idBoard": "board1"}],
            cards_by_list={
                "l1": [
                    {"id": "c1", "name": "过期卡", "due": "2000-01-01T00:00:00.000Z"},
                ]
            },
        )
        ov = svc.overview()
        assert len(ov.overdue) == 1
        assert ov.overdue[0].name == "过期卡"


class TestStatus:
    def test_status_shape(self):
        svc = _make_service()
        svc._client = FakeClient(
            lists=[{"id": "l1", "name": "TODO", "idBoard": "board1"}],
            cards_by_list={"l1": [{"id": "c1", "name": "卡A"}]},
        )
        status = svc.status()
        assert status["board_name"] == "我的看板"
        assert status["board_id"] == "board1"
        assert status["total_lists"] == 1
        assert status["list_names"] == ["TODO"]
        assert status["total_incomplete"] == 1
