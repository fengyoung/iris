"""Trello 业务逻辑：初始化、CRUD、汇总。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from iris.trello.client import TrelloClient, TrelloClientError
from iris.trello.models import TrelloBoard, TrelloCard, TrelloLabel, TrelloList, TrelloOverview

_TRELLO_DATETIME_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


class TrelloService:
    def __init__(self, config: Dict[str, Any]):
        self._config = config
        self._client = TrelloClient(api_key=config["api_key"], token=config["token"])
        self._board: Optional[Dict[str, Any]] = None
        self._labels: Dict[str, TrelloLabel] = {}

    def ensure_board(self) -> TrelloBoard:
        org = self._client.find_organization_by_name(self._config["workspace_name"])
        if org is None:
            raise TrelloClientError(f"未找到工作区: {self._config['workspace_name']}")

        board_name = self._config["board_name"]
        board_data = self._client.find_board_by_name(org["id"], board_name)
        if board_data is None:
            board_data = self._client.create_board(board_name, org["id"])

        self._board = board_data
        self._ensure_default_lists()
        self._ensure_labels()

        lists = self._load_lists()
        labels = self._load_labels()
        return TrelloBoard(
            id=board_data["id"],
            name=board_data["name"],
            url=board_data.get("url", ""),
            lists=lists,
            labels=labels,
        )

    def _ensure_default_lists(self) -> None:
        board_id = self._board["id"]
        for list_name in self._config["default_lists"]:
            existing = self._client.find_list_by_name(board_id, list_name)
            if existing is None:
                self._client.create_list(board_id, list_name)

    def _ensure_labels(self) -> None:
        board_id = self._board["id"]
        label_configs: Dict[str, Dict[str, str]] = self._config["labels"]
        for key, cfg in label_configs.items():
            existing = self._client.find_label_by_color(board_id, cfg["color"])
            if existing is None:
                label = self._client.create_label(board_id, cfg["name"], cfg["color"])
                self._labels[key] = TrelloLabel(id=label["id"], name=cfg["name"], color=cfg["color"])
            else:
                self._labels[key] = TrelloLabel(id=existing["id"], name=cfg["name"], color=cfg["color"])

    def _load_lists(self) -> List[TrelloList]:
        raw = self._client.list_lists(self._board["id"])
        return [TrelloList(id=item["id"], name=item["name"], id_board=item["idBoard"]) for item in raw]

    def _load_labels(self) -> List[TrelloLabel]:
        raw = self._client.list_labels(self._board["id"])
        return [TrelloLabel(id=item["id"], name=item.get("name", ""), color=item.get("color")) for item in raw]

    def get_lists(self) -> List[TrelloList]:
        if self._board is None:
            self.ensure_board()
        return self._load_lists()

    def _find_list(self, name: str) -> TrelloList:
        lists = self._load_lists()
        for lst in lists:
            if lst.name == name:
                return lst
        raise TrelloClientError(f"未找到列表: {name}")

    def _find_or_create_list(self, name: str) -> TrelloList:
        try:
            return self._find_list(name)
        except TrelloClientError:
            raw = self._client.create_list(self._board["id"], name)
            return TrelloList(id=raw["id"], name=raw["name"], id_board=raw["idBoard"])

    def _get_label_id(self, category: str) -> str:
        if not self._labels:
            self._ensure_labels()
        label = self._labels.get(category)
        if label is None:
            raise TrelloClientError(f"未知分类: {category}，可选 work/life")
        return label.id

    def create_card(self, title: str, desc: str = "", due: Optional[str] = None,
                    category: str = "work", list_name: str = "TODO") -> TrelloCard:
        if self._board is None:
            self.ensure_board()
        target_list = self._find_list(list_name)
        label_id = self._get_label_id(category)
        if due is None:
            due_dt = datetime.now(tz=timezone.utc) + timedelta(hours=self._config["default_due_hours"])
            due = due_dt.strftime(_TRELLO_DATETIME_FMT)
        raw = self._client.create_card(list_id=target_list.id, name=title, desc=desc, due=due, id_labels=[label_id])
        return self._parse_card(raw)

    def get_card(self, card_id: str) -> TrelloCard:
        raw = self._client.get_card(card_id)
        return self._parse_card(raw)

    def list_cards(self, list_name: Optional[str] = None, category: Optional[str] = None,
                   include_completed: bool = False) -> List[TrelloCard]:
        if self._board is None:
            self.ensure_board()
        if list_name:
            target_list = self._find_list(list_name)
            lists = [target_list]
        else:
            lists = self._load_lists()
        all_cards: List[TrelloCard] = []
        for lst in lists:
            raw_cards = self._client.list_cards(lst.id)
            for raw in raw_cards:
                card = self._parse_card(raw, list_name=lst.name)
                if not include_completed and card.is_completed:
                    continue
                if category and card.category != category:
                    continue
                all_cards.append(card)
        all_cards.sort(key=lambda c: c.due or "9999")
        return all_cards

    def update_card(self, card_id: str, title: Optional[str] = None, desc: Optional[str] = None,
                    due: Optional[str] = None, category: Optional[str] = None) -> TrelloCard:
        params: Dict[str, Any] = {}
        if title is not None:
            params["name"] = title
        if desc is not None:
            params["desc"] = desc
        if due is not None:
            params["due"] = due
        if category is not None:
            label_id = self._get_label_id(category)
            params["idLabels"] = label_id
        if not params:
            raise TrelloClientError("至少需要提供一项更新字段")
        self._client.update_card(card_id, **params)
        return self.get_card(card_id)

    def complete_card(self, card_id: str) -> TrelloCard:
        if self._board is None:
            self.ensure_board()
        self.get_card(card_id)
        self._client.set_due_complete(card_id)
        now = datetime.now(timezone.utc)
        pattern = self._config.get("done_list_pattern", "DONE-{year}{month:02d}")
        done_list_name = pattern.replace("{year}", str(now.year)).replace("{month:02d}", f"{now.month:02d}").replace("{month}", str(now.month))
        done_list = self._find_or_create_list(done_list_name)
        self._client.move_card(card_id, done_list.id)
        return self.get_card(card_id)

    def search_cards(self, query: str) -> List[TrelloCard]:
        if self._board is None:
            self.ensure_board()
        raw = self._client.search(query, board_id=self._board["id"])
        return [self._parse_card(item) for item in raw]

    def overview(self) -> TrelloOverview:
        if self._board is None:
            self.ensure_board()
        all_cards = self.list_cards(include_completed=False)
        now = datetime.now(timezone.utc)
        today_end = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=timezone.utc)
        week_end = today_end + timedelta(days=(7 - now.weekday()))
        today: List[TrelloCard] = []
        this_week: List[TrelloCard] = []
        overdue: List[TrelloCard] = []
        by_list: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        for card in all_cards:
            list_name = card.list_name or "未知"
            by_list[list_name] = by_list.get(list_name, 0) + 1
            cat = card.category or "未分类"
            by_category[cat] = by_category.get(cat, 0) + 1
            if card.due:
                try:
                    due_dt = datetime.strptime(card.due, _TRELLO_DATETIME_FMT).replace(tzinfo=timezone.utc)
                except ValueError:
                    due_dt = None
                if due_dt:
                    if due_dt < now:
                        overdue.append(card)
                    if due_dt <= today_end:
                        today.append(card)
                    if due_dt <= week_end:
                        this_week.append(card)
        return TrelloOverview(board_name=self._board["name"], total_incomplete=len(all_cards),
                              by_list=by_list, by_category=by_category, today=today, this_week=this_week, overdue=overdue)

    def today_cards(self) -> List[TrelloCard]:
        return self.overview().today

    def weekly_cards(self) -> List[TrelloCard]:
        return self.overview().this_week

    def status(self) -> Dict[str, Any]:
        if self._board is None:
            self.ensure_board()
        overview = self.overview()
        lists = self._load_lists()
        return {"board_name": overview.board_name, "board_id": self._board["id"], "board_url": self._board.get("url", ""),
                "total_lists": len(lists), "list_names": [lst.name for lst in lists],
                "total_incomplete": overview.total_incomplete, "by_list": overview.by_list,
                "by_category": overview.by_category, "today_count": len(overview.today),
                "this_week_count": len(overview.this_week), "overdue_count": len(overview.overdue)}

    def _parse_card(self, raw: Dict[str, Any], list_name: str = "") -> TrelloCard:
        labels = [TrelloLabel(id=lb.get("id", ""), name=lb.get("name", ""), color=lb.get("color"))
                  for lb in raw.get("labels", []) or []]
        return TrelloCard(id=raw["id"], name=raw.get("name", ""), desc=raw.get("desc", ""),
                          due=raw.get("due"), due_complete=bool(raw.get("dueComplete")),
                          closed=bool(raw.get("closed")), id_list=raw.get("idList", ""),
                          id_board=raw.get("idBoard", ""), list_name=list_name, labels=labels, url=raw.get("url", ""))
