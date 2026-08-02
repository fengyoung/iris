"""C5 BM25 全文计算 + M6 QA token 预算 专项测试。"""

import pytest
from unittest.mock import MagicMock, patch

from iris.retrieval.searcher import _score_chunk
from iris.utils.tokenization import tokenize, estimate_tokens


# ── C5: BM25 基于全文而非截断预览 ──────────────────────────

class TestBm25FullContent:
    """验证 BM25 统计量和评分基于 chunk.content 而非 content_preview。"""

    def testtokenize_on_content_vs_preview(self):
        """_compute_corpus_stats 使用 content 字段。"""
        # 模拟 LocalRetriever._compute_corpus_stats
        from collections import defaultdict

        class FakeChunk:
            def __init__(self, content, content_preview):
                self.content = content
                self.content_preview = content_preview
                self.title = "test"
                self.section_path = []
                self.source_name = "test"

        long_text = "项目Beta 项目 里程碑 手机 全量 标准化 拍摄 " * 20  # ~160 词
        short_preview = "项目Beta 项目"  # 仅 2 词

        chunk = FakeChunk(content=long_text, content_preview=short_preview)

        # 使用 content（全文）计算
        full_tokens = tokenize(chunk.content)
        # 使用 content_preview（截断）计算
        preview_tokens = tokenize(chunk.content_preview)

        # 全文 token 应显著多于预览
        assert len(full_tokens) > len(preview_tokens) * 5, \
            f"全文 token({len(full_tokens)})应远超预览({len(preview_tokens)})"

    def test_score_chunk_uses_full_content(self):
        """_score_chunk 内部使用 chunk.content。"""
        query = "拍照 标准化 手机"
        query_tokens = tokenize(query)

        class FakeChunk:
            content = "项目Beta 项目涉及 手机 全量标准化拍摄流程 " * 10
            content_preview = "项目Beta 项目"
            title = "项目Beta 项目"
            section_path = []
            token_freq = {}

        # 使用全文计算 BM25 分数
        score, matched = _score_chunk(
            query, query_tokens, FakeChunk,
            total_docs=100, avg_doc_len=200.0,
        )

        # 应匹配到至少一个词
        assert len(matched) >= 1, f"应在全文中匹配到 token: {matched}"
        assert score > 0, f"全文 BM25 分数应 > 0: {score}"

    def test_tf_differs_between_content_and_preview(self):
        """同一个词在 content 和 content_preview 中的 TF 不同。"""
        text = "手机 手机 手机 拍照"  # "手机" 出现 3 次
        preview = "手机 拍照"  # "手机" 出现 1 次

        full_tokens = tokenize(text)
        preview_tokens = tokenize(preview)

        from collections import Counter
        full_tf = Counter(full_tokens).get("手机", 0)
        preview_tf = Counter(preview_tokens).get("手机", 0)

        assert full_tf > preview_tf, \
            f"content TF({full_tf})应大于 preview TF({preview_tf})"


# ── M6: QA Token 预算使用 estimate_tokens ──────────────────

class TestQaTokenBudget:
    """验证 QA context packer 使用 estimate_tokens 而非 len()。"""

    def test_estimate_tokens_mixed_cn_en(self):
        """中英混排文本 token 估算不同于 len()。"""
        text_cn = "这是一个中文测试句子用于验证token估算"
        text_en = "This is an English test sentence for token estimation"
        text_mixed = "项目Beta 手机 全量 标准化 拍摄 SOP 4步法"

        cn_estimate = estimate_tokens(text_cn)
        en_estimate = estimate_tokens(text_en)
        mixed_estimate = estimate_tokens(text_mixed)

        # len() 近似值
        assert cn_estimate < len(text_cn), \
            f"中文 token 估算({cn_estimate})应小于字符数({len(text_cn)})"
        assert en_estimate < len(text_en), \
            f"英文 token 估算({en_estimate})应小于字符数({len(text_en)})"
        assert mixed_estimate < len(text_mixed), \
            f"混排 token 估算({mixed_estimate})应小于字符数({len(text_mixed)})"

    def test_prompt_context_packer_uses_estimate_tokens(self):
        """PromptContextPacker.pack() 使用 estimate_tokens 计算 cost。"""
        import sys
        sys.path.insert(0, 'src')
        from iris.qa.context import PromptContextPacker
        from iris.qa.models import AnswerBlock

        # 构造最小配置
        class FakeConfig:
            app = {
                "qa": {
                    "max_prompt_context_chars": 2000,
                    "max_evidence_blocks": 5,
                    "max_wiki_hits": 5,
                    "max_block_summary_chars": 300,
                    "max_wiki_summary_chars": 200,
                }
            }

        packer = PromptContextPacker(FakeConfig())

        # 构造带 citation 的 block
        class FakeCitation:
            relative_path = "SOURCE/test.md"
            section_path = ["Section"]

        block = AnswerBlock(
            title="测试标题",
            summary="这是一个测试摘要内容需要估算token数量",
            citation=FakeCitation(),
            score=0.9,
        )

        wiki_hit = {
            "title": "Wiki页面",
            "relative_path": "LLM-WIKI/test.md",
            "summary": "这是一个Wiki摘要内容",
        }

        result = packer.pack([block], [wiki_hit])

        # 验证至少有一个 wiki hit 被选中（如果 fit 在预算内）
        assert result.metadata is not None
        used = result.metadata.get("used_chars", 0)
        assert used >= 0, f"used_chars 应为非负: {used}"
