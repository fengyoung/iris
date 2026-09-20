"""真实 Wiki 文件的生成、备份、拒绝无效更新与批量隔离。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iris.wiki.generator import WikiGenerator, BatchWikiItem
from iris.llm import LLMProviderError


def page(title="质量", body="事实内容"):
    return f"---\ntitle: {title}\ntype: project\nupdated: 2026-09-01\n---\n## 摘要\n{body}\n"


@pytest.fixture
def gen(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    g = object.__new__(WikiGenerator)
    g._config = SimpleNamespace(root=tmp_path, app={"safety": {"allowed_write_paths": [str(root)]}},
                               wiki={"wiki_root": str(root)}, data_source={"sources": {}})
    g._wiki_root = root
    g._wiki_searcher = None
    g._logger = Mock()
    g._llm = Mock()
    g._llm.generate.return_value = SimpleNamespace(text=page())
    g._retriever = Mock()
    g._retriever.search.return_value = SimpleNamespace(hits=[])
    g._retriever.latest_documents.return_value = []
    return g


@pytest.mark.parametrize("kind", ["project", "person", "domain", "concept"])
def test_build_and_write_actual_file(gen, kind):
    draft = gen.build_page(query="质量", title="质量", page_type=kind)
    written = gen.write_page(draft)
    from pathlib import Path
    assert Path(written.path).read_text() == page().strip()
    assert written.action == "created"
    assert gen.write_page(draft).action == "skipped_exists"
    assert gen._llm.generate.call_count == 1


def test_update_persists_and_backs_up_original(gen):
    draft = gen.build_page(query="质量", title="质量", page_type="project")
    gen.write_page(draft)
    gen._llm.generate.return_value = SimpleNamespace(text=page(body="新的事实内容"))
    result = gen.update_page(title="质量")
    from pathlib import Path
    assert result["status"] == "updated"
    assert Path(result["backup_path"]).read_text() == page().strip()
    assert "新的事实内容" in Path(result["path"]).read_text()


@pytest.mark.parametrize("failed", [False, True])
def test_invalid_update_does_not_advance_fingerprint(gen, failed):
    draft = gen.build_page(query="质量", title="质量", page_type="project")
    gen.write_page(draft)
    gen._retriever.search.return_value = SimpleNamespace(hits=[SimpleNamespace(
        relative_path="new.md", title="新材料", content_preview="新的事实")])
    metadata = gen._config.root / "data" / "metadata"
    metadata.mkdir(parents=True)
    (metadata / "chunk_hash_index.json").write_text(json.dumps({"new.md": {"hash": "newhash"}}))
    gen._llm.generate.side_effect = LLMProviderError("断网") if failed else None
    gen._llm.generate.return_value = SimpleNamespace(text="抱歉无法更新")
    result = gen.update_page(title="质量")
    from pathlib import Path
    assert Path(draft.output_path).read_text() == page().strip()
    assert result["status"] != "updated"


def test_batch_progress_and_no_write(gen):
    callback = Mock()
    result = gen.build_pages([BatchWikiItem("质量", "质量", "project")], progress_callback=callback)
    assert len(result.items) == 1
    assert not list(gen._wiki_root.rglob("*.md"))
    callback.assert_called_once_with(1, 1)


@pytest.mark.parametrize("use_async", [False, True])
def test_bulk_continues_after_one_page_provider_failure(gen, use_async):
    for title in ["质量", "速度"]:
        gen._llm.generate.return_value = SimpleNamespace(text=page(title))
        gen.write_page(gen.build_page(query=title, title=title, page_type="project"))
    gen._llm.generate.side_effect = LLMProviderError("离线")
    if use_async:
        import asyncio
        result = asyncio.run(gen.update_all_pages_async(max_concurrency=2))
    else:
        result = gen.update_all_pages()
    assert result["total"] == result["errors"] == 2
    assert {r["title"] for r in result["details"]} == {"质量", "速度"}
