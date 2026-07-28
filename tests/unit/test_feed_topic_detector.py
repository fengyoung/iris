"""feed 包单元测试 — 话题检测（规则分割 + LLM 聚合 + 历史加载）。"""

import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

from iris.feed._topic_detector import (
    _segment_by_time,
    _build_candidate_groups,
    _load_history_topics,
    _shorten_title,
    TopicDetector,
)
from iris.feed._types import RawMessage


def _make_msg(content, send_time=None, sender_name="张三", chat_id="c1", chat_name=None):
    """创建测试用 RawMessage，支持自定义发送时间。"""
    if send_time is None:
        send_time = datetime.now(timezone.utc)
    return RawMessage(
        msg_id=f"mid_{id(content)}_{send_time.timestamp()}",
        chat_id=chat_id,
        chat_name=chat_name or f"{chat_id}_name",
        chat_type="group",
        sender_id=f"{sender_name}_id",
        sender_name=sender_name,
        content=content,
        raw_content={"text": content},
        msg_type="text",
        send_time=send_time,
        has_doc_link=False,
    )


def _make_candidates(*groups):
    """创建候选话题组列表。"""
    return [(f"群{i}", list(msgs)) for i, msgs in enumerate(groups)]


# ═══════════════════════════════════════════════════════════════
# _segment_by_time 测试
# ═══════════════════════════════════════════════════════════════

class TestSegmentByTime:
    """时间窗口切分测试。"""

    def test_empty_list(self):
        """空列表应返回空列表。"""
        assert _segment_by_time([]) == []

    def test_single_message(self):
        """单条消息应返回一个片段。"""
        msgs = [_make_msg("测试消息")]
        segments = _segment_by_time(msgs)
        assert len(segments) == 1
        assert len(segments[0]) == 1

    def test_same_window(self):
        """同一时间窗口内的多条消息应归为一个片段。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("消息1", send_time=base),
            _make_msg("消息2", send_time=base + timedelta(minutes=5)),
            _make_msg("消息3", send_time=base + timedelta(minutes=10)),
        ]
        segments = _segment_by_time(msgs, window_minutes=30)
        assert len(segments) == 1
        assert len(segments[0]) == 3

    def test_cross_window(self):
        """间隔超过时间窗口的消息应拆分为多个片段。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("早上的讨论", send_time=base),
            _make_msg("下午的讨论", send_time=base + timedelta(hours=3)),
        ]
        segments = _segment_by_time(msgs, window_minutes=30)
        assert len(segments) == 2
        assert len(segments[0]) == 1
        assert len(segments[1]) == 1

    def test_exact_boundary(self):
        """刚好等于时间窗口边界的消息不应断开（gap == window 时不断）。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("第一条", send_time=base),
            _make_msg("第二条", send_time=base + timedelta(minutes=30)),
        ]
        segments = _segment_by_time(msgs, window_minutes=30)
        # gap == window（30 分钟），不应断开（实现为 gap > window 才断）
        assert len(segments) == 1

    def test_over_boundary(self):
        """超过时间窗口边界的消息应断开。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("第一条", send_time=base),
            _make_msg("第二条", send_time=base + timedelta(minutes=31)),
        ]
        segments = _segment_by_time(msgs, window_minutes=30)
        # gap > window（31 > 30），应断开为两段
        assert len(segments) == 2

    def test_unsorted_input(self):
        """乱序输入应先按时间排序再切分。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("第三条", send_time=base + timedelta(minutes=40)),
            _make_msg("第一条", send_time=base),
            _make_msg("第二条", send_time=base + timedelta(minutes=5)),
        ]
        segments = _segment_by_time(msgs, window_minutes=30)
        assert len(segments) == 2
        # 第一条和第二条在窗口内，第三条在另一个窗口
        assert len(segments[0]) == 2
        assert len(segments[1]) == 1
        # 验证排序后的标题顺序
        assert segments[0][0].content == "第一条"
        assert segments[0][1].content == "第二条"
        assert segments[1][0].content == "第三条"


# ═══════════════════════════════════════════════════════════════
# _build_candidate_groups 测试
# ═══════════════════════════════════════════════════════════════

class TestBuildCandidateGroups:
    """候选话题组构建测试。"""

    def test_empty_input(self):
        """空输入应返回空列表。"""
        assert _build_candidate_groups({}) == []

    def test_ignore_empty_chat(self):
        """存在空消息列表的会话应被忽略。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        result = _build_candidate_groups({
            "empty": [],
            "real": [
                _make_msg("消息1", chat_id="real", send_time=base),
                _make_msg("消息2", chat_id="real", send_time=base + timedelta(minutes=1)),
            ],
        })
        assert len(result) == 1
        assert result[0][0] == "real_name"

    def test_below_min_messages(self):
        """少于 topic_min_messages 的片段应被过滤。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("唯一消息", send_time=base),
        ]
        result = _build_candidate_groups({"c1": msgs}, topic_min_messages=2)
        assert result == []

    def test_meet_min_messages(self):
        """达到 topic_min_messages 的片段应保留。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("消息1", send_time=base),
            _make_msg("消息2", send_time=base + timedelta(minutes=1)),
        ]
        result = _build_candidate_groups({"c1": msgs}, topic_min_messages=2)
        assert len(result) == 1
        assert len(result[0][1]) == 2

    def test_chat_name_fallback(self):
        """chat_name 为空时应使用 chat_id 前缀作为来源名。"""
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        result = _build_candidate_groups({
            "very_long_chat_id_12345": [
                _make_msg("消息1", chat_id="very_long_chat_id_12345", chat_name="",
                          send_time=base),
                _make_msg("消息2", chat_id="very_long_chat_id_12345", chat_name="",
                          send_time=base + timedelta(minutes=1)),
            ],
        })
        assert len(result) == 1
        assert "very_long_chat_id" in result[0][0]


# ═══════════════════════════════════════════════════════════════
# _shorten_title 测试
# ═══════════════════════════════════════════════════════════════

class TestShortenTitle:
    """标题缩短辅助函数测试。"""

    def test_short_text_unchanged(self):
        """短于最大长度的文本不应被截断。"""
        assert _shorten_title("Hello") == "Hello"

    def test_long_text_truncated(self):
        """超过最大长度的文本应被截断并添加省略号。"""
        text = "这是一条非常长的消息内容，需要被截断处理"
        result = _shorten_title(text, max_len=10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_at_mention_removed(self):
        """@mention 应被移除。"""
        assert _shorten_title("@张三 看看这个方案") == "看看这个方案"

    def test_url_removed(self):
        """URL 应被移除。"""
        result = _shorten_title("文档链接 https://example.com/doc 请查阅")
        # 移除链接后：文档链接  请查阅 → "文档链接 请查阅"
        assert "https://" not in result

    def test_mention_and_url_simultaneously(self):
        """同时含有 @mention 和 URL 时应都移除。"""
        result = _shorten_title("@李四 方案见 https://example.com/doc")
        assert "@李四" not in result
        assert "https://" not in result
        assert "方案见" in result


# ═══════════════════════════════════════════════════════════════
# _load_history_topics 测试
# ═══════════════════════════════════════════════════════════════

class TestLoadHistoryTopics:
    """历史话题加载测试（mock 文件系统）。"""

    def test_dir_not_exists(self, tmp_path):
        """简报目录不存在时返回空列表。"""
        result = _load_history_topics(tmp_path / "nonexistent")
        assert result == []

    def test_empty_dir(self, tmp_path):
        """空目录时返回空列表。"""
        brief_dir = tmp_path / "09-工作简报"
        brief_dir.mkdir(parents=True)
        # 创建月份子目录但无文件
        month_dir = brief_dir / "202607"
        month_dir.mkdir()
        result = _load_history_topics(brief_dir)
        assert result == []

    def test_load_with_files(self, tmp_path):
        """成功加载简报文件。"""
        brief_dir = tmp_path / "09-工作简报"
        month_dir = brief_dir / "202607"
        month_dir.mkdir(parents=True)
        brief_file = month_dir / "20260728-简报-测试话题（from飞书）.md"
        brief_file.write_text(
            "---\ntopic_id: feed-20260728-001\n---\n\n# 测试话题\n\n内容",
            encoding="utf-8",
        )
        result = _load_history_topics(brief_dir)
        assert len(result) == 1
        assert result[0]["title"] == "测试话题"
        assert result[0]["topic_id"] == "feed-20260728-001"

    def test_max_count_respected(self, tmp_path):
        """超过 max_count 的简报文件应被截断。"""
        brief_dir = tmp_path / "09-工作简报"
        month_dir = brief_dir / "202607"
        month_dir.mkdir(parents=True)
        for i in range(5):
            f = month_dir / f"20260728-简报-话题{i}（from飞书）.md"
            f.write_text(f"---\ntopic_id: feed-20260728-{i:03d}\n---\n\n# 话题{i}", encoding="utf-8")
        result = _load_history_topics(brief_dir, max_count=3)
        assert len(result) == 3

    def test_skip_non_brief_files(self, tmp_path):
        """glob 模式应只匹配 -简报- 文件。"""
        brief_dir = tmp_path / "09-工作简报"
        month_dir = brief_dir / "202607"
        month_dir.mkdir(parents=True)
        (month_dir / "readme.md").write_text("# readme", encoding="utf-8")
        (month_dir / "20260728-简报-有效话题（from飞书）.md").write_text(
            "---\ntopic_id: feed-001\n---\n\n# 有效话题", encoding="utf-8",
        )
        result = _load_history_topics(brief_dir)
        assert len(result) == 1

    def test_malformed_file_skipped(self, tmp_path):
        """格式损坏的文件应被跳过而不引发异常。"""
        brief_dir = tmp_path / "09-工作简报"
        month_dir = brief_dir / "202607"
        month_dir.mkdir(parents=True)
        bad_file = month_dir / "20260728-简报-损坏文件（from飞书）.md"
        bad_file.write_text("{not valid markdown structure", encoding="utf-8")
        result = _load_history_topics(brief_dir)
        # 没有title时 fallback 到 stem
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════
# TopicDetector 测试
# ═══════════════════════════════════════════════════════════════

class TestTopicDetector:
    """TopicDetector 主类测试（mock LLM 调用）。"""

    def _make_detector(self, brief_dir=None, topic_config=None, okr_context=""):
        llm = MagicMock()
        if brief_dir is None:
            brief_dir = Path("/tmp/briefs")
        return TopicDetector(llm, brief_dir, topic_config=topic_config, okr_context=okr_context), llm

    def test_detect_empty_messages(self, tmp_path):
        """空消息列表应返回空列表。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        result = detector.detect({})
        assert result == []

    def test_detect_all_chats_empty(self, tmp_path):
        """消息字典中存在空值但仍应返回空。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        result = detector.detect({"c1": [], "c2": []})
        assert result == []

    def test_simple_detect_low_volume(self, tmp_path):
        """消息量少于 5 条且候选组 ≤1 时走简单检测。"""
        detector, llm = self._make_detector(brief_dir=tmp_path)
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("今天我们来讨论一下新功能的设计方案", send_time=base),
            _make_msg("我觉得可以用微服务架构来实现", send_time=base + timedelta(minutes=5)),
        ]
        result = detector.detect({"c1": msgs})
        # 简单检测不应调 LLM
        assert len(result) == 1
        assert result[0].title
        llm.generate.assert_not_called()

    def test_simple_detect_generates_title(self, tmp_path):
        """简单检测应从首条消息内容生成标题。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("关于AI巡检的讨论", send_time=base),
            _make_msg("我们可以在现有系统上增加巡检功能", send_time=base + timedelta(minutes=1)),
        ]
        result = detector.detect({"c1": msgs})
        assert "AI巡检" in result[0].title

    def test_simple_detect_participants(self, tmp_path):
        """简单检测应提取参与者列表。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("第一条消息", sender_name="张三", send_time=base),
            _make_msg("第二条消息", sender_name="李四", send_time=base + timedelta(minutes=1)),
        ]
        result = detector.detect({"c1": msgs})
        assert "张三" in result[0].participants
        assert "李四" in result[0].participants

    def test_parse_llm_response_valid_json(self, tmp_path):
        """解析有效的 LLM JSON 输出应正确构建 DetectedTopic。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        candidates = _make_candidates(
            [_make_msg("讨论A1"), _make_msg("讨论A2")],
            [_make_msg("讨论B1")],
        )
        llm_output = json.dumps([
            {
                "title": "架构设计评审",
                "summary": "对系统架构进行了评审",
                "discussion_points": ["微服务拆分", "接口规范"],
                "participants": ["张三", "李四"],
                "group_indices": [0],
                "is_valuable": True,
                "is_update": False,
                "quotes": [
                    {"text": "可以用微服务", "speaker": "张三", "time": "07-28 10:00"},
                ],
            }
        ], ensure_ascii=False)
        result = detector._parse_llm_response(llm_output, candidates)
        assert len(result) == 1
        assert result[0].title == "架构设计评审"
        assert "微服务拆分" in result[0].discussion_points
        assert len(result[0].quotes) == 1
        assert result[0].is_update is False

    def test_parse_llm_response_with_code_fence(self, tmp_path):
        """LLM 输出含 ```json 代码块时应正确提取。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        candidates = _make_candidates([_make_msg("测试内容")])
        llm_output = "以下是我分析的结果：\n```json\n[{\"title\": \"测试话题\", \"summary\": \"摘要\", \"is_valuable\": true}]\n```\n"
        result = detector._parse_llm_response(llm_output, candidates)
        assert len(result) == 1
        assert result[0].title == "测试话题"

    def test_parse_llm_response_malformed(self, tmp_path):
        """非 JSON 输出应退回简单检测。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        candidates = _make_candidates(
            [_make_msg("唯一话题的讨论消息")],
        )
        result = detector._parse_llm_response("这不是JSON格式的输出", candidates)
        # 退回简单检测
        assert len(result) == 1

    def test_parse_llm_response_filter_not_valuable(self, tmp_path):
        """is_valuable 为 false 的话题应被过滤。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        candidates = _make_candidates([_make_msg("闲聊")])
        llm_output = json.dumps([
            {"title": "闲聊", "summary": "无价值", "is_valuable": False},
            {"title": "重要话题", "summary": "有价值", "is_valuable": True, "group_indices": [0]},
        ], ensure_ascii=False)
        result = detector._parse_llm_response(llm_output, candidates)
        assert len(result) == 1
        assert result[0].title == "重要话题"

    def test_parse_llm_response_okr_match(self, tmp_path):
        """OKR 匹配信息应正确解析到 DetectedTopic。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        candidates = _make_candidates([_make_msg("OKR相关工作")])
        llm_output = json.dumps([
            {
                "title": "AI巡检进展",
                "summary": "讨论AI巡检方案",
                "is_valuable": True,
                "group_indices": [0],
                "okr_match": {"kr_id": "O1-KR1", "match_strength": "strong", "reason": "直接相关"},
            }
        ], ensure_ascii=False)
        result = detector._parse_llm_response(llm_output, candidates)
        assert len(result) == 1
        assert result[0].okr_tags == ["O1-KR1"]
        assert result[0].okr_match_strength == "strong"

    def test_parse_llm_response_previous_versions(self, tmp_path):
        """is_update=True 应正确设置 previous_versions。"""
        detector, _ = self._make_detector(brief_dir=tmp_path)
        candidates = _make_candidates([_make_msg("更新内容")])
        llm_output = json.dumps([
            {
                "title": "话题更新",
                "summary": "这是旧话题的更新",
                "is_valuable": True,
                "group_indices": [0],
                "is_update": True,
                "update_of": "旧话题标题",
            }
        ], ensure_ascii=False)
        result = detector._parse_llm_response(llm_output, candidates)
        assert result[0].is_update is True
        assert result[0].previous_versions == ["旧话题标题"]

    def test_llm_fallback_on_error(self, tmp_path):
        """LLM 调用失败应退回简单检测。"""
        llm = MagicMock()
        llm.generate.side_effect = Exception("API error")
        brief_dir = tmp_path
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("第一条讨论消息", send_time=base),
            _make_msg("第二条讨论消息", send_time=base + timedelta(minutes=5)),
            _make_msg("第三条讨论消息", send_time=base + timedelta(minutes=10)),
            _make_msg("第四条讨论消息", send_time=base + timedelta(minutes=15)),
            _make_msg("第五条讨论消息", send_time=base + timedelta(minutes=20)),
        ]
        detector = TopicDetector(llm, brief_dir)
        result = detector.detect({"c1": msgs})
        assert len(result) == 1  # fallback 到简单检测

    def test_config_params_passed(self, tmp_path):
        """自定义 topic_config 参数应生效。"""
        llm = MagicMock()
        brief_dir = tmp_path
        config = {"time_window_minutes": 120, "topic_min_messages": 3, "max_topics_per_run": 10}
        detector = TopicDetector(llm, brief_dir, topic_config=config)
        # 窗口扩大，消息间隔应都在窗口内
        base = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
        msgs = [
            _make_msg("消息1", send_time=base),
            _make_msg("消息2", send_time=base + timedelta(hours=1)),
            _make_msg("消息3", send_time=base + timedelta(hours=1.5)),
        ]
        result = detector.detect({"c1": msgs})
        assert len(result) == 1
