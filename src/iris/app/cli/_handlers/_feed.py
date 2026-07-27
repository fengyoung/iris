"""信息汇聚 — feed 命令处理器。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from iris.feed import (
    FeedConfigManager,
    FeedPipeline,
    FeishuBridge,
    load_feed_config,
)
from iris.llm import LLMService

logger = logging.getLogger(__name__)


# ── feed-setup ────────────────────────────────────────────

def handle_feed_setup(args, bundle, _logger) -> int:
    """交互式首次配置向导。"""
    config_path = Path(bundle.root) / "config" / "feeds.json"
    bridge = FeishuBridge()

    print("\n📋 信息汇聚 — 首次配置向导")
    print("=" * 40)

    # Step 1: 发现可关注的群聊
    print("\nStep 1/3：发现可关注的群聊")
    print("正在拉取你可用的群聊列表...\n")
    try:
        chats = bridge.list_user_chats(page_size=30)
    except Exception as e:
        print(f"❌ 获取群聊列表失败: {e}")
        return 1

    if not chats:
        print("未找到可用群聊。")
        return 1

    for i, c in enumerate(chats, 1):
        name = FeishuBridge.get_display_name(c)
        print(f"  [{i}] {name}")

    print("\n选择要关注的群聊（可多选，逗号分隔，输入 skip 跳过）:")
    choice = input("> ").strip()
    if choice.lower() == "skip":
        print("跳过群聊配置。")
        return _save_and_finish([], config_path)

    selected = []
    for part in choice.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(chats):
                selected.append(chats[idx])

    if not selected:
        print("未选择任何群聊。")
        return _save_and_finish([], config_path)

    # Step 2: 配置导入模式
    print("\nStep 2/3：配置每个群聊的导入模式")
    print("  auto_import = 自动入库，confirm = 需要确认\n")
    for c in selected:
        name = FeishuBridge.get_display_name(c)
        mode = input(f"  {name} → 模式 (auto/confirm) [auto]: ").strip()
        if not mode:
            mode = "auto"
        c["_feed_mode"] = mode if mode == "confirm" else "auto_import"

    # Step 3: 关联 OKR 标签
    print("\nStep 3/3：关联 OKR 标签（可选，逗号分隔，留空跳过）\n")
    for c in selected:
        name = FeishuBridge.get_display_name(c)
        tags = input(f"  {name} → OKR 标签: ").strip()
        if tags:
            c["_feed_tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            c["_feed_tags"] = []

    wc_list = []
    for c in selected:
        wc_list.append({
            "id": c.get("chat_id", ""),
            "name": FeishuBridge.get_display_name(c),
            "type": "group",
            "mode": c.get("_feed_mode", "auto_import"),
            "okr_tags": c.get("_feed_tags", []),
        })

    return _save_and_finish(wc_list, config_path)


def _save_and_finish(wc_list: list, config_path: Path) -> int:
    """保存配置并结束。"""
    import json
    config = {
        "version": 1,
        "watch_chats": wc_list,
        "topic_config": {
            "default_range_days": 3,
            "min_msg_length": 10,
            "topic_min_messages": 2,
            "max_topics_per_run": 30,
            "time_window_minutes": 30,
        },
        "okr_mapping": {"enabled": True, "strict_match": False},
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 配置已保存到 {config_path}")
    print(f"   已关注 {len(wc_list)} 个会话")
    return 0


# ── feed-list ─────────────────────────────────────────────

def handle_feed_list(args, bundle, _logger) -> int:
    """列出当前关注的会话。"""
    config_path = Path(bundle.root) / "config" / "feeds.json"
    config = load_feed_config(config_path)
    chats = config.watch_chats
    if not chats:
        print("（未关注任何会话，请先运行 iris feed-setup）")
        return 0

    # 尝试加载 OKR 文档，解析标签语义
    from iris.feed._okr_loader import OKRLoader
    source_dir = None
    if hasattr(bundle, 'default_source_path'):
        try:
            source_dir = bundle.default_source_path
        except Exception:
            pass
    if not source_dir:
        import os
        source_dir = os.environ.get("IRIS_WORK_DOCS_DIR", "")
    okr_loader = OKRLoader(source_root=source_dir) if source_dir else None

    print(f"已关注 {len(chats)} 个会话:\n")
    for c in chats:
        tags_str = ", ".join(c.okr_tags) if c.okr_tags else "—"
        if c.okr_tags and okr_loader:
            resolved = okr_loader.resolve_tags(c.okr_tags)
            descs = [f"{tag}: {desc[:40]}…" for tag, desc in resolved.items()]
            tags_str = "\n" + "\n".join(f"                    ↳ {d}" for d in descs)
        print(f"  {c.name:20s} [{c.type:6s}]  {c.mode:12s}  → {tags_str}")
    return 0


# ── feed-add ──────────────────────────────────────────────

def handle_feed_add(args, bundle, _logger) -> int:
    """添加关注会话。"""
    chat_name = getattr(args, "chat", "")
    if not chat_name:
        print("用法: iris feed-add --chat <群聊名> [--chat-type group|single] [--import-mode auto_import|confirm] [--tags OKR标签]")
        return 1

    config_path = Path(bundle.root) / "config" / "feeds.json"
    mgr = FeedConfigManager(config_path)
    bridge = FeishuBridge()

    # 搜索群聊 ID
    found = bridge.search_chat_by_name(chat_name)
    if not found:
        print(f"未找到群聊: {chat_name}")
        return 1

    # 如果有多个匹配，选第一个
    target = found[0]
    chat_id = target.get("chat_id", "")
    name = FeishuBridge.get_display_name(target)

    chat_type = getattr(args, "chat_type", "group") or "group"
    import_mode = getattr(args, "import_mode", "auto_import") or "auto_import"
    tags_str = getattr(args, "tags", "") or ""
    okr_tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else None

    mgr.add_chat(chat_id=chat_id, name=name, chat_type=chat_type, mode=import_mode, okr_tags=okr_tags)
    print(f"✅ 已添加: {name} ({chat_type}, {import_mode})")
    return 0


# ── feed-remove ───────────────────────────────────────────

def handle_feed_remove(args, bundle, _logger) -> int:
    """移除关注会话。"""
    chat_name = getattr(args, "chat", "")
    if not chat_name:
        print("用法: iris feed-remove --chat <群聊名或ID>")
        return 1

    config_path = Path(bundle.root) / "config" / "feeds.json"
    mgr = FeedConfigManager(config_path)
    if mgr.remove_chat(chat_name):
        print(f"✅ 已移除: {chat_name}")
    else:
        print(f"未找到: {chat_name}")
    return 0


# ── feed-config ───────────────────────────────────────────

def handle_feed_config(args, bundle, _logger) -> int:
    """查看或修改配置。"""
    config_path = Path(bundle.root) / "config" / "feeds.json"
    mgr = FeedConfigManager(config_path)

    show = getattr(args, "show", False)
    if show:
        import json
        config = load_feed_config(config_path)
        print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))
        return 0

    chat = getattr(args, "chat", "")
    if not chat:
        print("用法: iris feed-config --chat <群聊名> [--import-mode auto_import|confirm] [--tags OKR标签]\n"
              "      iris feed-config --show  # 查看完整配置")
        return 1

    bridge = FeishuBridge()
    found = bridge.search_chat_by_name(chat)
    if not found:
        print(f"未找到: {chat}")
        return 1

    chat_id = found[0].get("chat_id", "")
    updates = {}
    import_mode = getattr(args, "import_mode", "")
    if import_mode:
        updates["mode"] = import_mode
    tags = getattr(args, "tags", "")
    if tags:
        updates["okr_tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    if updates:
        updated = mgr.update_chat(chat_id, **updates)
        if updated:
            print(f"✅ 已更新: {updated.name}")
        else:
            print(f"更新失败: {chat}")
    else:
        # 查看单个
        for c in mgr.list_chats():
            if c.id == chat_id:
                tags = ", ".join(c.okr_tags) if c.okr_tags else "—"
                print(f"  {c.name} | {c.type} | {c.mode} | OKR: {tags}")
                return 0
    return 0


# ── feed-collect ──────────────────────────────────────────

def handle_feed_collect(args, bundle, _logger) -> int:
    """执行信息汇聚。"""
    llm_service = LLMService(bundle)
    # 解析 SOURCE 路径
    source_dir = None
    if hasattr(bundle, 'default_source_path'):
        try:
            source_dir = bundle.default_source_path
        except Exception:
            pass
    pipeline = FeedPipeline(bundle, llm_service, source_dir=source_dir)

    # 解析时间范围
    since_str = getattr(args, "since", "")
    since = None
    if since_str:
        try:
            since = datetime.strptime(since_str, "%Y-%m-%d")
        except ValueError:
            print(f"日期格式错误: {since_str}，期望 YYYY-MM-DD")
            return 1

    chat_filter_str = getattr(args, "chat", "")
    chat_filter = [c.strip() for c in chat_filter_str.split(",") if c.strip()] if chat_filter_str else None

    dry_run = getattr(args, "dry_run", False)
    import_mode_raw = getattr(args, "import_mode", "") or ""

    # 解析导入模式：'auto_import' / 'confirm' 覆盖所有会话；其他值（如 '' 或 'all'）使用各会话配置
    import_mode_override = None
    if import_mode_raw in ("auto_import", "confirm"):
        import_mode_override = import_mode_raw
    send_notifications = (import_mode_raw == "confirm")

    result = pipeline.run(
        since=since,
        chat_filter=chat_filter,
        dry_run=dry_run,
        send_notifications=send_notifications,
        import_mode=import_mode_override,
    )

    if result.empty_reason:
        print(f"ℹ️  {result.empty_reason}")
        return 0

    print(f"\n📊 信息汇聚完成")
    print(f"  获取消息: {result.fetched_count} 条")
    print(f"  过滤后:   {result.filtered_count} 条")
    print(f"  检测话题: {len(result.topics)} 个")
    print(f"  生成简报: {len(result.brief_files)} 份")

    if result.auto_imported:
        print(f"  ✅ 自动入库: {len(result.auto_imported)} 个话题")
    if result.pending:
        print(f"  👁️  待确认: {len(result.pending)} 个话题")
        print(f"     执行 iris feed-pending 查看详情")

    if dry_run:
        print("\n📋 预览话题:")
        for t in result.topics:
            tags = ",".join(t.okr_tags) if t.okr_tags else "—"
            print(f"  [{tags}] {t.title}")
            print(f"    {t.summary[:100]}...")
        print("\n（--dry-run 模式，未实际写入）")

    return 0


# ── feed-pending ──────────────────────────────────────────

def handle_feed_pending(args, bundle, _logger) -> int:
    """查看待确认话题。"""
    config_path = Path(bundle.root) / "config" / "feeds.json"
    data_dir = Path(bundle.root) / "data"
    mgr = FeedConfigManager(config_path)
    pending = mgr.load_pending(data_dir)

    if not pending:
        print("（无待确认话题）")
        return 0

    print(f"待确认话题: {len(pending)} 个\n")
    for i, item in enumerate(pending):
        print(f"  [{i + 1}] {item['title']}")
        print(f"      ID: {item['topic_id']}")
        print(f"      来源: {', '.join(item.get('sources', []))}")
        print(f"      摘要: {item.get('summary', '')[:80]}...")
        print(f"      创建: {item.get('created', '')[:19]}")
        print(f"     确认: iris feed-confirm {item['topic_id']}")
        print(f"     忽略: iris feed-ignore {item['topic_id']}")
        print()
    return 0


# ── feed-confirm ──────────────────────────────────────────

def handle_feed_confirm(args, bundle, _logger) -> int:
    """确认话题入库。"""
    topic_id = getattr(args, "topic_id", "")
    confirm_all = getattr(args, "all_", False)

    config_path = Path(bundle.root) / "config" / "feeds.json"
    data_dir = Path(bundle.root) / "data"
    mgr = FeedConfigManager(config_path)
    pending = mgr.load_pending(data_dir)

    if confirm_all:
        confirmed = len(pending)
        mgr.save_pending(data_dir, [])
        print(f"✅ 已确认全部 {confirmed} 个话题")
        return 0

    if topic_id:
        new_pending = [p for p in pending if p["topic_id"] != topic_id]
        if len(new_pending) < len(pending):
            mgr.save_pending(data_dir, new_pending)
            print(f"✅ 已确认: {topic_id}")
        else:
            print(f"未找到: {topic_id}")
        return 0

    print("用法: iris feed-confirm <topic_id>  或  iris feed-confirm --all")
    return 1


# ── feed-ignore ────────────────────────────────────────────

def handle_feed_ignore(args, bundle, _logger) -> int:
    """忽略话题。"""
    topic_id = getattr(args, "topic_id", "")

    config_path = Path(bundle.root) / "config" / "feeds.json"
    data_dir = Path(bundle.root) / "data"
    mgr = FeedConfigManager(config_path)
    pending = mgr.load_pending(data_dir)

    if topic_id:
        new_pending = [p for p in pending if p["topic_id"] != topic_id]
        if len(new_pending) < len(pending):
            mgr.save_pending(data_dir, new_pending)
            print(f"🚫 已忽略: {topic_id}")
        else:
            print(f"未找到: {topic_id}")
        return 0

    print("用法: iris feed-ignore <topic_id>")
    return 1


# ── 命令字典 ───────────────────────────────────────────────

FEED_HANDLERS = {
    "feed-setup": handle_feed_setup,
    "feed-list": handle_feed_list,
    "feed-add": handle_feed_add,
    "feed-remove": handle_feed_remove,
    "feed-config": handle_feed_config,
    "feed-collect": handle_feed_collect,
    "feed-pending": handle_feed_pending,
    "feed-confirm": handle_feed_confirm,
    "feed-ignore": handle_feed_ignore,
}
