"""feed 包单元测试 — 简报生成（文件名 / 模板渲染 / 文档匹配）。"""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from iris.feed._brief_generator import (
    _sanitize_filename,
    _build_filename,
    BriefGenerator,
)
from iris.feed._types import (
    RawMessage,
    DetectedTopic,
    SourceRef,
    Quote,
    ConvertedDoc,
)


def _make_topic(
    title="测试话题",
    summary="这是一个测试话题的摘要",
    key_status="进行中",
    discussion_points=None,
    decisions=None,
    quotes=None,
    participants=None,
    source_chats=None,
    okr_tags=None,
    okr_match_strength="none",
    is_update=False,
    topic_id="feed-20260728-001",
    messages=None,
):
    return DetectedTopic(
        topic_id=topic_id,
        title=title,
        summary=summary,
        key_status=key_status,
        discussion_points=discussion_points or [],
        decisions=decisions or [],
        quotes=quotes or [],
        participants=participants or [],
        source_chats=source_chats or [SourceRef(type="group", name="测试群", msg_count=3)],
        is_update=is_update,
        okr_tags=okr_tags or [],
        okr_match_strength=okr_match_strength,
        messages=messages or [],
    )


# ═══════════════════════════════════════════════════════════════
# _sanitize_filename 测试
# ═══════════════════════════════════════════════════════════════

class TestSanitizeFilename:
    """文件名安全化测试。"""

    def test_normal_chinese(self):
        """正常中文标题不产生变化。"""
        assert _sanitize_filename("智能巡查方案") == "智能巡查方案"

    def test_special_chars_removed(self):
        """特殊字符应被去除。"""
        result = _sanitize_filename("测试/话题:评审<草案>")
        assert "/" not in result
        assert ":" not in result
        assert "<" not in result
        assert ">" not in result

    def test_spaces_removed(self):
        """空格应被删除。"""
        assert _sanitize_filename("测试 话题 讨论") == "测试话题讨论"

    def test_punctuation_removed(self):
        """标点符号应被去除。"""
        assert _sanitize_filename("话题！这是？问题。") == "话题这是问题"

    def test_empty_result(self):
        """全特殊字符可能得到空字符串。"""
        result = _sanitize_filename("!@#$%^&*()")
        assert result == ""


# ═══════════════════════════════════════════════════════════════
# _build_filename 测试
# ═══════════════════════════════════════════════════════════════

class TestBuildFilename:
    """简报文件名构建测试。"""

    def test_normal(self):
        """标准文件名格式。"""
        topic = _make_topic(title="智能巡查方案")
        name = _build_filename(topic, "20260728")
        assert "20260728" in name
        assert "智能巡查方案" in name
        assert "简报" in name
        assert "from飞书" in name
        assert name.endswith(".md")

    def test_with_special_title(self):
        """含特殊字符的标题在文件名中应被清理。"""
        topic = _make_topic(title="测试/方案:评审")
        name = _build_filename(topic, "20260728")
        assert "/" not in name
        assert ":" not in name


# ═══════════════════════════════════════════════════════════════
# _match_docs 测试
# ═══════════════════════════════════════════════════════════════

class TestMatchDocs:
    """文档匹配测试。"""

    def test_doc_matches_source_chat(self):
        """来源群名匹配的文档应返回。"""
        topic = _make_topic(source_chats=[SourceRef(type="group", name="技术群", msg_count=5)])
        docs = [
            ConvertedDoc(original_url="url1", local_path=Path("/a.md"), relative_path="a.md", title="文档A", source_chat="技术群"),
            ConvertedDoc(original_url="url2", local_path=Path("/b.md"), relative_path="b.md", title="文档B", source_chat="产品群"),
        ]
        result = BriefGenerator._match_docs(topic, docs)
        assert len(result) == 1
        assert result[0].title == "文档A"

    def test_no_matching_docs(self):
        """无匹配文档时返回空列表。"""
        topic = _make_topic(source_chats=[SourceRef(type="group", name="技术群", msg_count=5)])
        docs = [
            ConvertedDoc(original_url="url1", local_path=Path("/a.md"), relative_path="a.md", title="文档A", source_chat="产品群"),
        ]
        result = BriefGenerator._match_docs(topic, docs)
        assert result == []

    def test_empty_docs(self):
        """空文档列表返回空列表。"""
        topic = _make_topic()
        result = BriefGenerator._match_docs(topic, [])
        assert result == []

    def test_multiple_matches(self):
        """多个来源匹配应返回多个文档。"""
        topic = _make_topic(source_chats=[
            SourceRef(type="group", name="技术群", msg_count=3),
            SourceRef(type="group", name="产品群", msg_count=2),
        ])
        docs = [
            ConvertedDoc(original_url="u1", local_path=Path("/1.md"), relative_path="1.md", title="D1", source_chat="技术群"),
            ConvertedDoc(original_url="u2", local_path=Path("/2.md"), relative_path="2.md", title="D2", source_chat="产品群"),
            ConvertedDoc(original_url="u3", local_path=Path("/3.md"), relative_path="3.md", title="D3", source_chat="设计群"),
        ]
        result = BriefGenerator._match_docs(topic, docs)
        assert len(result) == 2
        assert {d.title for d in result} == {"D1", "D2"}


# ═══════════════════════════════════════════════════════════════
# BriefGenerator 测试
# ═══════════════════════════════════════════════════════════════

class TestBriefGenerator:
    """BriefGenerator 主类测试（mock 文件写入）。"""

    def test_generate_empty_topics(self, tmp_path):
        """空话题列表应返回空列表。"""
        gen = BriefGenerator(tmp_path)
        files = gen.generate([], [], "20260728")
        assert files == []

    def test_generate_dry_run(self, tmp_path):
        """dry_run 模式下不应写入文件。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(title="DRY测试")
        files = gen.generate([topic], [], "20260728", dry_run=True)
        assert len(files) == 1
        # dry_run 模式下文件应不存在
        assert not files[0].exists()

    def test_generate_with_topic(self, tmp_path):
        """正常生成一份简报。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(title="技术方案评审")
        files = gen.generate([topic], [], "20260728")
        assert len(files) == 1
        assert files[0].exists()
        content = files[0].read_text(encoding="utf-8")
        assert "# 技术方案评审" in content
        assert "topic_id: feed-20260728-001" in content
        assert "测试话题的摘要" in content

    def test_generate_output_dir_created(self, tmp_path):
        """应自动创建输出目录 09-工作简报/YYYYMM。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(title="自动目录")
        gen.generate([topic], [], "20260728")
        output_dir = tmp_path / "09-工作简报" / "202607"
        assert output_dir.exists()

    def test_generate_with_okr_tags(self, tmp_path):
        """含 OKR 标签的话题应生成 OKR 关联章节。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(
            title="智能巡查进展",
            okr_tags=["O1-KR1: 【质量】图像验证"],
            okr_match_strength="strong",
        )
        files = gen.generate([topic], [], "20260728")
        content = files[0].read_text(encoding="utf-8")
        assert "OKR 关联" in content
        assert "O1-KR1" in content
        assert "匹配强度" in content
        assert "strong" in content

    def test_generate_without_okr_tags(self, tmp_path):
        """无 OKR 标签应显示「未关联 OKR」。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(title="日常讨论", okr_tags=[])
        files = gen.generate([topic], [], "20260728")
        content = files[0].read_text(encoding="utf-8")
        assert "未关联 OKR" in content

    def test_generate_with_quotes(self, tmp_path):
        """应包含原始消息精选章节。"""
        gen = BriefGenerator(tmp_path)
        quotes = [
            Quote(text="我觉得可以用微服务架构", speaker="张三", time="07-28 10:00"),
            Quote(text="同意这个方案", speaker="李四", time="07-28 10:05"),
        ]
        topic = _make_topic(title="架构讨论", quotes=quotes)
        files = gen.generate([topic], [], "20260728")
        content = files[0].read_text(encoding="utf-8")
        assert "原始消息精选" in content
        assert "微服务架构" in content
        assert "张三" in content
        assert "李四" in content

    def test_generate_with_decisions(self, tmp_path):
        """应包含已明确决策章节。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(title="决策讨论", decisions=["采用微服务架构", "使用Python语言"])
        files = gen.generate([topic], [], "20260728")
        content = files[0].read_text(encoding="utf-8")
        assert "已明确的决策" in content
        assert "采用微服务架构" in content
        assert "使用Python语言" in content

    def test_generate_with_participants(self, tmp_path):
        """应包含参与者列表。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(title="全员会", participants=["张三", "李四", "王五"])
        files = gen.generate([topic], [], "20260728")
        content = files[0].read_text(encoding="utf-8")
        assert "参与者" in content
        assert "张三" in content
        assert "李四" in content
        assert "王五" in content

    def test_generate_frontmatter_fields(self, tmp_path):
        """frontmatter 应包含 type / date / topic_id / sources 等字段。"""
        gen = BriefGenerator(tmp_path)
        topic = _make_topic(title="前奏测试")
        files = gen.generate([topic], [], "20260728")
        content = files[0].read_text(encoding="utf-8")
        assert "type: discussion" in content
        assert "date: 20260728" in content
        assert "sources:" in content
        assert "测试群" in content
        assert "msg_count: 3" in content

    def test_generate_multiple_topics(self, tmp_path):
        """多个话题应生成多个简报文件。"""
        gen = BriefGenerator(tmp_path)
        t1 = _make_topic(title="话题一", topic_id="feed-20260728-001")
        t2 = _make_topic(title="话题二", topic_id="feed-20260728-002")
        files = gen.generate([t1, t2], [], "20260728")
        assert len(files) == 2
        assert files[0].exists()
        assert files[1].exists()

    def test_generate_fallback_quotes_from_messages(self, tmp_path):
        """无 quotes 时应从 messages 提取原始消息。"""
        gen = BriefGenerator(tmp_path)
        msg = RawMessage(
            msg_id="m1", chat_id="c1", chat_name="群1", chat_type="group",
            sender_id="s1", sender_name="张三",
            content="这是一条原始讨论消息，关于技术方案的设计思路",
            raw_content={"text": "内容"},
            msg_type="text",
            send_time=datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc),
        )
        topic = _make_topic(title="回退测试", quotes=[], messages=[msg])
        files = gen.generate([topic], [], "20260728")
        content = files[0].read_text(encoding="utf-8")
        assert "原始讨论消息" in content
        assert "张三" in content
