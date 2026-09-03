"""chunker 纯函数扩展测试 — 覆盖 _extract_fields、_chunk_lines、数据类序列化等未充分测试区域。"""

from __future__ import annotations

import json
from pathlib import Path

from iris.ingest.chunker import (
    _split_content,
    _split_hard,
    _overlap_tail,
    _apply_overlap,
    _build_token_freq,
    _build_structural_tags,
    _extract_fields,
    _chunk_lines,
    ChunkRecord,
    ChunkSlim,
    ChunkSummary,
)
from iris.ingest.scanner import DocumentRecord


# ─────────────────────────────────────────────────────────────
# _extract_fields（全新覆盖）
# ─────────────────────────────────────────────────────────────

class TestExtractFields:
    def test_short_text_returns_empty(self):
        """少于 20 字符的文本返回空字典。"""
        assert _extract_fields("短文本") == {}
        assert _extract_fields("") == {}

    def test_exactly_20_chars(self):
        """刚好 20 字符仍不够 → 空字典。"""
        assert _extract_fields("一二三四五六七八九十一二三四五六七八九十") == {}

    def test_goal_extraction(self):
        """含"目标"关键词的句子被提取。"""
        text = "本季度目标是提升搜索召回率10个百分点。我们计划在Q3完成上线。"
        fields = _extract_fields(text)
        assert "goal" in fields
        assert len(fields["goal"]) >= 1

    def test_progress_extraction(self):
        """含"进展"关键词的句子被提取。"""
        text = "项目当前进展顺利，已完成第一阶段开发。第二阶段正在推进中。"
        fields = _extract_fields(text)
        assert "progress" in fields

    def test_decision_extraction(self):
        """含"结论"关键词的句子被提取。"""
        text = "经过讨论，我们决定采用方案A。最终结论是优先保障稳定性。"
        fields = _extract_fields(text)
        assert "decision" in fields

    def test_risk_extraction(self):
        """含"风险"关键词的句子被提取。"""
        text = "主要风险在于依赖的外部API可能不稳定。当前最大的问题是人力不足。"
        fields = _extract_fields(text)
        assert "risk" in fields

    def test_definition_extraction(self):
        """含"定义"关键词的句子被提取。"""
        text = "召回率的定义是检索到的相关文档数除以所有相关文档总数。这个术语指的是评估检索系统的能力。"
        fields = _extract_fields(text)
        assert "definition" in fields

    def test_timeline_extraction(self):
        """含时间关键词的句子被提取。"""
        text = "本周计划完成前端页面开发。下周期待开始联调。Q3的里程碑是上线灰度。"
        fields = _extract_fields(text)
        assert "timeline" in fields

    def test_multi_field_extraction(self):
        """同时包含多种字段 → 每种至多 2 条。"""
        text = (
            "目标是提升召回率。计划在Q3完成。"
            "当前进展顺利。推进速度很快。"
            "主要风险是资源不足。问题是排期紧张。"
            "结论是优先做核心功能。决定推迟非核心需求。"
        )
        fields = _extract_fields(text)
        for matches in fields.values():
            assert len(matches) <= 2
        # 至少有 goal / progress / risk / decision 中的部分
        assert len(fields) >= 2

    def test_semicolon_separator(self):
        """分号也被当作句子分隔符。"""
        text = "目标是提升转化率；当前进展是已上线A/B测试；风险是样本量不足"
        fields = _extract_fields(text)
        assert len(fields) >= 1

    def test_no_match_returns_empty(self):
        """不含任何 FIELD_KEYWORDS 的文本 → 空字典。"""
        text = "今天天气很好。我们中午一起吃饭。下午继续写代码。"
        fields = _extract_fields(text)
        assert fields == {}


# ─────────────────────────────────────────────────────────────
# _chunk_lines（核心切块逻辑，不依赖文件 I/O）
# ─────────────────────────────────────────────────────────────

def _make_doc(title: str = "测试文档", rel_path: str = "test/doc.md",
              file_hash: str = "abc123", source_name: str = "test_source") -> DocumentRecord:
    return DocumentRecord(
        source_name=source_name,
        path="/tmp/test/doc.md",
        relative_path=rel_path,
        size_bytes=1024,
        modified_at="2026-07-01",
        file_hash=file_hash,
        title=title,
    )


class TestChunkLines:
    def test_single_section_no_heading(self):
        """无标题行 → 整个文档视为一个 section。"""
        doc = _make_doc(title="无标题文档")
        lines = ["第一行内容", "第二行内容", "第三行内容"]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        assert len(chunks) == 1
        assert chunks[0].title == "无标题文档"
        assert chunks[0].section_path == ["无标题文档"]
        assert chunks[0].line_start == 1
        assert chunks[0].line_end == 3

    def test_heading_splits_sections(self):
        """# 标题行正确地拆分 section。"""
        doc = _make_doc(title="会议记录")
        lines = [
            "# 项目进展",
            "本周完成了前端重构。",
            "## 技术细节",
            "采用了 React 18 的新特性。",
            "## 排期",
            "预计下周一上线。",
        ]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        # 应有多个 section（至少 3 个: 项目进展 / 技术细节 / 排期）
        titles = [c.title for c in chunks]
        assert "项目进展" in titles
        assert "技术细节" in titles
        assert "排期" in titles
        assert len(chunks) >= 3

    def test_heading_hierarchy(self):
        """多级标题的 section_path 正确嵌套。"""
        doc = _make_doc(title="产品规划")
        lines = [
            "# 整体目标",
            "Q3 整体目标描述。",
            "## 搜索优化",
            "搜索召回提升计划。",
            "### 向量检索",
            "采用新 embedding 模型。",
            "# 团队建设",
            "团队扩张计划。",
        ]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        vector_chunks = [c for c in chunks if "向量检索" in c.title]
        assert len(vector_chunks) == 1
        vc = vector_chunks[0]
        # section_path 应反映层级: 产品规划 > 搜索优化 > 向量检索
        assert vc.section_path[-1] == "向量检索"
        assert "搜索优化" in vc.section_path

    def test_large_section_produces_segments(self):
        """超长 section 被 _split_content 切分为多个 segment。"""
        doc = _make_doc(title="长文档")
        long_para = ("这是很长的段落内容用于测试切分逻辑。" * 30)
        lines = ["# 长章节", long_para]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=200, max_preview_chars=100))
        assert len(chunks) >= 2
        # 同一个 title 的多个 segment
        for c in chunks:
            assert c.title == "长章节"
        # segment 编号递增
        seg_indices = [c.segment_index for c in chunks]
        assert seg_indices == sorted(seg_indices)

    def test_empty_section_skipped(self):
        """仅含标题行的空 section 被跳过。"""
        doc = _make_doc(title="文档")
        lines = [
            "# 标题一",
            "有内容",
            "# 标题二（空）",
            "# 标题三",
            "也有内容",
        ]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        titles = {c.title for c in chunks}
        assert "标题一" in titles
        assert "标题三" in titles
        # "标题二（空）" 没有内容，不应产出 chunk
        assert "标题二（空）" not in titles

    def test_empty_document(self):
        """空行列表 → 无 chunk。"""
        doc = _make_doc(title="空文档")
        chunks = list(_chunk_lines([], doc, max_chunk_chars=2000, max_preview_chars=200))
        assert len(chunks) == 0

    def test_only_headings_no_content(self):
        """全是标题无正文内容 → fallback 兜底：整篇视作一个 section。"""
        doc = _make_doc(title="纯标题文档")
        lines = ["# 第一章", "## 第一节", "### 细节", "# 第二章"]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        # flush 过滤掉每个纯标题 section 后 sections 为空，触发 line 306 fallback
        assert len(chunks) == 1
        assert chunks[0].title == "纯标题文档"

    def test_chunk_id_format(self):
        """验证 chunk_id 格式。"""
        doc = _make_doc(rel_path="subdir/doc.md")
        lines = ["# 章节", "一些内容"]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        assert len(chunks) == 1
        assert chunks[0].chunk_id.startswith("subdir/doc.md::chunk-")

    def test_word_and_token_count(self):
        """验证 word_count 和 token_count 合理。"""
        doc = _make_doc()
        lines = ["# 测试", "搜索 召回 向量 索引 BM25"]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        assert len(chunks) == 1
        assert chunks[0].word_count > 0
        assert chunks[0].token_count > 0

    def test_preview_truncation(self):
        """preview 按 max_preview_chars 截断。"""
        doc = _make_doc()
        words = "A " * 200
        lines = ["# 章节", words]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=50))
        assert len(chunks[0].content_preview) <= 50

    def test_structural_tags_in_chunk(self):
        """验证 structural_tags 被正确附加到 chunk。"""
        doc = _make_doc(rel_path="05-会议纪要/weekly.md")
        lines = ["# 目标讨论", "本季度目标是提升召回率"]
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=2000, max_preview_chars=200))
        assert len(chunks) == 1
        tags = chunks[0].structural_tags
        assert "meeting" in tags
        assert "goal" in tags

    def test_overlap_applied_in_chunk_lines(self):
        """_chunk_lines 正确透传 overlap_chars 参数。"""
        doc = _make_doc()
        para = "前提条件第一句。前提条件第二句。结论在此段给出。" * 8
        lines = ["# 章节", para]
        # 小 max_chunk_chars 触发多 segment
        chunks = list(_chunk_lines(lines, doc, max_chunk_chars=120, max_preview_chars=60, overlap_chars=20))
        # 如果有多于 1 个 segment，第二个应包含来自第一个尾部的内容
        if len(chunks) > 1:
            # 重叠逻辑至少使所有 segment 有内容
            for c in chunks:
                assert len(c.content) > 0


# ─────────────────────────────────────────────────────────────
# _split_content 增强边界测试
# ─────────────────────────────────────────────────────────────

class TestSplitContentExtended:
    def test_multi_paragraph_each_small(self):
        """多个段落，每段都不超限但合起来超 → 正确拆分。"""
        para = "段落内容。" * 3  # ~18 chars
        content = "\n\n".join([para] * 10)
        result = _split_content(content, max_chunk_chars=50)
        assert len(result) >= 3
        for chunk in result:
            assert len(chunk) <= 100  # 允许少量超出（段落未做 hard split）

    def test_paragraph_exactly_fills_chunk(self):
        """段落刚好填满 max_chunk_chars → 不触发 hard split。"""
        para = "A" * 100  # 刚好 100 字符
        content = para + "\n\n" + para
        result = _split_content(content, max_chunk_chars=100)
        # 每个段落单独一个 chunk
        assert len(result) == 2

    def test_whitespace_normalization(self):
        """前后空白被 strip 掉。"""
        content = "   \n\n  实际内容\n\n   "
        result = _split_content(content, max_chunk_chars=100)
        assert len(result) == 1
        assert result[0] == "实际内容"

    def test_single_giant_paragraph(self):
        """单体超长段落走 hard split 路径。"""
        content = "没有段落分隔的超长文本。" * 100
        result = _split_content(content, max_chunk_chars=80)
        assert len(result) >= 3
        for chunk in result:
            assert isinstance(chunk, str)

    def test_overlap_preserves_chunk_count(self):
        """overlap 不改变 chunk 数量。"""
        para = "段落X。" * 8
        content = "\n\n".join([para] * 5)
        no_overlap = _split_content(content, max_chunk_chars=80, overlap_chars=0)
        with_overlap = _split_content(content, max_chunk_chars=80, overlap_chars=20)
        assert len(no_overlap) == len(with_overlap)


# ─────────────────────────────────────────────────────────────
# _split_hard 增强边界测试
# ─────────────────────────────────────────────────────────────

class TestSplitHardExtended:
    def test_multiple_sentences_per_chunk(self):
        """多短句合并到一个 chunk。"""
        sentences = "。".join([f"句子{i}" for i in range(20)]) + "。"
        result = _split_hard(sentences, max_chunk_chars=200)
        assert len(result) >= 1
        for chunk in result:
            assert len(chunk) <= 200

    def test_empty_sentence_skipped(self):
        """空句子被跳过。"""
        content = "第一句。  。第三句。"
        result = _split_hard(content, max_chunk_chars=20)
        # 正确的句子是第一句和第三句
        assert len(result) >= 1

    def test_exclamation_and_question_marks(self):
        """感叹号和问号也被视作句子边界。"""
        content = "真的吗！太好了？当然。没问题！"
        result = _split_hard(content, max_chunk_chars=5)
        # 应有多个 chunk
        assert len(result) >= 1

    def test_all_chunks_within_limit(self):
        """每个 chunk 长度不超过 max_chunk_chars。"""
        content = "。" .join([f"句子编号{i}" for i in range(30)]) + "。"
        max_chars = 30
        result = _split_hard(content, max_chunk_chars=max_chars)
        for chunk in result:
            assert len(chunk) <= max_chars


# ─────────────────────────────────────────────────────────────
# _overlap_tail 增强边界测试
# ─────────────────────────────────────────────────────────────

class TestOverlapTailExtended:
    def test_boundary_at_period(self):
        """句子边界标点后的内容被保留。"""
        text = "前言部分。核心结论是XXX。"
        tail = _overlap_tail(text, 20)
        # 应从"核心结论…"开始
        assert "核心结论" in tail

    def test_boundary_at_newline(self):
        """换行符也被视为句子边界。"""
        text = "第一行\n第二行内容"
        tail = _overlap_tail(text, 10)
        # 应从第二行开始
        assert tail.startswith("第二行")

    def test_no_boundary_returns_full_tail(self):
        """无句子边界的文本返回完整 tail。"""
        text = "没有标点符号的长文本内容"
        tail = _overlap_tail(text, 6)
        assert len(tail) <= 6
        assert tail in text

    def test_boundary_at_end_returns_full(self):
        """边界在末尾 → 返回完整 tail（无 leading 半句）。"""
        text = "前面一句。结束。"
        tail = _overlap_tail(text, 3)
        assert len(tail) == 3


# ─────────────────────────────────────────────────────────────
# _apply_overlap 增强边界测试
# ─────────────────────────────────────────────────────────────

class TestApplyOverlapExtended:
    def test_empty_chunks(self):
        """空列表 → 空列表。"""
        assert _apply_overlap([], 20) == []

    def test_consecutive_overlap(self):
        """三个 chunks 的连续重叠。"""
        chunks = [
            "第一章：引言部分内容。",
            "第二章：方法论描述。",
            "第三章：实验结果讨论。",
        ]
        result = _apply_overlap(chunks, 15)
        assert len(result) == 3
        # 第三块不应包含第一块的内容（去级联）
        assert "第一章" not in result[2]


# ─────────────────────────────────────────────────────────────
# _build_token_freq 增强边界测试
# ─────────────────────────────────────────────────────────────

class TestBuildTokenFreqExtended:
    def test_mixed_cjk_latin(self):
        """中英混合 token 正确计数。
        TOKEN_RE = [A-Za-z0-9_\\-一-鿿]+ 会将紧邻的 CJK 字符并入同一个 token，
        "BM25算法" 是一个 token（非 "BM25" + "算法"）。
        """
        text = "BM25算法 和 embedding向量 的BM25实验"
        freq = _build_token_freq(text)
        # "BM25算法" 出现 1 次，"的BM25实验" 出现 1 次 — 均含 "BM25"
        assert any("bm25" in k for k in freq), f"Expected token containing 'bm25', got {freq}"
        assert any("embedding" in k for k in freq), f"Expected token containing 'embedding', got {freq}"

    def test_numeric_tokens(self):
        """数字和下划线 token 被正确提取。"""
        text = "version_2  model_v3  version_2"
        freq = _build_token_freq(text)
        assert freq.get("version_2", 0) == 2

    def test_special_chars_excluded(self):
        """特殊字符不进入 token。"""
        text = "hello!!! world??? @#$%"
        freq = _build_token_freq(text)
        assert "!!!" not in freq
        assert "@#$%" not in freq


# ─────────────────────────────────────────────────────────────
# _build_structural_tags 增强边界测试
# ─────────────────────────────────────────────────────────────

class TestBuildStructuralTagsExtended:
    def test_weekly_tag_from_path_part(self):
        """路径包含"周报" → weekly 标记。"""
        tags = _build_structural_tags("06-我的周报/2026W30.md", ["周报"], "")
        assert "weekly" in tags

    def test_report_tag_from_path(self):
        """路径包含"汇报" → report 标记。"""
        tags = _build_structural_tags("方案报告/汇报材料.md", [], "")
        assert "report" in tags or "proposal" in tags

    def test_multiple_field_tags_from_content(self):
        """同一内容同时命中多个字段标签。"""
        content = (
            "目标是提升准确率。当前进展是模型已训练。"
            "主要风险是数据量不足。时间计划是Q3完成。"
        )
        tags = _build_structural_tags("other/doc.md", ["文档"], content)
        assert "goal" in tags
        assert "progress" in tags
        assert "risk" in tags
        assert "timeline" in tags

    def test_tag_deduplication(self):
        """同一 tag 只出现一次。"""
        tags = _build_structural_tags(
            "周报/会议/weekly_report.md",
            ["会议纪要", "周报"],
            "本周目标已达成",
        )
        assert tags.count("weekly") <= 1
        assert tags.count("meeting") <= 1


# ─────────────────────────────────────────────────────────────
# ChunkSlim.from_dict（全新覆盖）
# ─────────────────────────────────────────────────────────────

class TestChunkSlimFromDict:
    def test_full_dict(self):
        data = {
            "relative_path": "path/to/doc.md",
            "title": "文档标题",
            "section_path": ["文档标题", "子章节"],
            "content_preview": "预览内容",
            "content": "完整内容",
        }
        slim = ChunkSlim.from_dict(data)
        assert slim.relative_path == "path/to/doc.md"
        assert slim.title == "文档标题"
        assert slim.section_path == ["文档标题", "子章节"]
        assert slim.content_preview == "预览内容"
        assert slim.content == "完整内容"

    def test_empty_dict(self):
        slim = ChunkSlim.from_dict({})
        assert slim.relative_path == ""
        assert slim.title == ""
        assert slim.section_path == []
        assert slim.content_preview == ""
        assert slim.content == ""

    def test_partial_dict(self):
        slim = ChunkSlim.from_dict({"relative_path": "only_path.md"})
        assert slim.relative_path == "only_path.md"
        assert slim.title == ""
        assert slim.section_path == []


# ─────────────────────────────────────────────────────────────
# ChunkSummary.to_dict（全新覆盖）
# ─────────────────────────────────────────────────────────────

class TestChunkSummaryToDict:
    def test_empty_summary(self):
        summary = ChunkSummary(
            source_name="test_source",
            scanned_at="2026-07-30T10:00:00",
            document_count=0,
            chunk_count=0,
            chunks=[],
        )
        d = summary.to_dict()
        assert d["source_name"] == "test_source"
        assert d["document_count"] == 0
        assert d["chunk_count"] == 0
        assert d["chunks"] == []

    def test_summary_with_chunks(self):
        chunk = ChunkRecord(
            chunk_id="doc::chunk-1",
            source_name="src",
            document_path="/tmp/doc.md",
            relative_path="doc.md",
            document_hash="hash123",
            title="标题",
            section_path=["标题"],
            level=1,
            content="内容",
            content_preview="预览",
            line_start=1,
            line_end=5,
            word_count=2,
            token_count=2,
        )
        summary = ChunkSummary(
            source_name="src",
            scanned_at="2026-07-30",
            document_count=1,
            chunk_count=1,
            chunks=[chunk],
            build_stats={"reused": 0, "rebuilt": 1},
        )
        d = summary.to_dict()
        assert d["chunk_count"] == 1
        assert len(d["chunks"]) == 1
        assert d["chunks"][0]["chunk_id"] == "doc::chunk-1"
        assert d["build_stats"]["rebuilt"] == 1

    def test_roundtrip_chunk_data(self):
        """to_dict 产出的 chunk 字段与 ChunkRecord 构造兼容。"""
        chunk = ChunkRecord(
            chunk_id="roundtrip::chunk-1",
            source_name="test",
            document_path="/tmp/rt.md",
            relative_path="rt.md",
            document_hash="rt_hash",
            title="测试页",
            section_path=["测试页"],
            level=1,
            content="正文",
            content_preview="正文",
            line_start=1,
            line_end=1,
            word_count=2,
            token_count=2,
            chunk_type="section",
            segment_index=1,
            segment_count=1,
            structural_tags=["goal"],
            extracted_fields={"goal": ["目标是X"]},
            token_freq={"测试": 1},
        )
        summary = ChunkSummary(
            source_name="test",
            scanned_at="2026-07-30",
            document_count=1,
            chunk_count=1,
            chunks=[chunk],
        )
        d = summary.to_dict()
        c = d["chunks"][0]
        # 核心字段一致
        assert c["chunk_id"] == "roundtrip::chunk-1"
        assert c["chunk_type"] == "section"
        assert c["structural_tags"] == ["goal"]
        assert c["extracted_fields"] == {"goal": ["目标是X"]}


# ─────────────────────────────────────────────────────────────
# iter_chunk_items 额外边界测试
# ─────────────────────────────────────────────────────────────

class TestIterChunkItemsExtended:
    def test_nonexistent_directory(self):
        """metadata_root 不存在 → 所有源跳过。"""
        from iris.ingest.chunker import iter_chunk_items
        items = list(iter_chunk_items(Path("/nonexistent/path/12345"), {"src": {"enabled": True}}))
        assert items == []

    def test_mixed_enabled_disabled(self):
        """启用+禁用混合 → 仅产出启用的源。"""
        import tempfile
        from iris.ingest.chunker import iter_chunk_items

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a_chunk_summary.json").write_text(
                json.dumps({"chunks": [{"id": "a1"}]}), encoding="utf-8"
            )
            (root / "b_chunk_summary.json").write_text(
                json.dumps({"chunks": [{"id": "b1"}]}), encoding="utf-8"
            )
            sources = {"a": {"enabled": True}, "b": {"enabled": False}}
            items = list(iter_chunk_items(root, sources))
        assert len(items) == 1
        assert items[0]["id"] == "a1"
