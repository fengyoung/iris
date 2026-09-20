"""敏感守卫接入点 — 单元测试。

覆盖各下游消费者对 ``iris.wiki._sensitive`` 的接入：
候选发现、Wiki 生成与增量更新、检索器、ASR 热词与替换词典。

背景：2026-09-20 发现调薪/人员盘点内容已渗入 Wiki 人物页正文（含真实薪资数字）
与 ASR 现网热词。这些测试钉住「不再复发」。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from iris.core.exceptions import IrisValueError, SensitiveDocumentError
from iris.wiki.discovery import CandidateDiscovery
from iris.wiki.discovery_types import CandidateItem
from iris.wiki.discovery_utils import drop_sensitive_candidates
from iris.wiki.generator import WikiGenerator, WikiPageDraft


SENSITIVE_PATH = "02-部门管理/2026/20260806-数据智能部2026最终调薪方案.md"
CLEAN_PATH = "03-方案报告/2026/20260129-首页推荐feeds系统性盘点.md"


def _chunk_dict(relative_path: str, title: str = "标题") -> dict:
    return {
        "chunk_id": f"{relative_path}::chunk-1",
        "source_name": "work_docs_main",
        "document_path": f"/src/{relative_path}",
        "relative_path": relative_path,
        "document_hash": "hash",
        "title": title,
        "section_path": [title],
        "level": 0,
        "content": "内容",
        "content_preview": "内容",
        "line_start": 1,
        "line_end": 2,
        "word_count": 2,
        "token_count": 2,
        "chunk_type": "section",
        "segment_index": 0,
        "segment_count": 1,
    }


# ── 候选发现 ───────────────────────────────────────────────


class TestDiscoveryGuard:
    def test_load_chunks_drops_sensitive_documents(self, tmp_path):
        discovery = object.__new__(CandidateDiscovery)
        discovery._metadata_root = tmp_path
        discovery._config = MagicMock()
        discovery._config.data_source.get.return_value = {}

        items = [_chunk_dict(SENSITIVE_PATH), _chunk_dict(CLEAN_PATH)]
        with patch("iris.ingest.iter_chunk_items", return_value=items):
            chunks = discovery._load_chunks()

        assert [c.relative_path for c in chunks] == [CLEAN_PATH]

    def test_load_chunks_keeps_all_when_none_sensitive(self, tmp_path):
        discovery = object.__new__(CandidateDiscovery)
        discovery._metadata_root = tmp_path
        discovery._config = MagicMock()
        discovery._config.data_source.get.return_value = {}

        items = [_chunk_dict(CLEAN_PATH), _chunk_dict("docs/正常文档.md")]
        with patch("iris.ingest.iter_chunk_items", return_value=items):
            chunks = discovery._load_chunks()

        assert len(chunks) == 2

    def test_load_chunks_tolerates_malformed_entries(self, tmp_path):
        """畸形条目不应因守卫而抛异常。

        ChunkSlim.from_dict 对缺字段的 dict 不抛错、产出空对象（既有行为），
        守卫对空 relative_path 必须返回 False 而非崩溃。
        """
        discovery = object.__new__(CandidateDiscovery)
        discovery._metadata_root = tmp_path
        discovery._config = MagicMock()
        discovery._config.data_source.get.return_value = {}

        with patch("iris.ingest.iter_chunk_items", return_value=[{"bad": "entry"}]):
            chunks = discovery._load_chunks()

        assert len(chunks) == 1
        assert chunks[0].relative_path == ""


class TestDropSensitiveCandidates:
    def _cand(self, title: str) -> CandidateItem:
        return CandidateItem(title=title, page_type="domain", query=title,
                             score=10, evidence_count=5, sample_paths=["a.md"])

    def test_drops_sensitive_title(self):
        kept = drop_sensitive_candidates([
            self._cand("数据智能部 2026 调薪方案"),
            self._cand("质检大脑质检池页面标准类型筛选 PRD"),
        ])
        assert [c.title for c in kept] == ["质检大脑质检池页面标准类型筛选 PRD"]

    def test_keeps_normal_titles(self):
        items = [self._cand("首页推荐feeds系统性盘点"), self._cand("拍照3.0外观定级")]
        assert drop_sensitive_candidates(items) == items


# ── Wiki 生成 ──────────────────────────────────────────────


class TestBuildPageGuard:
    def test_sensitive_title_raises_before_any_work(self):
        gen = object.__new__(WikiGenerator)  # 不调 __init__：守卫在检索之前
        with pytest.raises(SensitiveDocumentError) as exc:
            gen.build_page(query="调薪", page_type="domain", title="数据智能部 2026 调薪方案")
        assert isinstance(exc.value, IrisValueError)

    def test_clean_title_passes_guard(self):
        """守卫不应拦截正常标题——放行后才会因缺 _wiki_root 失败。"""
        gen = object.__new__(WikiGenerator)
        with pytest.raises(AttributeError):
            gen.build_page(query="搜推", page_type="domain", title="搜索推荐优化")


class TestBuildPagesBatchSemantics:
    """批量生成必须逐项降级：一条敏感项不能让整批中断。"""

    def _gen(self, drafts: dict):
        gen = object.__new__(WikiGenerator)

        def _fake_build_page(*, query, page_type, title, top_k=5):
            if title in drafts:
                return drafts[title]
            raise SensitiveDocumentError(f"标题命中敏感策略: {title!r}")

        gen.build_page = _fake_build_page
        return gen

    def test_sensitive_item_refused_others_still_built(self):
        draft = WikiPageDraft(page_type="domain", title="正常页面", slug="正常页面",
                              output_path="/tmp/x.md", markdown="# 正常")
        gen = self._gen({"正常页面": draft})

        items = [
            MagicMock(query="q1", page_type="domain", title="调薪方案"),
            MagicMock(query="q2", page_type="domain", title="正常页面"),
        ]
        result = gen.build_pages(items, write=False)

        assert len(result.items) == 2
        assert result.items[0]["status"] == "refused_sensitive"
        assert result.items[1]["markdown"] == "# 正常"

    def test_all_sensitive_still_returns_all_items(self):
        gen = self._gen({})
        items = [MagicMock(query="q", page_type="domain", title=f"调薪方案{i}") for i in range(3)]
        result = gen.build_pages(items, write=False)
        assert len(result.items) == 3
        assert all(r["status"] == "refused_sensitive" for r in result.items)


class TestUpdatePageGuard:
    def test_sensitive_title_returns_refused_status(self):
        gen = object.__new__(WikiGenerator)
        result = gen._update_page_with_content(
            title="数据智能部 2026 调薪方案", page_type="domain",
            path=Path("/tmp/x.md"), existing_content="", top_k=5,
        )
        assert result["status"] == "refused_sensitive"
        assert result["title"] == "数据智能部 2026 调薪方案"


# ── 检索器 ─────────────────────────────────────────────────


class TestRetrieverGuard:
    def _retriever(self, chunks):
        from iris.retrieval.searcher import LocalRetriever

        r = object.__new__(LocalRetriever)
        r._chunks = list(chunks)
        r._by_source = {"main": list(chunks)}
        return r

    def test_drops_sensitive_chunks(self):
        clean = MagicMock(relative_path=CLEAN_PATH)
        dirty = MagicMock(relative_path=SENSITIVE_PATH)
        r = self._retriever([dirty, clean])

        r._drop_sensitive_chunks()

        assert r._chunks == [clean]
        assert r._by_source["main"] == [clean]

    def test_noop_when_nothing_sensitive(self):
        clean = MagicMock(relative_path=CLEAN_PATH)
        r = self._retriever([clean])
        r._drop_sensitive_chunks()
        assert r._chunks == [clean]


# ── ASR 输出 ───────────────────────────────────────────────


class TestAsrFormatterGuard:
    def test_hotwords_file_excludes_sensitive(self, tmp_path):
        from iris.wiki.asr.formatter import format_hotwords_file

        out = format_hotwords_file(
            ["AI", "调薪方案", "万涛", "绩效A级", "搜推"],
            tmp_path / "hotwords.txt",
        )
        written = Path(out).read_text(encoding="utf-8").splitlines()
        assert written == ["AI", "万涛", "搜推"]

    def test_replace_dict_excludes_sensitive_term_and_its_misreadings(self, tmp_path):
        import json

        from iris.wiki.asr._types import AsrTerm
        from iris.wiki.asr.formatter import format_replace_dict

        terms = [
            AsrTerm(term="调薪方案", category="domain_term", context="",
                    mis_asr=["调新方案", "调薪放案"]),
            AsrTerm(term="搜推", category="domain_term", context="", mis_asr=["搜推"]),
        ]
        out = format_replace_dict(terms, str(tmp_path / "dict.json"))
        replace_map = json.loads(Path(out).read_text(encoding="utf-8"))["replace_map"]

        # term 与其携带的全部 mis 都不应出现（key 与 value 双向）
        assert "调薪方案" not in replace_map
        assert "调新方案" not in replace_map
        assert "调薪放案" not in replace_map
        assert replace_map == {}
