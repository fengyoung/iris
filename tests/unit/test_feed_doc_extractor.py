"""feed 包单元测试 — 文档提取器 DocExtractor。"""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from iris.feed._doc_extractor import (
    DocExtractor,
    _extract_doc_urls,
    _collect_topic_urls,
    _FEISHU_DOC_URL_PATTERN,
)
from iris.feed._types import RawMessage, DetectedTopic, SourceRef, ConvertedDoc


def _make_msg(msg_id, chat_id, sender, content, chat_type="group", msg_type="text",
              send_time=None):
    """创建测试用 RawMessage。"""
    return RawMessage(
        msg_id=msg_id, chat_id=chat_id, chat_name=f"{chat_id}_name",
        chat_type=chat_type, sender_id=f"{sender}_id", sender_name=sender,
        content=content, raw_content={"text": content}, msg_type=msg_type,
        send_time=send_time or datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc),
    )


def _make_topic(topic_id, title, messages, okr_tags=None):
    """创建测试用 DetectedTopic。"""
    return DetectedTopic(
        topic_id=topic_id,
        title=title,
        summary="测试摘要",
        messages=messages,
        source_chats=[SourceRef(type="group", name="测试群", msg_count=len(messages))],
        participants=list({m.sender_name for m in messages}),
        okr_tags=okr_tags or [],
    )


# ═══════════════════════════════════════════════════════════════
# _extract_doc_urls 测试
# ═══════════════════════════════════════════════════════════════

class TestExtractDocUrls:
    def test_empty_text(self):
        assert _extract_doc_urls("") == []

    def test_no_doc_links(self):
        text = "今天讨论了很多内容，但没有飞书文档链接"
        assert _extract_doc_urls(text) == []

    def test_single_docx_link(self):
        text = "参考这个文档 https://xxx.feishu.cn/docx/abc123def456 看看"
        urls = _extract_doc_urls(text)
        assert len(urls) == 1
        assert "abc123def456" in urls[0]

    def test_single_wiki_link(self):
        text = "见知识库 https://xxx.feishu.cn/wiki/WIKI789XYZ"
        urls = _extract_doc_urls(text)
        assert len(urls) == 1
        assert "WIKI789XYZ" in urls[0]

    def test_single_sheet_link(self):
        text = "数据看板 https://xxx.feishu.cn/sheet/SHEET001"
        urls = _extract_doc_urls(text)
        assert len(urls) == 1
        assert "SHEET001" in urls[0]

    def test_single_base_link(self):
        text = "多维表格 https://xxx.feishu.cn/base/BASE002"
        urls = _extract_doc_urls(text)
        assert len(urls) == 1
        assert "BASE002" in urls[0]

    def test_multiple_different_links(self):
        text = (
            "方案见 https://xxx.feishu.cn/docx/AAA，"
            "数据看板 https://xxx.feishu.cn/sheet/BBB"
        )
        urls = _extract_doc_urls(text)
        assert len(urls) == 2

    def test_duplicate_links_dedup(self):
        text = "文档 https://xxx.feishu.cn/docx/AAA 和同一个 https://xxx.feishu.cn/docx/AAA"
        urls = _extract_doc_urls(text)
        assert len(urls) == 1

    def test_non_feishu_links_ignored(self):
        text = "参考 https://example.com/doc 和 https://google.com"
        urls = _extract_doc_urls(text)
        assert urls == []

    def test_doubao_links_not_yet_supported(self):
        """doubao.com 链接不在当前支持范围内。"""
        text = "文档 https://xxx.doubao.com/docx/ABC123"
        urls = _extract_doc_urls(text)
        assert urls == []


# ═══════════════════════════════════════════════════════════════
# _FEISHU_DOC_URL_PATTERN 模式覆盖测试
# ═══════════════════════════════════════════════════════════════

class TestDocUrlPattern:
    @pytest.mark.parametrize("url,expected", [
        ("https://abc.feishu.cn/docx/ABC123def456", True),
        ("https://xxx.feishu.cn/wiki/WIKI001", True),
        ("https://feishu.cn/sheet/SHEET001", True),
        ("https://a.feishu.cn/base/BASE001", True),
        ("http://feishu.cn/docx/OLD001", True),   # http 也支持
        ("https://example.com/docx/ABC", False),   # 非 feishu
        ("https://feishu.cn/other/ABC", False),    # 非四类文档路径
        ("https://feishu.cn/docx", False),         # 无 token
        ("https://feishu.cn/docx/", False),        # 无 token
    ])
    def test_pattern_match(self, url, expected):
        match = _FEISHU_DOC_URL_PATTERN.search(url)
        if expected:
            assert match is not None, f"Expected {url} to match"
        else:
            assert match is None, f"Expected {url} NOT to match"


# ═══════════════════════════════════════════════════════════════
# _collect_topic_urls 测试
# ═══════════════════════════════════════════════════════════════

class TestCollectTopicUrls:
    def test_empty_topics(self):
        assert _collect_topic_urls([]) == {}

    def test_no_doc_links_in_messages(self):
        topic = _make_topic("t1", "话题1", [
            _make_msg("1", "c1", "张三", "今天天气不错"),
            _make_msg("2", "c1", "李四", "确实"),
        ])
        assert _collect_topic_urls([topic]) == {}

    def test_single_url_single_topic(self):
        topic = _make_topic("t1", "AI讨论", [
            _make_msg("1", "c1", "张三", "看文档 https://xxx.feishu.cn/docx/ABC"),
        ])
        result = _collect_topic_urls([topic])
        assert len(result) == 1
        url = list(result.keys())[0]
        assert "ABC" in url
        assert result[url] == ["AI讨论"]

    def test_multiple_topics_share_same_url(self):
        url = "https://xxx.feishu.cn/docx/SHARED"
        t1 = _make_topic("t1", "话题A", [
            _make_msg("1", "c1", "张三", f"看这个 {url}"),
        ])
        t2 = _make_topic("t2", "话题B", [
            _make_msg("2", "c1", "李四", f"我看了 {url} 写得不错"),
        ])
        result = _collect_topic_urls([t1, t2])
        assert len(result) == 1
        assert result[url] == ["话题A", "话题B"]

    def test_urls_across_messages_in_same_topic(self):
        t = _make_topic("t1", "多文档讨论", [
            _make_msg("1", "c1", "张三", "方案 https://xxx.feishu.cn/docx/AAA"),
            _make_msg("2", "c1", "李四", "数据 https://xxx.feishu.cn/sheet/BBB"),
        ])
        result = _collect_topic_urls([t])
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════
# DocExtractor.extract 测试
# ═══════════════════════════════════════════════════════════════

class TestDocExtractor:
    @pytest.fixture
    def tmp_source_dir(self, tmp_path):
        """创建临时 SOURCE 目录。"""
        source = tmp_path / "SOURCE"
        source.mkdir()
        return source

    def test_empty_topics_noop(self, tmp_source_dir):
        extractor = DocExtractor(tmp_source_dir)
        result = extractor.extract([])
        assert result == []

    def test_no_doc_links_noop(self, tmp_source_dir):
        topic = _make_topic("t1", "闲聊", [
            _make_msg("1", "c1", "张三", "你好"),
        ])
        result = DocExtractor(tmp_source_dir).extract([topic])
        assert result == []

    def test_dry_run_returns_placeholder(self, tmp_source_dir):
        """dry-run 应返回占位 ConvertedDoc 供预览。"""
        url = "https://xxx.feishu.cn/docx/ABC123"
        topic = _make_topic("t1", "方案讨论", [
            _make_msg("1", "c1", "张三", f"看文档 {url}"),
        ])
        result = DocExtractor(tmp_source_dir).extract([topic], dry_run=True)
        assert len(result) == 1
        assert result[0].original_url == url
        assert result[0].local_path == Path()  # dry-run 未实际转换
        assert result[0].title == ""

    def test_dry_run_multiple_topics_shared_url(self, tmp_source_dir):
        """dry-run 多话题共享同一 URL 时仍然只返回一条。"""
        url = "https://xxx.feishu.cn/docx/SHARED"
        t1 = _make_topic("t1", "话题A", [
            _make_msg("1", "c1", "张三", f"看 {url}"),
        ])
        t2 = _make_topic("t2", "话题B", [
            _make_msg("2", "c2", "李四", f"也看了 {url}"),
        ])
        result = DocExtractor(tmp_source_dir).extract([t1, t2], dry_run=True)
        assert len(result) == 1
        assert result[0].original_url == url

    def test_conversion_success(self, tmp_source_dir):
        """成功转换返回 ConvertedDoc。"""
        url = "https://xxx.feishu.cn/docx/ABC123"
        output_path = tmp_source_dir / "03-方案报告" / "20260730-测试文档.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# 测试文档", encoding="utf-8")

        topic = _make_topic("t1", "方案讨论", [
            _make_msg("1", "c1", "张三", f"看文档 {url}"),
        ])

        mock_converter = MagicMock()
        mock_converter.convert.return_value = {
            "status": "success",
            "url": url,
            "title": "测试文档",
            "output": str(output_path),
        }

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([topic])
            assert len(result) == 1
            assert result[0].original_url == url
            assert result[0].title == "测试文档"
            assert "03-方案报告" in result[0].relative_path

    def test_conversion_skipped_existing_file(self, tmp_source_dir):
        """跨次排重命中（status=skipped）时复用已有路径，从文件名推断标题。"""
        url = "https://xxx.feishu.cn/docx/EXISTING"
        output_path = tmp_source_dir / "08-参考资料" / "20260725-已有文档.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# 已有文档", encoding="utf-8")

        topic = _make_topic("t1", "引用已有文档", [
            _make_msg("1", "c1", "张三", f"参考 {url}"),
        ])

        mock_converter = MagicMock()
        mock_converter.convert.return_value = {
            "status": "skipped",
            "url": url,
            "reason": "⏭️ 已提取于 2026-07-27，使用 --force 覆盖",
            "output": str(output_path),
        }

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([topic])
            assert len(result) == 1
            assert result[0].original_url == url
            assert "08-参考资料" in result[0].relative_path
            # 标题应从文件名推断（剥离日期前缀）
            assert "已有文档" in result[0].title

    def test_conversion_failure_skipped(self, tmp_source_dir):
        """转换失败时跳过该文档，不影响其他文档。"""
        url_good = "https://xxx.feishu.cn/docx/GOOD"
        url_bad = "https://xxx.feishu.cn/docx/BAD"
        output_path = tmp_source_dir / "03-方案报告" / "20260730-正常文档.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# 正常", encoding="utf-8")

        t1 = _make_topic("t1", "话题A", [
            _make_msg("1", "c1", "张三", f"好文档 {url_good}"),
        ])
        t2 = _make_topic("t2", "话题B", [
            _make_msg("2", "c2", "李四", f"坏文档 {url_bad}"),
        ])

        mock_converter = MagicMock()
        # 第一个成功，第二个失败
        mock_converter.convert.side_effect = [
            {"status": "success", "url": url_good, "title": "正常文档",
             "output": str(output_path)},
            {"status": "error", "url": url_bad, "error": "权限不足"},
        ]

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([t1, t2])
            # 好的保留，坏的跳过
            assert len(result) == 1
            assert result[0].original_url == url_good

    def test_conversion_exception_caught(self, tmp_source_dir):
        """转换抛出异常时捕获并跳过。"""
        url = "https://xxx.feishu.cn/docx/CRASH"
        topic = _make_topic("t1", "崩溃文档", [
            _make_msg("1", "c1", "张三", f"看 {url}"),
        ])

        mock_converter = MagicMock()
        mock_converter.convert.side_effect = RuntimeError("网络超时")

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([topic])
            assert result == []  # 异常 → 跳过

    def test_output_path_outside_source(self, tmp_source_dir):
        """输出路径在 SOURCE 之外时，使用绝对路径作为 relative_path。"""
        url = "https://xxx.feishu.cn/docx/OUTSIDE"
        output_path = tmp_source_dir.parent / "somewhere" / "20260730-外部文档.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# 外部", encoding="utf-8")

        topic = _make_topic("t1", "外部文档话题", [
            _make_msg("1", "c1", "张三", f"链接 {url}"),
        ])

        mock_converter = MagicMock()
        mock_converter.convert.return_value = {
            "status": "success",
            "url": url,
            "title": "外部文档",
            "output": str(output_path),
        }

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([topic])
            assert len(result) == 1
            # relative_to 失败时用绝对路径
            assert result[0].relative_path == str(output_path)

    def test_skipped_without_output_path(self, tmp_source_dir):
        """status=skipped 但无 output 路径时，静默跳过。"""
        url = "https://xxx.feishu.cn/docx/NOPATH"
        topic = _make_topic("t1", "无路径文档", [
            _make_msg("1", "c1", "张三", f"链接 {url}"),
        ])

        mock_converter = MagicMock()
        mock_converter.convert.return_value = {
            "status": "skipped",
            "url": url,
            "reason": "⏭️ 已提取",
            "output": "",  # 空路径
        }

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([topic])
            assert result == []

    def test_doc_extract_max_enforced(self, tmp_source_dir):
        """max_docs 参数生效：超过上限时截断至前 N 个。"""
        urls = [f"https://xxx.feishu.cn/docx/DOC{i:03d}" for i in range(15)]
        msgs = [_make_msg(str(i), "c1", "张三", f"文档 {urls[i]}") for i in range(15)]
        topic = _make_topic("t1", "超多文档", msgs)

        # 无 mock converter → 实际会调 _get_converter()，所以需要 mock
        mock_converter = MagicMock()
        mock_converter.convert.return_value = {
            "status": "success",
            "title": "文档",
            "output": str(tmp_source_dir / "test.md"),
        }
        # 创建输出文件
        (tmp_source_dir / "test.md").write_text("# test", encoding="utf-8")

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([topic], max_docs=3)
            assert len(result) == 3  # max_docs=3 生效，只返回前 3 个

    def test_doc_extract_max_zero_no_limit(self, tmp_source_dir):
        """max_docs=0 表示不限制数量。"""
        urls = [f"https://xxx.feishu.cn/docx/DOC{i:03d}" for i in range(5)]
        msgs = [_make_msg(str(i), "c1", "张三", f"文档 {urls[i]}") for i in range(5)]
        topic = _make_topic("t1", "多文档", msgs)

        mock_converter = MagicMock()
        mock_converter.convert.return_value = {
            "status": "success",
            "title": "文档",
            "output": str(tmp_source_dir / "test.md"),
        }
        (tmp_source_dir / "test.md").write_text("# test", encoding="utf-8")

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([topic], max_docs=0)
            assert len(result) == 5  # max_docs=0 → 不限制

    def test_mixed_success_and_skipped(self, tmp_source_dir):
        """同一批次中混合成功和排重。"""
        url_new = "https://xxx.feishu.cn/docx/NEW"
        url_old = "https://xxx.feishu.cn/docx/OLD"
        output_new = tmp_source_dir / "03-方案报告" / "20260730-新文档.md"
        output_old = tmp_source_dir / "08-参考资料" / "20260725-旧文档.md"
        output_new.parent.mkdir(parents=True, exist_ok=True)
        output_old.parent.mkdir(parents=True, exist_ok=True)
        output_new.write_text("# 新", encoding="utf-8")
        output_old.write_text("# 旧", encoding="utf-8")

        t1 = _make_topic("t1", "新话题", [
            _make_msg("1", "c1", "张三", f"新文档 {url_new}"),
        ])
        t2 = _make_topic("t2", "旧话题", [
            _make_msg("2", "c1", "李四", f"旧文档 {url_old}"),
        ])

        mock_converter = MagicMock()
        side_effect_1 = {"status": "success", "url": url_new, "title": "NewDoc", "output": str(output_new)}
        side_effect_2 = {"status": "skipped", "url": url_old, "reason": "Skipped: exists", "output": str(output_old)}
        mock_converter.convert.side_effect = [side_effect_1, side_effect_2]

        with patch.object(DocExtractor, '_get_converter', return_value=mock_converter):
            result = DocExtractor(tmp_source_dir).extract([t1, t2])
            assert len(result) == 2
            assert result[0].title == "NewDoc"
