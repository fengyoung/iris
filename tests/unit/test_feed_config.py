"""feed 包单元测试 — 配置加载 / WatchChat / FeedConfig / FeedConfigManager。"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

from iris.feed.feed_config import (
    WatchChat,
    FeedConfig,
    FeedConfigManager,
    load_feed_config,
    save_feed_config,
    write_example_config,
)


# ═══════════════════════════════════════════════════════════════
# WatchChat 测试
# ═══════════════════════════════════════════════════════════════

class TestWatchChat:
    """WatchChat 数据类测试。"""

    def test_create_basic(self):
        """创建基本 WatchChat 实例。"""
        c = WatchChat(id="oc_xxx", name="测试群", type="group", mode="auto_import")
        assert c.id == "oc_xxx"
        assert c.name == "测试群"
        assert c.type == "group"
        assert c.mode == "auto_import"
        assert c.okr_tags == []  # 默认空列表

    def test_create_with_tags(self):
        """创建带 okr_tags 的 WatchChat 实例。"""
        c = WatchChat(id="oc_yyy", name="技术群", type="group", mode="confirm", okr_tags=["AI巡检", "搜推"])
        assert c.okr_tags == ["AI巡检", "搜推"]

    def test_create_none_tags(self):
        """okr_tags 传入 None 应转为空列表。"""
        c = WatchChat(id="oc_zzz", name="空标签群", type="single", mode="confirm", okr_tags=None)
        assert c.okr_tags == []

    def test_to_dict(self):
        """验证 to_dict 输出。"""
        c = WatchChat(id="oc_a", name="会话A", type="group", mode="auto_import", okr_tags=["标签1"])
        d = c.to_dict()
        assert d == {"id": "oc_a", "name": "会话A", "type": "group", "mode": "auto_import", "okr_tags": ["标签1"]}

    def test_to_dict_no_tags(self):
        """无 okr_tags 时 to_dict 输出应包含空列表。"""
        c = WatchChat(id="oc_b", name="会话B", type="single", mode="confirm")
        d = c.to_dict()
        assert d["okr_tags"] == []

    def test_repr(self):
        """验证 __repr__ 格式。"""
        c = WatchChat(id="oc_c", name="我的群", type="group", mode="confirm")
        r = repr(c)
        assert "'我的群'" in r
        assert "group" in r
        assert "confirm" in r


# ═══════════════════════════════════════════════════════════════
# FeedConfig 测试
# ═══════════════════════════════════════════════════════════════

class TestFeedConfig:
    """FeedConfig 数据类测试。"""

    def test_create_empty(self):
        """空关注列表 + 默认 topic_config 和 okr_mapping。"""
        cfg = FeedConfig(watch_chats=[])
        assert cfg.watch_chats == []
        assert cfg.topic_config["default_range_days"] == 3
        assert cfg.topic_config["min_msg_length"] == 10
        assert cfg.okr_mapping["enabled"] is True
        assert cfg.okr_mapping["strict_match"] is False

    def test_create_with_custom_config(self):
        """传入自定义配置时不应使用默认值覆盖。"""
        chats = [WatchChat(id="oc_1", name="群1", type="group", mode="auto_import")]
        tcfg = {"default_range_days": 7, "min_msg_length": 5}
        omap = {"enabled": False, "strict_match": True}
        cfg = FeedConfig(watch_chats=chats, topic_config=tcfg, okr_mapping=omap)
        assert cfg.topic_config["default_range_days"] == 7
        assert cfg.topic_config["min_msg_length"] == 5
        assert cfg.okr_mapping["enabled"] is False

    def test_to_dict(self):
        """验证 FeedConfig.to_dict 结构正确。"""
        chats = [WatchChat(id="oc_a", name="会话A", type="group", mode="auto_import")]
        cfg = FeedConfig(watch_chats=chats)
        d = cfg.to_dict()
        assert d["version"] == 1
        assert len(d["watch_chats"]) == 1
        assert d["watch_chats"][0]["id"] == "oc_a"
        assert "topic_config" in d
        assert "okr_mapping" in d


# ═══════════════════════════════════════════════════════════════
# load_feed_config 测试
# ═══════════════════════════════════════════════════════════════

class TestLoadFeedConfig:
    """load_feed_config 纯函数测试。"""

    def test_file_not_exists(self, tmp_path):
        """配置文件不存在时返回空配置。"""
        path = tmp_path / "feeds.json"
        cfg = load_feed_config(path)
        assert cfg.watch_chats == []
        assert cfg.topic_config["default_range_days"] == 3

    def test_valid_json(self, tmp_path):
        """有效 JSON 文件应正确加载。"""
        path = tmp_path / "feeds.json"
        data = {
            "watch_chats": [
                {"id": "oc_1", "name": "群1", "type": "group", "mode": "auto_import", "okr_tags": ["AI"]},
            ],
            "topic_config": {"default_range_days": 5, "min_msg_length": 8},
            "okr_mapping": {"enabled": True, "strict_match": True},
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cfg = load_feed_config(path)
        assert len(cfg.watch_chats) == 1
        assert cfg.watch_chats[0].name == "群1"
        assert cfg.watch_chats[0].okr_tags == ["AI"]
        assert cfg.topic_config["default_range_days"] == 5
        assert cfg.okr_mapping["strict_match"] is True

    def test_missing_fields_in_json(self, tmp_path):
        """JSON 缺少 watch_chats 字段时应优雅处理。"""
        path = tmp_path / "feeds.json"
        path.write_text('{"version": 1}', encoding="utf-8")
        cfg = load_feed_config(path)
        assert cfg.watch_chats == []

    def test_empty_watch_chats(self, tmp_path):
        """watch_chats 为空列表时应返回空列表。"""
        path = tmp_path / "feeds.json"
        path.write_text('{"watch_chats": []}', encoding="utf-8")
        cfg = load_feed_config(path)
        assert cfg.watch_chats == []


# ═══════════════════════════════════════════════════════════════
# save_feed_config 测试
# ═══════════════════════════════════════════════════════════════

class TestSaveFeedConfig:
    """save_feed_config 纯函数测试（通过 mock 隔离文件系统）。"""

    @patch("iris.core.locks.FileLock")
    @patch("iris.utils.shared.atomic_write_json")
    def test_save_calls_atomic_write(self, mock_atomic, mock_lock, tmp_path):
        """验证 save 调用 atomic_write_json 和 FileLock。"""
        cfg = FeedConfig(watch_chats=[WatchChat(id="oc_1", name="群1", type="group", mode="confirm")])
        config_path = tmp_path / "feeds.json"
        save_feed_config(cfg, config_path)
        mock_atomic.assert_called_once_with(config_path, cfg.to_dict())

    @patch("iris.core.locks.FileLock")
    @patch("iris.utils.shared.atomic_write_json")
    def test_save_creates_parent_dir(self, mock_atomic, mock_lock, tmp_path):
        """父目录不存在时自动创建。"""
        cfg = FeedConfig(watch_chats=[])
        deep_path = tmp_path / "sub" / "feeds.json"
        save_feed_config(cfg, deep_path)
        assert deep_path.parent.exists()


# ═══════════════════════════════════════════════════════════════
# write_example_config 测试
# ═══════════════════════════════════════════════════════════════

class TestWriteExampleConfig:
    """write_example_config 纯函数测试。"""

    def test_write_creates_file(self, tmp_path):
        """写入示例配置到目标路径。"""
        config_path = tmp_path / "feeds.json.example"
        write_example_config(config_path)
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["watch_chats"]) == 1
        assert data["watch_chats"][0]["name"] == "数据智能部群"
        assert data["watch_chats"][0]["mode"] == "auto_import"

    def test_write_creates_parent_dir(self, tmp_path):
        """父目录不存在时应自动创建。"""
        deep_path = tmp_path / "deep" / "dir" / "feeds.json.example"
        write_example_config(deep_path)
        assert deep_path.exists()


# ═══════════════════════════════════════════════════════════════
# FeedConfigManager 测试
# ═══════════════════════════════════════════════════════════════

class TestFeedConfigManager:
    """FeedConfigManager 测试（mock 文件读取/写入）。"""

    def test_init_and_list(self, tmp_path):
        """初始化后 list_chats 应返回空列表。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        assert mgr.list_chats() == []

    def test_reload(self, tmp_path):
        """reload 应重新读取配置。"""
        config_path = tmp_path / "feeds.json"
        config_path.write_text(
            '{"watch_chats": [{"id": "oc_x", "name": "群X", "type": "group", "mode": "confirm"}]}',
            encoding="utf-8",
        )
        mgr = FeedConfigManager(config_path)
        assert len(mgr.list_chats()) == 1
        # 修改文件后 reload
        config_path.write_text('{"watch_chats": []}', encoding="utf-8")
        mgr.reload()
        assert mgr.list_chats() == []

    @patch("iris.feed.feed_config.save_feed_config")
    def test_add_chat(self, mock_save, tmp_path):
        """add_chat 应追加到列表并调用 save。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        c = mgr.add_chat("oc_new", "新群", chat_type="group", mode="confirm", okr_tags=["T1"])
        assert c.id == "oc_new"
        assert len(mgr.list_chats()) == 1
        mock_save.assert_called_once()

    @patch("iris.feed.feed_config.save_feed_config")
    def test_add_chat_duplicate(self, mock_save, tmp_path):
        """重复添加同一 chat_id 应返回已有实例，不重复保存。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        mgr.add_chat("oc_dup", "重复群")
        mock_save.reset_mock()
        c = mgr.add_chat("oc_dup", "重复群")
        assert c.id == "oc_dup"
        assert len(mgr.list_chats()) == 1
        mock_save.assert_not_called()

    @patch("iris.feed.feed_config.save_feed_config")
    def test_remove_chat_by_id(self, mock_save, tmp_path):
        """按 id 移除关注会话。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        mgr.add_chat("oc_1", "群1")
        mgr.add_chat("oc_2", "群2")
        mock_save.reset_mock()
        result = mgr.remove_chat("oc_1")
        assert result is True
        assert len(mgr.list_chats()) == 1
        assert mgr.list_chats()[0].id == "oc_2"

    @patch("iris.feed.feed_config.save_feed_config")
    def test_remove_chat_by_name(self, mock_save, tmp_path):
        """按名称移除关注会话。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        mgr.add_chat("oc_x", "目标群")
        mock_save.reset_mock()
        result = mgr.remove_chat("目标群")
        assert result is True
        assert mgr.list_chats() == []

    @patch("iris.feed.feed_config.save_feed_config")
    def test_remove_chat_not_found(self, mock_save, tmp_path):
        """不存在的 id/name 应返回 False。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        result = mgr.remove_chat("不存在的群")
        assert result is False
        mock_save.assert_not_called()

    @patch("iris.feed.feed_config.save_feed_config")
    def test_update_chat(self, mock_save, tmp_path):
        """update_chat 应更新指定会话的字段。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        mgr.add_chat("oc_upd", "原始名")
        mock_save.reset_mock()
        updated = mgr.update_chat("oc_upd", mode="auto_import", okr_tags=["T1", "T2"], name="新名字")
        assert updated is not None
        assert updated.mode == "auto_import"
        assert updated.okr_tags == ["T1", "T2"]
        assert updated.name == "新名字"

    @patch("iris.feed.feed_config.save_feed_config")
    def test_update_chat_not_found(self, mock_save, tmp_path):
        """不存在的 chat_id 应返回 None。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        result = mgr.update_chat("oc_nonexist", mode="auto_import")
        assert result is None
        mock_save.assert_not_called()

    def test_pending_queue_path(self, tmp_path):
        """get_pending_queue_path 返回正确路径。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        p = mgr.get_pending_queue_path(tmp_path)
        assert p == tmp_path / "feed_pending.json"

    def test_load_pending_file_not_exists(self, tmp_path):
        """待确认队列文件不存在时返回空列表。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        pending = mgr.load_pending(tmp_path)
        assert pending == []

    def test_load_pending_file_exists(self, tmp_path):
        """载入已有的待确认队列。"""
        config_path = tmp_path / "feeds.json"
        pending_file = tmp_path / "feed_pending.json"
        data = [{"topic_id": "t1", "title": "话题1", "status": "pending"}]
        pending_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        mgr = FeedConfigManager(config_path)
        pending = mgr.load_pending(tmp_path)
        assert len(pending) == 1
        assert pending[0]["topic_id"] == "t1"

    @patch("iris.core.locks.FileLock")
    @patch("iris.utils.shared.atomic_write_json")
    def test_save_pending(self, mock_atomic, mock_lock, tmp_path):
        """save_pending 应调用 atomic_write_json。"""
        config_path = tmp_path / "feeds.json"
        mgr = FeedConfigManager(config_path)
        pending = [{"topic_id": "t1", "title": "话题1"}]
        mgr.save_pending(tmp_path, pending)
        mock_atomic.assert_called_once_with(tmp_path / "feed_pending.json", pending)
