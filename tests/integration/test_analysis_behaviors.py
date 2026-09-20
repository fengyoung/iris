"""报告编排回归：外部模型替身配合真实缓存，校验产物与调用边界。"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iris.analysis.service import AnalysisReportService
from iris.analysis._biweekly_cache import BiweeklyCache
from iris.llm import LLMProviderError


@pytest.fixture
def svc(tmp_path):
    service = object.__new__(AnalysisReportService)
    service._config = SimpleNamespace(app={"biweekly_report": {"report_author": "测试者"}})
    service._cache = BiweeklyCache(tmp_path / "cache")
    service._llm = Mock()
    service._logger = Mock()
    service._prompt_loader = Mock()
    service._prompt_loader.render.side_effect = lambda name, ctx: str(ctx)
    service._collector = Mock()
    service._collector.load_op_document.return_value = "年度目标"
    service._collector.collect_recent_files.return_value = [file_record()]
    service._qa = Mock()
    service._qa.ask.return_value = SimpleNamespace(answer="已完成上线", blocks=[], structured={})
    return service


def file_record(content="工作进展", label="周报"):
    return {"content": content, "label": label, "date": datetime(2026, 9, 20),
            "dir": "成员周报", "filename": label + ".md", "char_count": len(content)}


@pytest.mark.parametrize("mode,fail,expected", [
    ("local", False, "local"), ("llm", False, "llm"), ("llm", True, "local_fallback")])
def test_report_generation_and_fallback(svc, mode, fail, expected):
    svc._llm.generate.side_effect = LLMProviderError("断网") if fail else None
    svc._llm.generate.return_value = SimpleNamespace(text="模型报告")
    result = svc.build_report("进展", mode=mode)
    assert result.mode == expected
    assert result.markdown
    assert svc._llm.generate.call_count == (mode == "llm")
    assert result.llm["fallback_used"] == fail


@pytest.mark.parametrize("review,revised", [('{"quality_score": 5}', False),
    ('{"quality_score": 2,"issues":["缺引用"],"suggestions":["补引用"]}', True),
    ('不是 JSON', False), ('{"issues": []}', False)])
def test_report_review_controls_revision(svc, review, revised):
    svc._llm.generate.side_effect = [SimpleNamespace(text="初稿"),
                                    SimpleNamespace(text=review), SimpleNamespace(text="修订稿")]
    result = svc.build_report("进展", two_stage=True)
    assert result.revised is revised
    assert result.markdown == ("修订稿" if revised else "初稿")


def test_dry_run_never_calls_model_on_cache_miss(svc):
    svc._llm.generate.return_value = SimpleNamespace(text='{"directions": []}')
    result = svc.build_biweekly_report(dry_run=True, as_of="20260920")
    assert result.mode == "dry_run"
    assert "周报" in result.markdown
    svc._llm.generate.assert_not_called()


def test_dry_run_uses_existing_directions(svc):
    directions = [{"id": 1, "name": "质量"}]
    svc._cache.save_op_directions(svc._cache.content_hash("年度目标", 4), directions)
    result = svc.build_biweekly_report(dry_run=True)
    assert result.structured["op_directions"] == directions
    svc._llm.generate.assert_not_called()


@pytest.mark.parametrize("empty,mode", [(True, "llm"), (False, "local")])
def test_biweekly_local_and_empty_skip_llm(svc, empty, mode):
    if empty:
        svc._collector.collect_recent_files.return_value = []
    result = svc.build_biweekly_report(mode=mode, as_of="20260920")
    assert "2026.09.06～2026.09.20" in result.markdown
    svc._llm.generate.assert_not_called()


def test_biweekly_provider_failure_preserves_source_manifest(svc):
    svc._llm.generate.side_effect = LLMProviderError("断网")
    result = svc.build_biweekly_report(as_of="20260920")
    assert result.mode == "local_fallback"
    assert "年度目标" in result.markdown and "周报" in result.markdown


def test_op_cache_reuses_but_invalidates_full_document(svc):
    svc._llm.generate.return_value = SimpleNamespace(text='{"directions":[{"id":1,"name":"质量"}]}')
    svc._stage0a_parse_op("x" * 2100 + "旧")
    svc._stage0a_parse_op("x" * 2100 + "旧")
    assert svc._llm.generate.call_count == 1
    svc._stage0a_parse_op("x" * 2100 + "新")
    assert svc._llm.generate.call_count == 2


@pytest.mark.parametrize("change", ["tail", "assignment", "definition"])
def test_brief_cache_invalidates_all_semantic_inputs(svc, change):
    directions = [{"id": 1, "name": "质量", "scope_summary": "旧目标"}, {"id": 2, "name": "速度"}]
    files = [file_record("x" * 2100 + "旧结论")]
    mapping = {"质量": {"high": [{"label": "周报"}]}}
    svc._llm.generate.side_effect = lambda **kw: SimpleNamespace(text='{"brief_md":"摘要","relevant_directions":[]}')
    svc._stage2_summarize_files(directions, mapping, files)
    svc._stage2_summarize_files(directions, mapping, files)
    assert svc._llm.generate.call_count == 1
    if change == "tail":
        files[0]["content"] = "x" * 2100 + "新结论"
    elif change == "assignment":
        mapping = {"速度": {"high": [{"label": "周报"}]}}
    else:
        directions[0]["scope_summary"] = "新目标"
    result = svc._stage2_summarize_files(directions, mapping, files)
    assert svc._llm.generate.call_count == 2
    assert result["周报"]["relevant_directions"] == ([2] if change == "assignment" else [1])


def test_stage1_cache_tracks_owner_and_scope(svc):
    directions = [{"id": 1, "name": "质量", "scope_summary": "旧目标"}]
    svc._llm.generate.return_value = SimpleNamespace(text='{"high":[{"label":"周报"}]}')
    svc._stage1_filter_files(directions, [file_record()])
    svc._stage1_filter_files(directions, [file_record()])
    assert svc._llm.generate.call_count == 1
    directions[0]["scope_summary"] = "新目标"
    svc._stage1_filter_files(directions, [file_record()])
    assert svc._llm.generate.call_count == 2


@pytest.mark.parametrize("review_failure", [False, True])
def test_full_biweekly_pipeline_keeps_facts_and_author(svc, review_failure):
    svc._collector.load_recent_biweeklies.return_value = []
    def generate(*args, route_context, **kwargs):
        stage = route_context["use_case"]
        if stage.endswith("stage0a"):
            text = '{"directions":[{"id":1,"name":"质量","scope_summary":"降低错误","key_indicators":["准确率"]}]}'
        elif stage.endswith("stage1"):
            text = '{"high":[{"label":"周报"}]}'
        elif stage.endswith("stage2"):
            text = '{"brief_md":"准确率提升至95%","primary_direction":1,"relevant_directions":[1]}'
        elif stage.endswith("stage3"):
            assert "准确率提升至95%" in kwargs["prompt"]
            text = '## 质量\n\n### 关键进展\n\n准确率提升至95%'
        else:
            if review_failure:
                raise LLMProviderError("审查离线")
            text = '## 质量\n\n### 关键进展\n\n准确率提升至95%'
        return SimpleNamespace(text=text)
    svc._llm.generate.side_effect = generate
    result = svc.build_biweekly_report(as_of="20260920")
    assert result.mode == "llm"
    assert "95%" in result.markdown
    assert result.markdown.count("revised by 测试者") == 1
    assert result.llm["brief_count"] == result.llm["direction_count"] == 1
    assert svc._llm.generate.call_count == 5
