"""Trello CLI 输出格式化（--pretty）。"""

from __future__ import annotations

from typing import Any, Dict, List


def format_trello_payload(command: str, payload: Dict[str, Any]) -> str:
    handlers = {
        "status": _format_status,
        "lists": _format_lists,
        "list": _format_cards_list,
        "show": _format_card_detail,
        "create": _format_card_detail,
        "update": _format_card_detail,
        "done": _format_card_detail,
        "summarize": _format_summarize,
        "prioritize": _format_prioritize,
        "search": _format_cards_list,
        "discover": _format_discover,
    }
    handler = handlers.get(command)
    return handler(payload) if handler else ""


def _format_status(payload: Dict[str, Any]) -> str:
    lines = [f"看板：{payload.get('board_name', '')}", f"链接：{payload.get('board_url', '')}",
             f"列表数：{payload.get('total_lists', 0)}", "",
             f"未完成待办总数：{payload.get('total_incomplete', 0)}"]
    by_list = payload.get("by_list", {})
    if by_list:
        lines.append("按列分布：")
        for name, count in by_list.items():
            lines.append(f"  - {name}: {count}")
    by_cat = payload.get("by_category", {})
    if by_cat:
        lines.append("按分类：")
        for name, count in by_cat.items():
            lines.append(f"  - {name}: {count}")
    lines.extend(["", f"今日待办：{payload.get('today_count', 0)}",
                  f"本周待办：{payload.get('this_week_count', 0)}",
                  f"已逾期：{payload.get('overdue_count', 0)}"])
    return "\n".join(lines)


def _format_lists(payload: Dict[str, Any]) -> str:
    lines = ["看板列表：", ""]
    for item in payload.get("lists", []):
        lines.append(f"- {item['name']} ({item['id']})")
    return "\n".join(lines)


def _format_cards_list(payload: Dict[str, Any]) -> str:
    cards = payload.get("cards", [])
    total = payload.get("total", len(cards))
    lines = [f"待办数量：{total}", ""]
    for idx, card in enumerate(cards, 1):
        labels = ", ".join(f"{lb['name']}({lb['color']})" for lb in card.get("labels", []))
        due = card.get("due", "无截止时间")
        lines.append(f"{idx}. [{card.get('list_name', '')}] {card['name']}")
        if labels:
            lines.append(f"   标签：{labels}")
        lines.append(f"   截止：{due}")
    return "\n".join(lines)


def _format_card_detail(payload: Dict[str, Any]) -> str:
    return "\n".join([f"标题：{payload.get('name', '')}", f"ID：{payload.get('id', '')}",
                      f"列表：{payload.get('list_name', '')}", f"描述：{payload.get('desc', '') or '(无)'}",
                      f"截止：{payload.get('due', '无')}", f"已完成：{payload.get('due_complete', False)}",
                      f"标签：{payload.get('labels', [])}", f"分类：{payload.get('category', '')}",
                      f"链接：{payload.get('url', '')}"])


def _format_summarize(payload: Dict[str, Any]) -> str:
    return payload.get("summary", "")


def _format_prioritize(payload: Dict[str, Any]) -> str:
    items = payload.get("items", [])
    lines = ["LLM 优先级建议：", ""]
    for item in items:
        lines.append(f"{item.get('suggested_order', '?')}. {item.get('name', '')}")
        lines.append(f"   理由：{item.get('priority_reason', '')}")
    return "\n".join(lines)


def _format_discover(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates", [])
    existing = payload.get("existing_similar", [])
    auto_created = payload.get("auto_created", [])
    lines = []
    if auto_created:
        lines.append(f"已自动创建 {len(auto_created)} 个待办：")
        for c in auto_created:
            lines.append(f"  ✅ {c.get('name', c.get('title', ''))}")
        lines.append("")
    if existing:
        lines.append(f"跳过 {len(existing)} 个重复项（已在看板中）：")
        for c in existing:
            lines.append(f"  ⏭  {c.get('name', '')}")
        lines.append("")
    if candidates:
        lines.append(f"发现 {len(candidates)} 个候选待办：")
        for idx, c in enumerate(candidates, 1):
            conf = c.get("confidence", 0)
            conf_bar = "█" * round(conf * 10)
            cat = "🔵 工作" if c.get("category") == "work" else "🟢 生活"
            due = f" 截止: {c['due']}" if c.get("due") else ""
            lines.append(f"  {idx}. [{cat}] {c.get('title', '')}  [{conf_bar} {conf:.0%}]{due}")
            if c.get("desc"):
                lines.append(f"     {c['desc']}")
            if c.get("context"):
                lines.append(f'     💬 "{c["context"]}"')
    elif not auto_created:
        lines.append("未发现待办事项。")
    return "\n".join(lines)
