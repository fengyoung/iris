"""评估判词与页面级兜底回归，禁止把失败或歧义输出当成正确事实。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iris.evaluation.deep_eval import AccuracyVerifier, ComprehensivenessVerifier, ReferenceEntry


@pytest.mark.parametrize("parallel", [1, 3])
@pytest.mark.parametrize("answer,expected", [("inconsistent", "inconsistent"),
    ("consistent", "consistent"), ("not consistent", "unverifiable"),
    ("inconsistent|数字错误", "inconsistent"), ("未知", "unverifiable")])
def test_verdict_exact_matching(parallel, answer, expected):
    llm, locator = Mock(), Mock()
    llm.generate.return_value = SimpleNamespace(text=answer)
    locator.lookup_with_context.return_value = "准确率为80%"
    locator.lookup_relevant.return_value = None
    entries = [ReferenceEntry(raw="引用", line_number=None, source_path=f"{i}.md", description="准确率90%") for i in range(2)]
    results = AccuracyVerifier(llm, locator).verify(entries, max_workers=parallel)
    assert [r.reference for r in results] == entries
    assert [r.verdict for r in results] == [expected, expected]


@pytest.mark.parametrize("workers", [1, 2])
def test_empty_description_uses_page_evidence(workers):
    llm, locator = Mock(), Mock()
    llm.generate.return_value = SimpleNamespace(text="inconsistent|Wiki 数字错误")
    locator.lookup_with_context.return_value = "准确率80%"
    entries = [ReferenceEntry(raw="引用", line_number=None, source_path=f"{i}.md", description="") for i in range(2)]
    results = AccuracyVerifier(llm, locator).verify(entries, wiki_title="质量",
        wiki_content="准确率90%，已发布。" * 30, max_workers=workers)
    assert all(r.verdict == "inconsistent" for r in results)
    assert llm.generate.call_count == 2


def test_gaps_exclude_every_existing_reference():
    llm, locator = Mock(), Mock()
    locator.search_sources_by_keywords.return_value = ["second.md", "new.md"]
    locator.lookup.return_value = "新的里程碑"
    llm.generate.return_value = SimpleNamespace(text="has_gap|缺少里程碑")
    gaps = ComprehensivenessVerifier(llm, locator).find_gaps("项目", "正文", ["first.md", "second.md"])
    assert [g.source_path for g in gaps] == ["new.md"]
    assert llm.generate.call_count == 1


def test_full_evaluation_aggregates_evidence_and_recommendations(tmp_path):
    import json
    from iris.evaluation.deep_eval import DeepEvaluator, SourceLocator, deep_eval_result_to_json
    root = tmp_path / "03-项目"
    root.mkdir()
    (root / "质量.md").write_text('---\ntitle: 质量\n---\n## 摘要\n质量复盘\n## 参考来源\n'
        '1. [a.md:1] 准确率90%\n2. [b.md:1] 延迟10毫秒\n3. [missing.md:1] 无来源事实\n')
    (root / "无引用.md").write_text('---\ntitle: 无引用\n---\n正文')
    index = tmp_path / "chunks.json"
    index.write_text(json.dumps({"chunks": [
        {"relative_path": "a.md", "line_start": 1, "line_end": 3, "content": "准确率80%"},
        {"relative_path": "b.md", "line_start": 1, "line_end": 3, "content": "延迟10毫秒"}]}))
    llm = Mock()
    llm.generate.side_effect = lambda prompt, **kw: SimpleNamespace(
        text="inconsistent|数字错误" if "准确率90%" in prompt else "consistent|正确")
    evaluator = object.__new__(DeepEvaluator)
    evaluator._wiki_root = tmp_path
    evaluator._locator = SourceLocator([str(index)])
    evaluator._acc_verifier = AccuracyVerifier(llm, evaluator._locator)
    evaluator._comp_verifier = ComprehensivenessVerifier(llm, evaluator._locator)
    result = evaluator.evaluate()
    assert result.total_pages == 2
    assert result.consistent_count == result.inconsistent_count == result.source_missing_count == 1
    assert result.overall_accuracy_rate == 0.5
    assert result.recommendations
    payload = deep_eval_result_to_json(result)
    assert payload["accuracy"]["total_references"] == 3
    assert evaluator.evaluate(page_filter="不存在").total_pages == 0


@pytest.mark.parametrize("answer", ["no_gap|没有遗漏", "not has_gap", "未知"])
def test_gap_parser_rejects_ambiguous_or_negative_answers(answer):
    llm, locator = Mock(), Mock()
    locator.search_sources_by_keywords.return_value = ["new.md"]
    locator.lookup.return_value = "新增资料"
    llm.generate.return_value = SimpleNamespace(text=answer)
    assert ComprehensivenessVerifier(llm, locator).find_gaps("主题", "内容", []) == []


@pytest.mark.parametrize("page_level", [False, True])
def test_model_failure_cannot_be_reported_as_consistent(page_level):
    from iris.llm import LLMProviderError
    llm, locator = Mock(), Mock()
    locator.lookup_with_context.return_value = "事实来源"
    locator.lookup_relevant.return_value = None
    llm.generate.side_effect = LLMProviderError("离线")
    entry = ReferenceEntry("引用", "a.md", None, "" if page_level else "事实描述")
    result = AccuracyVerifier(llm, locator).verify([entry], wiki_content="内容" * 150)
    assert result[0].verdict == "unverifiable"


def test_missing_source_skips_model_entirely():
    llm, locator = Mock(), Mock()
    locator.lookup_with_context.return_value = None
    result = AccuracyVerifier(llm, locator).verify([ReferenceEntry("引用", "absent.md", None, "描述")])
    assert result[0].verdict == "source_missing"
    llm.generate.assert_not_called()
