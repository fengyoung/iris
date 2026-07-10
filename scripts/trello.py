"""Trello Explorer CLI 入口脚本。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from iris.config.loader import load_config_bundle, resolve_env_vars, load_env_file
from iris.trello.client import TrelloClientError
from iris.trello.service import TrelloService
from iris.trello.llm import TrelloLLM
from iris.trello.formatter import format_trello_payload


def _load_trello_config(project_root: str) -> dict:
    config_path = Path(project_root) / "config" / "trello.json"
    if not config_path.exists():
        raise SystemExit(f"缺少 Trello 配置文件: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        raw_config = json.load(f)
    # 加载 .env 并解析 ${VAR} 占位符（TRELLO_API_KEY / TRELLO_TOKEN 等）
    root_path = Path(project_root).resolve()
    env = load_env_file(root_path / ".env")
    return resolve_env_vars(raw_config, env)


def _emit(command: str, payload: dict, *, pretty: bool) -> None:
    if pretty:
        rendered = format_trello_payload(command, payload)
        if rendered:
            print(rendered)
            return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Trello Explorer - Iris 任务看板管理")
    parser.add_argument("--project-root", default=str(_PROJECT_ROOT), help="Iris 项目根目录")
    parser.add_argument("--pretty", action="store_true", help="人类可读输出")
    parser.add_argument("--model", choices=["base", "adv"], default=None, help="显式指定模型")
    sub = parser.add_subparsers(dest="command", required=True)

    _common = argparse.ArgumentParser(add_help=False)
    _common.add_argument("--pretty", action="store_true", help="人类可读输出")

    sub.add_parser("status", parents=[_common], help="看板健康状态")
    sub.add_parser("lists", parents=[_common], help="列出看板所有列表")

    list_parser = sub.add_parser("list", parents=[_common], help="列出未完成待办")
    list_parser.add_argument("--category", choices=["work", "life"], help="按分类筛选")
    list_parser.add_argument("--today", action="store_true", help="仅今日待办")
    list_parser.add_argument("--weekly", action="store_true", help="仅本周待办")
    list_parser.add_argument("--list", dest="list_name", default=None, help="指定列表名")

    show_parser = sub.add_parser("show", parents=[_common], help="查看卡片详情")
    show_parser.add_argument("card_id", help="卡片 ID 或 URL")

    create_parser = sub.add_parser("create", parents=[_common], help="创建待办卡片")
    create_parser.add_argument("--title", required=True, help="待办标题")
    create_parser.add_argument("--desc", default="", help="描述")
    create_parser.add_argument("--due", default=None, help="截止时间")
    create_parser.add_argument("--category", choices=["work", "life"], default="work", help="分类")
    create_parser.add_argument("--list", dest="list_name", default="TODO", help="目标列表")

    update_parser = sub.add_parser("update", parents=[_common], help="修改待办")
    update_parser.add_argument("card_id", help="卡片 ID")
    update_parser.add_argument("--title", default=None, help="新标题")
    update_parser.add_argument("--desc", default=None, help="新描述")
    update_parser.add_argument("--due", default=None, help="新截止时间")
    update_parser.add_argument("--category", choices=["work", "life"], default=None, help="新分类")

    done_parser = sub.add_parser("done", parents=[_common], help="完成待办")
    done_parser.add_argument("card_id", help="卡片 ID")

    sub.add_parser("summarize", parents=[_common], help="LLM 汇总当前待办")
    sub.add_parser("prioritize", parents=[_common], help="LLM 优先级排序建议")

    search_parser = sub.add_parser("search", parents=[_common], help="搜索卡片")
    search_parser.add_argument("--query", required=True, help="搜索关键词")

    discover_parser = sub.add_parser("discover", parents=[_common], help="从对话文本检测待办")
    discover_parser.add_argument("--text", required=True, help="对话文本（或 - 从 stdin 读取）")
    discover_parser.add_argument("--auto", action="store_true", help="自动创建高置信度候选")
    discover_parser.add_argument("--min-confidence", type=float, default=0.7, help="自动创建的最低置信度")

    args = parser.parse_args()
    pretty: bool = getattr(args, "pretty", False)

    try:
        trello_config = _load_trello_config(args.project_root)
        service = TrelloService(trello_config)

        if args.command == "status":
            _emit("status", service.status(), pretty=pretty)
            return 0
        if args.command == "lists":
            lists = service.get_lists()
            _emit("lists", {"lists": [{"id": l.id, "name": l.name} for l in lists]}, pretty=pretty)
            return 0
        if args.command == "list":
            cards = service.today_cards() if args.today else (service.weekly_cards() if args.weekly else service.list_cards(list_name=args.list_name, category=args.category))
            _emit("list", {"cards": [c.to_dict() for c in cards], "total": len(cards)}, pretty=pretty)
            return 0
        if args.command == "show":
            card = service.get_card(_extract_card_id(args.card_id))
            _emit("show", card.to_dict(), pretty=pretty)
            return 0
        if args.command == "create":
            due = args.due
            if due and len(due) == 10:
                due = f"{due}T23:59:59.000Z"
            card = service.create_card(title=args.title, desc=args.desc, due=due, category=args.category, list_name=args.list_name)
            _emit("create", card.to_dict(), pretty=pretty)
            return 0
        if args.command == "update":
            card = service.update_card(args.card_id, title=args.title, desc=args.desc, due=args.due, category=args.category)
            _emit("update", card.to_dict(), pretty=pretty)
            return 0
        if args.command == "done":
            card = service.complete_card(args.card_id)
            _emit("done", card.to_dict(), pretty=pretty)
            return 0
        if args.command in ("summarize", "prioritize"):
            bundle = load_config_bundle(Path(args.project_root))
            llm = TrelloLLM(bundle, model=getattr(args, "model", None))
            cards = service.list_cards()
            if args.command == "summarize":
                _emit("summarize", {"summary": llm.summarize(cards), "card_count": len(cards)}, pretty=pretty)
            else:
                _emit("prioritize", {"items": llm.prioritize(cards), "card_count": len(cards)}, pretty=pretty)
            return 0
        if args.command == "search":
            cards = service.search_cards(args.query)
            _emit("search", {"cards": [c.to_dict() for c in cards], "total": len(cards)}, pretty=pretty)
            return 0
        if args.command == "discover":
            return _handle_discover(args, service, pretty)

    except TrelloClientError as exc:
        print(f"Trello 错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


def _handle_discover(args, service, pretty) -> int:
    text = args.text
    if text == "-":
        text = sys.stdin.read()
    if not text.strip():
        _emit("discover", {"candidates": [], "existing_similar": [], "auto_created": []}, pretty=pretty)
        return 0

    bundle = load_config_bundle(Path(args.project_root))
    llm = TrelloLLM(bundle, model=getattr(args, "model", None))
    existing_cards = service.list_cards()
    existing_titles = [c.name for c in existing_cards]
    candidates = llm.discover_todos(text, existing_titles=existing_titles)

    if not candidates:
        _emit("discover", {"candidates": [], "existing_similar": [], "auto_created": []}, pretty=pretty)
        return 0

    new_candidates = []
    existing_similar = []
    for cand in candidates:
        title = cand.get("title", "")
        if not title:
            continue
        if _is_duplicate(title, existing_titles):
            existing_similar.append(cand)
        else:
            new_candidates.append(cand)

    auto_created = []
    if args.auto and new_candidates:
        for cand in new_candidates:
            if cand.get("confidence", 0) >= args.min_confidence:
                due = cand.get("due")
                if due and len(str(due)) == 10:
                    due = f"{due}T23:59:59.000Z"
                else:
                    due = None
                try:
                    card = service.create_card(title=cand.get("title", ""), desc=cand.get("desc", ""), due=due, category=cand.get("category", "work"))
                    auto_created.append(card.to_dict())
                except Exception as exc:
                    print(f"创建失败 [{cand.get('title', '')}]: {exc}", file=sys.stderr)
        auto_titles = {c.get("name", "") for c in auto_created}
        new_candidates = [c for c in new_candidates if c.get("title", "") not in auto_titles]

    _emit("discover", {"candidates": new_candidates, "existing_similar": [{"name": c.get("title", ""), "reason": "已在 Trello 看板中"} for c in existing_similar], "auto_created": auto_created}, pretty=pretty)
    return 0


def _normalize_title(title: str) -> str:
    import re
    return re.sub(r"[^\w一-鿿]", "", title.lower().strip())


def _is_duplicate(candidate_title: str, existing_titles: list[str]) -> bool:
    norm_cand = _normalize_title(candidate_title)
    if not norm_cand:
        return False
    for ext_title in existing_titles:
        norm_ext = _normalize_title(ext_title)
        if not norm_ext:
            continue
        if norm_cand == norm_ext or norm_cand in norm_ext or norm_ext in norm_cand:
            return True
    return False


def _extract_card_id(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http"):
        parts = raw.split("/")
        for i, part in enumerate(parts):
            if part == "c" and i + 1 < len(parts):
                return parts[i + 1]
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
