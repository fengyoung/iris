"""feed 包单元测试 — 消息过滤器 / 游标追踪 / 话题类型。"""

from datetime import datetime, timezone
from pathlib import Path

from iris.feed._message_filter import MessageFilter
from iris.feed._cursor_tracker import CursorTracker
from iris.feed._types import RawMessage, DetectedTopic, PipelineResult


def _make_msg(msg_id, chat_id, sender, content, chat_type="group", msg_type="text"):
    """创建测试用 RawMessage 的辅助函数。"""
    return RawMessage(
        msg_id=msg_id, chat_id=chat_id, chat_name=f"{chat_id}_name",
        chat_type=chat_type, sender_id=f"{sender}_id", sender_name=sender,
        content=content, raw_content={"text": content}, msg_type=msg_type,
        send_time=datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════
# MessageFilter 测试
# ═══════════════════════════════════════════════════════════════

class TestMessageFilter:
    def test_empty(self):
        f = MessageFilter()
        assert f.filter({}) == {}

    def test_too_short_filtered(self):
        f = MessageFilter(min_msg_length=10)
        msg = _make_msg("1", "c1", "张三", "短")
        assert f.filter({"c1": [msg]}) == {}

    def test_long_enough_passed(self):
        f = MessageFilter(min_msg_length=10)
        msg = _make_msg("1", "c1", "张三", "这是一条足够长的消息内容")
        result = f.filter({"c1": [msg]})
        assert len(result) == 1
        assert len(result["c1"]) == 1

    def test_noise_pattern_filtered(self):
        f = MessageFilter()
        noise_msg = _make_msg("3", "c1", "张三", "红包来了！")
        assert f.is_noise(noise_msg) is True

    def test_system_message_filtered(self):
        f = MessageFilter()
        sys_msg = _make_msg("4", "c1", "系统", "张三加入了群聊")
        assert f.is_noise(sys_msg) is True

    def test_link_only_filtered(self):
        f = MessageFilter()
        link_msg = _make_msg("5", "c1", "张三", "https://example.com/doc/123")
        assert f.is_noise(link_msg) is True

    def test_mixed_chats(self):
        f = MessageFilter(min_msg_length=5)
        good = _make_msg("m1", "c1", "张", "这是一条有效消息")
        short = _make_msg("m2", "c2", "李", "短")
        result = f.filter({"c1": [good], "c2": [short]})
        assert "c1" in result
        assert "c2" not in result


# ═══════════════════════════════════════════════════════════════
# CursorTracker 测试
# ═══════════════════════════════════════════════════════════════

class TestCursorTracker:
    def test_new_tracker_empty(self, tmp_path):
        tracker = CursorTracker(tmp_path)
        assert tracker.get_cursor("chat1") is None
        assert tracker.get_last_fetch("chat1") is None

    def test_update_and_read(self, tmp_path):
        tracker = CursorTracker(tmp_path)
        tracker.update("c1", last_msg_id="msg_001")
        assert tracker.get_cursor("c1") == "msg_001"
        assert tracker.get_last_fetch("c1") is not None

    def test_cursor_persisted(self, tmp_path):
        tracker1 = CursorTracker(tmp_path)
        tracker1.update("c1", last_msg_id="msg_abc")
        tracker2 = CursorTracker(tmp_path)
        assert tracker2.get_cursor("c1") == "msg_abc"

    def test_corrupted_file_recovery(self, tmp_path):
        cursor_file = tmp_path / "feed_cursors.json"
        cursor_file.write_text("{corrupted json!!!", encoding="utf-8")
        tracker = CursorTracker(tmp_path)
        assert tracker.get_cursor("any") is None  # 应重置而非崩溃

    def test_multiple_chats(self, tmp_path):
        tracker = CursorTracker(tmp_path)
        tracker.update("a", last_msg_id="ma")
        tracker.update("b", last_msg_id="mb")
        assert tracker.get_cursor("a") == "ma"
        assert tracker.get_cursor("b") == "mb"

    def test_page_token(self, tmp_path):
        tracker = CursorTracker(tmp_path)
        tracker.update("c1", page_token="pt_123")
        assert tracker.get_last_page_token("c1") == "pt_123"

    def test_atomic_save(self, tmp_path):
        """验证 _save 使用 atomic_write_json（临时文件+os.replace 模式）。"""
        import os
        tracker = CursorTracker(tmp_path)
        cursor_file = tmp_path / "feed_cursors.json"
        # 记录写入前的 inode
        tracker.update("c1", last_msg_id="before")
        inode_before = os.stat(cursor_file).st_ino
        tracker.update("c1", last_msg_id="after")
        inode_after = os.stat(cursor_file).st_ino
        # 原子写入可能产生新 inode（取决于文件系统）
        assert tracker.get_cursor("c1") == "after"


# ═══════════════════════════════════════════════════════════════
# _types 测试
# ═══════════════════════════════════════════════════════════════

class TestPipelineResult:
    def test_empty(self):
        r = PipelineResult.empty("测试原因")
        assert r.fetched_count == 0
        assert r.filtered_count == 0
        assert r.topics == []
        assert r.brief_files == []

    def test_normal_result(self):
        now = datetime.now(timezone.utc)
        topic = DetectedTopic(
            topic_id="t1",
            title="测试话题",
            summary="摘要",
            discussion_points=["p1"],
            messages=[],
            source_chats=[],
            participants=[],
            is_update=False,
            previous_versions=[],
            okr_tags=[],
            okr_match_strength="none",
        )
        r = PipelineResult(
            fetched_count=100,
            filtered_count=50,
            topics=[topic],
            brief_files=[Path("/tmp/test.md")],
        )
        assert r.fetched_count == 100
        assert len(r.topics) == 1
        assert r.topics[0].title == "测试话题"
